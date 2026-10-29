"""Build the Swedish statutes (SFS) git repository from the fetch cache.

    python -X utf8 build.py <repo dir> --today YYYY-MM-DD [--limit N] [--workers N]

Current-only corpus (the sources keep no past consolidations), with three kinds of real dated change:
- a statute's text appears on the in-force day of the latest amending act the consolidation includes (SFSR);
- paragrafs the publisher prints twice, as "/Upph\u00f6r att g\u00e4lla U:<date>/" and "/Tr\u00e4der i kraft I:<date>/", change
  on that date (on main if the date is not after --today, otherwise on the never-merged `future` branch),
  credited to the act named in the new text ("Lag (YYYY:NNN)");
- a repealed statute's files are deleted on its repeal day (Upph\u00e4vd), credited to the repealing act.
"""
import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone

import sfs

HERE = os.path.dirname(os.path.abspath(__file__))
COMMITTER = 'Lexifina <sweden-law@lexifina.com>'
NL = chr(10)
NL2 = NL + NL
PUBLISHER = ('Regeringskansliet SFST (via Riksdagen open data)', 'sfst@rkrattsbaser.invalid')


# ------------------------------------------------------------------------------------------------ dates

def _last_sunday(year, month):
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def tz_hours(d):
    """Sweden at noon on day d: CET (+1); summer time (+2) from 1980. 1980: 6 April - 28 September; 1981-1995: last
    Sunday of March - last Sunday of September; from 1996 the EU rule, last Sunday of March - last Sunday of
    October. start <= day < end (the change happens at 01:00 UTC, before noon)."""
    if d.year < 1980:
        return 1
    if d.year == 1980:
        start, end = date(1980, 4, 6), date(1980, 9, 28)
    elif d.year <= 1995:
        start, end = _last_sunday(d.year, 3), _last_sunday(d.year, 9)
    else:
        start, end = _last_sunday(d.year, 3), _last_sunday(d.year, 10)
    return 2 if start <= d < end else 1


def git_date(day, seq):
    d = date.fromisoformat(day)
    if d.year < 1970:
        return '%d +0100' % seq  # clamped: 1970-01-01 plus seq seconds; the real day is in In-force
    h = tz_hours(d)
    ts = int(datetime(d.year, d.month, d.day, 12 - h, tzinfo=timezone.utc).timestamp())
    return '%d +0%d00' % (ts, h)


def day_before(day):
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def valid_iso(s):
    try:
        date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------------------------------------ one statute

def act_name(num, sfsr_entries, fallback=None):
    for e in sfsr_entries:
        if sfs.sfs_key(e['sfs']) == sfs.sfs_key(num):
            r = e['fields'].get('Rubrik')
            if r:
                return r
    return fallback or 'SFS %s' % num


def act_email(num):
    k = sfs.sfs_key(num)
    if k is None or not sfs.re.fullmatch(r'\s*(?:SFS\s*)?\d{4}:\d+\s*', num):
        return 'sfs-%s@sfs.invalid' % sfs.slug(num).replace('/', '-')  # old numbers such as 1878:bih. 56 s. 1
    return 'sfs-%d-%d@sfs.invalid' % k


def unit_validity(u):
    """(I, U) of a unit from markers at the very start of its first text line (after the \u00a7 label):
    None = no marker, a date, or 'undated'. Returns also whether the unit carries any other marker."""
    lines = [l for l in u['lines'] if not l.startswith('#')]
    first = lines[0] if lines else ''
    head = re.sub(r'^\d+(?: ?[a-z])? \u00a7\s*', '', first)
    I = U = None
    pos = 0
    while True:
        m = sfs.MARK_RE.match(head, pos)
        if not m or m.group(1).strip():
            break
        when = m.group(3).strip()
        d = sfs.when_date(when)
        v = d if d and valid_iso(d) else 'undated'
        if m.group(2).lower().startswith('upph'):
            U = v
        else:
            I = v
        pos = m.end()
        while pos < len(head) and head[pos] == ' ':
            pos += 1
    lead = len(sfs.markers(head[:pos])) if pos else 0
    total = sum(len(sfs.markers(l)) for l in u['lines'])
    return I, U, total - lead


def in_force(I, U, day):
    if I == 'undated' or (I is not None and I > day):
        return False
    if U is not None and U != 'undated' and day >= U:
        return False
    return True


def _v(when):
    d = sfs.when_date(when)
    return d if d and valid_iso(d) else 'undated'


def combine(a, b):
    """Both validities must hold: the later start, the earlier end ('undated' start never comes)."""
    (I1, U1), (I2, U2) = a, b
    if 'undated' in (I1, I2):
        I = 'undated'
    else:
        I = max([x for x in (I1, I2) if x] or [None]) if (I1 or I2) else None
    ends = [x for x in (U1, U2) if x and x != 'undated']
    U = min(ends) if ends else (U1 or U2)
    return I, U


def assign(units):
    """Validity of every unit, and of single lines inside units, from the publisher's markers.

    - a marker right after the paragraf label: the whole paragraf;
    - a stycke that is only "/Rubriken tr\u00e4der i kraft I:<when>/" (or upph\u00f6r ... U:): itself and the next heading line;
      the same marker right after a paragraf label: the heading line(s) at the top of that paragraf;
    - "/Kapitlet upph\u00f6r att g\u00e4lla U:<when>": itself and every unit of the chapter it closes (back to the chapter's
      first unit); "/Kapitlet tr\u00e4der i kraft I:<when>/": itself and every unit of the next chapter.
    Returns (unit validity list, line validity lists, number of markers not understood)."""
    uval = []
    lval = [[(None, None)] * len(u['lines']) for u in units]
    stray = 0
    for u in units:
        I, U, _ = unit_validity(u)
        uval.append((I, U))
    for k, u in enumerate(units):
        lines = u['lines']
        first_text = next((i for i, l in enumerate(lines) if not l.startswith('#')), None)
        for i, line in enumerate(lines):
            ms = sfs.markers(line)
            if not ms:
                continue
            if i == first_text:
                head = re.sub(r'^\d+(?: ?[a-z])? \u00a7\s*', '', line)
                lead = sfs.MARK_RE.match(head)
                if lead and not lead.group(1).strip():
                    ms = ms[len([m for m in sfs.MARK_RE.finditer(head[:_lead_end(head)])]):]
                elif lead and lead.group(1).strip().lower() == 'rubriken' and head.startswith('/') and len(ms) == 1:
                    v = (_v(ms[0][2]), None) if ms[0][1] == 'I' else (None, _v(ms[0][2]))
                    heads = [j for j in range(i) if lines[j].startswith('#')]
                    if heads and u['kind'] == 'par':
                        lval[k][heads[-1]] = combine(lval[k][heads[-1]], v)
                        continue
            for scope, kind, when in ms:
                v = (_v(when), None) if kind == 'I' else (None, _v(when))
                sc = scope.lower()
                whole = line.startswith('/') and len(ms) == 1
                if sc == 'rubriken' and whole:
                    lval[k][i] = combine(lval[k][i], v)
                    target = None
                    for j in range(i + 1, len(lines)):
                        if lines[j].startswith('#'):
                            target = (k, j)
                            break
                    if target is None:
                        for kk in range(k + 1, len(units)):
                            hs = [j for j, l in enumerate(units[kk]['lines']) if l.startswith('#')]
                            if hs:
                                target = (kk, hs[0])
                            break
                    if target is None:
                        stray += 1
                    else:
                        lval[target[0]][target[1]] = combine(lval[target[0]][target[1]], v)
                elif sc == 'kapitlet' and whole and kind == 'U' and u['chapter']:
                    lval[k][i] = combine(lval[k][i], v)
                    kk = k
                    while kk >= 0 and units[kk]['chapter'] == u['chapter'] and units[kk]['kind'] == 'par':
                        uval[kk] = combine(uval[kk], v)
                        lval[kk] = [combine(x, v) for x in lval[kk]]
                        kk -= 1
                elif sc == 'kapitlet' and whole and kind == 'I' and k + 1 < len(units) and units[k + 1]['chapter']:
                    lval[k][i] = combine(lval[k][i], v)
                    ch = units[k + 1]['chapter']
                    kk = k + 1
                    while kk < len(units) and units[kk]['chapter'] == ch and units[kk]['kind'] == 'par':
                        uval[kk] = combine(uval[kk], v)
                        lval[kk] = [combine(x, v) for x in lval[kk]]
                        kk += 1
                else:
                    stray += 1
    # renumbering markers ("/Ny beteckning 4 a \u00a7 U:<when>/") are not modelled: the statute is kept as printed
    stray += sum(len(re.findall(r'/\s*Ny beteckning', l)) for u in units for l in u['lines'])
    return uval, lval, stray


def _lead_end(head):
    pos = 0
    while True:
        m = sfs.MARK_RE.match(head, pos)
        if not m or m.group(1).strip():
            return pos
        pos = m.end()
        while pos < len(head) and head[pos] == ' ':
            pos += 1


def render_state(units, day, validity, lvalidity=None, clashes=None):
    """Files in force on day. Headings at the top of a paragraf that is not in force are not dropped with it (the
    publisher marks new or ending headings with their own "/Rubriken ..." markers): they go to the next paragraf
    shown, or the end of the last one."""
    files = {}
    order = []
    carry = []
    for k, (u, (I, U)) in enumerate(zip(units, validity)):
        lines = u['lines'] if lvalidity is None else [l for l, (a, b) in zip(u['lines'], lvalidity[k]) if in_force(a, b, day)]
        if not in_force(I, U, day):
            for l in lines:
                if not l.startswith('#'):
                    break
                carry.append(l)
            continue
        lines = carry + lines
        carry = []
        if not lines:
            continue
        p = sfs.unit_path(u)
        n = 1
        while p in files:  # two texts under one number in force together (as printed): keep both
            n += 1
            p = sfs.unit_path(u)[:-3] + '~%d.md' % n
        if n > 1 and clashes is not None:
            clashes.append(sfs.unit_path(u))
        files[p] = NL2.join(lines) + NL
        order.append(p)
    if carry and order:
        files[order[-1]] = files[order[-1]] + NL + NL2.join(carry) + NL
    return (files, order), None


def as_printed(units):
    files, order = {}, []
    dup = Counter()
    for u in units:
        p = sfs.unit_path(u)
        if p in files:
            dup[p] += 1
            p = p[:-3] + '~%d.md' % (dup[p] + 1)
        files[p] = '\n\n'.join(u['lines']) + '\n'
        order.append(p)
    return files, order


def last_note(text):
    notes = sfs.NOTE_RE.findall(text)
    return notes[-1] if notes else None


def build_law(doc_meta, today):
    """Everything about one statute: metadata, files per version, events. Pure function of the cache."""
    dok, bet = doc_meta['dok_id'], doc_meta['beteckning']
    out = {'dok_id': dok, 'bet': bet, 'dir': sfs.slug(bet), 'issues': [], 'events': [], 'amend': [], 'undated': []}
    d = sfs.read_doc(dok)
    if d is None:
        out['issues'].append([bet, 'no document in the cache'])
        return out
    reg = sfs.read_sfsr(bet)
    entries = reg['entries'] if reg else []
    fields, body = sfs.split_header(d['html'])
    up = d['uppgift']
    title = sfs.clean_line(d['titel'] or doc_meta['titel'])
    out.update(title=title, issued=(up.get('utfardad') or fields.get('Utf\u00e4rdad') or '')[:10],
               amended_through=(re.sub(r'^t\.o\.m\.\s*(?:SFS\s*)?', '', up.get('andrattillochmed') or fields.get('\u00c4ndrad') or '')),
               repealed=(up.get('upphavd') or fields.get('Upph\u00e4vd') or '')[:10],
               repealed_by=re.sub(r'^SFS\s*', '', up.get('upphnr') or fields.get('F\u00f6rfattningen har upph\u00e4vts genom') or ''),
               department=d['organ'], other=up.get('text2') or fields.get('\u00d6vrig text') or '',
               sfsr=bool(reg), base_in_force='', class_='plain')
    if reg:
        bi = sfs.DATE_RE.findall(reg['fields'].get('Ikraft', ''))
        out['base_in_force'] = bi[0] if bi and valid_iso(bi[0]) else ''
        for e in entries:
            f = e['fields']
            out['amend'].append([bet, e['sfs'], f.get('Rubrik', ''), f.get('Omfattning', ''), f.get('Ikraft', ''),
                                 f.get('F\u00f6rarbeten', '')])
    units = sfs.parse_body(body)
    if not any(l for u in units for l in u['lines'] if not l.startswith('#')):
        out['issues'].append([bet, 'no text in the source document'])
        return out
    out['units'] = len(units)

    # law-level markers: a marker as the whole first text line, before any provision
    law_I = law_U = None
    first = next((l for u in units for l in u['lines'] if not l.startswith('#')), '')
    fm = sfs.MARK_RE.fullmatch(first.strip())
    if fm and units[0]['kind'] == 'ingress':
        dd = sfs.when_date(fm.group(3))
        v = dd if dd and valid_iso(dd) else 'undated'
        if fm.group(2).lower().startswith('upph'):
            law_U = v
        else:
            law_I = v

    validity, lvalidity, stray = assign(units)
    if law_I is not None or law_U is not None:
        stray -= 1  # the law-level marker itself
    has_markers = (any(v != (None, None) for v in validity) or stray > 0
                   or any(x != (None, None) for lv in lvalidity for x in lv))
    tom = sfs.sfs_key(out['amended_through'])
    repealed = out['repealed'] if valid_iso(out['repealed']) else ''

    # dated change days from paragraf markers
    dated = sorted({x for v in validity + [x for lv in lvalidity for x in lv] for x in v if x not in (None, 'undated')})
    cls = 'plain'
    if has_markers:
        cls = 'dated-versions'
        if stray > 0:
            cls = 'as-printed'
        else:
            probe_days = sorted(set([today] + dated + [day_before(x) for x in dated]))
            seen = set()
            for day in probe_days:
                cl = []
                render_state(units, day, validity, lvalidity, cl)
                for c in cl:
                    if c not in seen:
                        seen.add(c)
                        out['issues'].append([bet, 'two texts of %s in force together (from %s); the second kept as %s'
                                              % (c, day, c[:-3] + '~2.md')])
    out['class_'] = cls
    for u, (I, U) in zip(units, validity):
        if 'undated' in (I, U):
            out['undated'].append([bet, sfs.unit_path(u), 'I' if I == 'undated' else 'U',
                                   (sfs.markers(u['lines'][0] if u['lines'] else '') or [('', '', '')])[0][2]])

    def amend_dates(cutoff, exclude=None):
        """(day, act) of the latest in-force day not after cutoff among the base law and its amendments in the text."""
        best = (out['base_in_force'], None) if out['base_in_force'] and out['base_in_force'] <= cutoff else ('', None)
        for e in entries:
            k = sfs.sfs_key(e['sfs'])
            if not k or (tom and k > tom) or (exclude and k == exclude):
                continue
            for x in sfs.DATE_RE.findall(e['fields'].get('Ikraft', '')):
                if valid_iso(x) and x <= cutoff and x > best[0]:
                    best = (x, e['sfs'])
        return best

    # the end of the record on main: repeal day or --today
    if law_U not in (None, 'undated') and (not repealed or law_U < repealed):
        # the whole statute ceases to apply on a printed day ("/Upph\u00f6r att g\u00e4lla U:<day> genom lag (X)/")
        repealed = law_U
        g = re.search(r'(\d{4}:\d+)', fm.group(3)) if fm else None
        if g:
            out['repealed_by'] = g.group(1)
        out['repealed'] = law_U
    end = repealed if repealed and repealed <= today else None
    if cls == 'dated-versions':
        past = [x for x in dated if x <= today and (not end or x < end)]
        future = [x for x in dated if x > today and not end and (not repealed or x < repealed)]
    else:
        past, future = [], []
    if law_I not in (None, 'undated') and law_I > today:
        # the whole statute takes effect later: future branch only
        st, _ = render_state(units, law_I, validity, lvalidity) if cls == 'dated-versions' else ((as_printed(units)), None)
        files, order = st if cls == 'dated-versions' else st
        out['events'].append({'branch': 'future', 'day': law_I, 'kind': 'enacted', 'act': bet, 'files': files,
                              'order': order, 'date_source': 'marker', 'law': out['dir']})
        out['status'] = 'future'
        return out
    if law_I == 'undated':
        out['issues'].append([bet, 'the whole statute takes effect on a day not yet set; left out'])
        out['status'] = 'undated'
        return out
    cutoff = day_before(past[0]) if past else (day_before(end) if end else today)
    base_day, base_act = amend_dates(cutoff, exclude=sfs.sfs_key(out['repealed_by']))
    date_source = 'sfsr-amendment' if base_act else ('sfsr-in-force' if base_day else '')
    if not base_act and out['base_in_force'] and out['base_in_force'] > cutoff:
        if out['base_in_force'] > today and not end:
            files, order = (render_state(units, out['base_in_force'], validity, lvalidity)[0] if cls == 'dated-versions'
                            else as_printed(units))
            out['events'].append({'branch': 'future', 'day': out['base_in_force'], 'kind': 'enacted', 'act': bet,
                                  'files': files, 'order': order, 'date_source': 'sfsr-in-force', 'law': out['dir']})
            out['status'] = 'future'
            return out
        out['issues'].append([bet, 'in force %s (SFSR), after the end of its record %s; left out'
                              % (out['base_in_force'], cutoff)])
        out['status'] = 'never in force'
        return out
    if not base_day:
        base_day = out['issued'] if valid_iso(out['issued']) and out['issued'] <= cutoff else ''
        date_source = 'issued' if base_day else ''
    if law_I not in (None, 'undated') and law_I > base_day and law_I <= cutoff:
        base_day, date_source, base_act = law_I, 'marker', None
    if not base_day:
        out['issues'].append([bet, 'no in-force or issue day on or before %s; left out' % cutoff])
        out['status'] = 'no-date'
        return out
    amended = any(sfs.sfs_key(e['sfs']) and (not tom or sfs.sfs_key(e['sfs']) <= tom) and
                  any(valid_iso(x) and x <= cutoff for x in sfs.DATE_RE.findall(e['fields'].get('Ikraft', '')))
                  and sfs.sfs_key(e['sfs']) != sfs.sfs_key(out['repealed_by']) for e in entries)
    if cls == 'dated-versions':
        (files, order), _ = render_state(units, cutoff, validity, lvalidity)
    else:
        files, order = as_printed(units)
    out['base_day'], out['date_source'] = base_day, date_source
    out['events'].append({'branch': 'main', 'day': base_day, 'kind': 'consolidated' if amended else 'enacted',
                          'act': base_act if amended else bet, 'files': files, 'order': order,
                          'date_source': date_source, 'law': out['dir']})
    prev = files
    for day in past + future:
        (nf, norder), _ = render_state(units, day, validity, lvalidity)
        changes = {}
        for p in sorted(set(prev) | set(nf)):
            if prev.get(p) != nf.get(p):
                changes[p] = nf.get(p)
        if not changes:
            prev = nf
            continue
        by_act = defaultdict(dict)
        for p, txt in changes.items():
            act = None
            for u, (I, U) in zip(units, validity):
                if sfs.unit_path(u) != p:
                    continue
                if I == day and txt is not None:
                    act = last_note('\n'.join(u['lines']))
                elif U == day and act is None:
                    ms = sfs.markers(u['lines'][0] if u['lines'] else '')
                    g = re.search(r'(\d{4}:\d+)', ms[0][2]) if ms else None
                    act = g.group(1) if g else act
            if act is None:
                cands = sorted({e['sfs'] for e in entries if day in sfs.DATE_RE.findall(e['fields'].get('Ikraft', ''))})
                act = cands[0] if len(cands) == 1 else ('?' + '|'.join(cands))
            by_act[act][p] = txt
        for act, ch in sorted(by_act.items()):
            ev = {'branch': 'main' if day <= today else 'future', 'day': day, 'kind': 'amendment', 'act': act,
                  'changes': ch, 'order': norder, 'law': out['dir'], 'date_source': 'marker'}
            if act.startswith('?'):
                ev['kind'] = 'unexplained'
                ev['candidates'] = [c for c in act[1:].split('|') if c]
            out['events'].append(ev)
        prev = nf
    if repealed:
        rb = out['repealed_by'] if sfs.sfs_key(out['repealed_by']) else None
        out['events'].append({'branch': 'main' if repealed <= today else 'future', 'day': repealed, 'kind': 'repeal',
                              'act': rb, 'law': out['dir'], 'date_source': 'upphavd'})
        out['status'] = 'repealed' if repealed <= today else 'in force (repeal set)'
    else:
        out['status'] = 'in force'
    out['entries_names'] = {sfs.sfs_str(sfs.sfs_key(e['sfs'])): e['fields'].get('Rubrik', '') for e in entries
                            if sfs.sfs_key(e['sfs'])}
    return out


def build_law_safe(args):
    meta, today = args
    try:
        return build_law(meta, today)
    except Exception as e:  # noqa: BLE001 - one bad document must not stop the corpus; it is listed
        return {'dok_id': meta['dok_id'], 'bet': meta['beteckning'], 'dir': sfs.slug(meta['beteckning']),
                'issues': [[meta['beteckning'], 'parse error: %s: %s' % (type(e).__name__, e)]], 'events': [],
                'amend': [], 'undated': [], 'status': 'error'}


# ------------------------------------------------------------------------------------------------ commits

def short(s, n=72):
    return s if len(s) <= n else s[:n - 3].rstrip() + '...'


def law_label(law):
    return 'SFS %s' % law['bet']


def commit_text(g, laws_by_dir, today):
    """(author name, email, message) for a group of events (same day, same act, same kind)."""
    ev = g['events']
    kind, day, act = g['kind'], g['day'], g['act']
    laws = [laws_by_dir[e['law']] for e in ev]
    tr = ['Kind: %s' % kind, 'In-force: %s' % day]
    if kind in ('consolidated', 'enacted'):
        law = laws[0]
        if kind == 'enacted':
            name, email = law['title'], act_email(law['bet'])
            subject = short('%s: %s' % (law_label(law), law['title']))
            body = ('%s as consolidated in SFST (Regeringskansliet), published through Riksdagen open data, '
                    'retrieved %s.\n\nThe register (SFSR) lists no amending act in force by %s, so the text is credited '
                    'to the statute itself. The source keeps no earlier consolidated versions.' %
                    (law['title'], g['retrieved'], day))
        else:
            name, email = PUBLISHER
            last = ev[0]['act']
            subject = short('%s: %s' % (law_label(law), law['title']))
            body = ('%s as consolidated in SFST (Regeringskansliet), "%s", published through Riksdagen open data '
                    'and retrieved %s.\n\nThe source keeps only this consolidation, so the whole text is one commit '
                    'credited to the publisher, dated by the in-force day of the latest amending act it includes '
                    '(SFS %s, from the SFSR register). Each paragraf still ends with the publisher\'s note naming the '
                    'act that last gave it its wording, e.g. "Lag (1994:458)." The register is in data/amendments.csv.'
                    % (law['title'], 't.o.m. SFS ' + law['amended_through'] if law['amended_through'] else 'as published',
                       g['retrieved'], last))
            tr.append('Consolidated: %s' % day)
            if law['amended_through']:
                tr.append('Amended-through: SFS %s' % law['amended_through'])
            tr.append('Last-amended-by: SFS %s' % last)
        tr.append('Date-source: %s' % ev[0]['date_source'])
        if law['class_'] == 'as-printed':
            tr.append('Text: as-printed')
            body += ('\n\nThe consolidation prints some provisions in two versions with markers ("/Upph\u00f6r att g\u00e4lla '
                     'U:.../", "/Tr\u00e4der i kraft I:.../") that could not be split cleanly by date; both are kept as '
                     'printed, the second copy in a file named ~2.')
        tr += ['Law: SFS %s' % law['bet'], 'Issued: %s' % law['issued']] if valid_iso(law['issued']) else ['Law: SFS %s' % law['bet']]
        tr.append('Source: https://data.riksdagen.se/dokument/%s.html' % law['dok_id'].replace(' ', '%20'))
    elif kind == 'amendment':
        name = act_name(act, [], None)
        for law in laws:
            n = law.get('entries_names', {}).get(sfs.sfs_str(sfs.sfs_key(act)) if sfs.sfs_key(act) else '', '')
            if n:
                name = n
                break
        email = act_email(act)
        paths = sorted(p for e in ev for p in ('%s/%s' % (e['law'], q) for q in e['changes']))
        subject = short('SFS %s in force: %s' % (act, ', '.join(law_label(l) for l in laws)))
        body = ('%s takes effect on %s. The consolidation prints the provisions it changes twice, the old text marked '
                '"/Upph\u00f6r att g\u00e4lla U:%s/" and the new text "/Tr\u00e4der i kraft I:%s/" ending "Lag (%s)."; this commit '
                'replaces one with the other.' % (name, day, day, day, act))
        tr.append('Amending-act: SFS %s' % act)
        tr += ['Affecting: %s' % p for p in paths]
    elif kind == 'unexplained':
        name, email = PUBLISHER
        paths = sorted(p for e in ev for p in ('%s/%s' % (e['law'], q) for q in e['changes']))
        subject = short('Dated change on %s not credited: %s' % (day, ', '.join(law_label(l) for l in laws)))
        body = ('The consolidation marks these provisions to change on %s, but no single act can be named from the text '
                'or the register, so the change is credited to the publisher.' % day)
        cands = sorted({c for e in ev for c in e.get('candidates', [])})
        tr += ['Candidate: SFS %s' % c for c in cands]
        tr += ['Affecting: %s' % p for p in paths]
    elif kind == 'repeal':
        if act:
            name = act
            for law in laws:
                n = law.get('entries_names', {}).get(sfs.sfs_str(sfs.sfs_key(act)), '')
                if n:
                    name = n
                    break
            if name == act:
                name = 'SFS %s' % act
            email = act_email(act)
            tr.append('Repealed-by: SFS %s' % act)
        else:
            name, email = PUBLISHER
        subject = short('%s repealed: %s' % (('SFS %s' % act) if act else 'Repeal', ', '.join(law_label(l) for l in laws)))
        body = ('%s ceases to apply on %s (Upph\u00e4vd, per Riksdagen open data and the SFSR register)%s. The files are '
                'removed; the last text stays in history.' % ('; '.join('%s (SFS %s)' % (l['title'], l['bet']) for l in laws),
                                                                day, (', repealed by %s' % name) if act else
                                                                '; the source names no repealing act'))
        tr += ['Repeals: SFS %s' % l['bet'] for l in laws]
    else:
        raise ValueError(kind)
    msg = subject + '\n\n' + body + '\n\n' + '\n'.join(tr) + '\n'
    msg = msg.replace('\r', '')
    return ' '.join(name.replace('<', '').replace('>', '').split()), email, msg


KIND_ORDER = {'enacted': 0, 'consolidated': 0, 'amendment': 1, 'unexplained': 2, 'repeal': 3}


def plan(events, laws_by_dir):
    """Group events into commits: base texts one per statute; amendments and repeals one per act per day."""
    groups = {}
    for e in events:
        if e['kind'] in ('enacted', 'consolidated'):
            key = (e['day'], 0, laws_by_dir[e['law']]['sort'], e['kind'], e['law'])
        elif e['kind'] == 'unexplained':
            key = (e['day'], 2, (0, 0), 'unexplained', '')
        else:
            k = sfs.sfs_key(e['act'] or '') or (9999, 0)
            key = (e['day'], KIND_ORDER[e['kind']], k, e['kind'], e['act'] or '')
        g = groups.setdefault(key, {'day': e['day'], 'kind': e['kind'], 'act': e['act'], 'events': []})
        g['events'].append(e)
    return [groups[k] for k in sorted(groups)]


def fast_import(repo, groups, fgroups, docs, laws_by_dir, retrieved, today):
    proc = subprocess.Popen(['git', '-C', repo, 'fast-import', '--quiet', '--done'], stdin=subprocess.PIPE)
    w = proc.stdin.write
    mark = 0
    state = {}

    def commit(ref, g, parent):
        nonlocal mark
        changed = {}
        for e in g['events']:
            law = e['law']
            if e['kind'] in ('enacted', 'consolidated'):
                for p in [p for p in state if p.startswith(law + '/')]:
                    changed[p] = None
                for p, t in e['files'].items():
                    changed['%s/%s' % (law, p)] = t
                changed['%s/index.txt' % law] = ''.join(p + '\n' for p in e['order'])
            elif e['kind'] in ('amendment', 'unexplained'):
                for p, t in e['changes'].items():
                    changed['%s/%s' % (law, p)] = t
                changed['%s/index.txt' % law] = ''.join(p + '\n' for p in e['order'])
            elif e['kind'] == 'repeal':
                for p in [p for p in state if p.startswith(law + '/')]:
                    changed[p] = None
        changed = {p: t for p, t in changed.items() if state.get(p) != t}
        if not changed:
            return None
        mark += 1
        name, email, msg = commit_text(g, laws_by_dir, today)
        when = git_date(g['day'], mark)
        w(('commit %s\nmark :%d\n' % (ref, mark)).encode())
        w(('author %s <%s> %s\ncommitter %s %s\n' % (name, email, when, COMMITTER, when)).encode('utf-8'))
        m = msg.encode('utf-8')
        w(b'data %d\n' % len(m) + m + b'\n')
        if parent:
            w(b'from :%d\n' % parent)
        for p in sorted(changed):
            t = changed[p]
            if t is None:
                w(('D %s\n' % p).encode('utf-8'))
                state.pop(p, None)
            else:
                b = t.encode('utf-8')
                w(('M 100644 inline %s\n' % p).encode('utf-8') + b'data %d\n' % len(b) + b + b'\n')
                state[p] = t
        w(b'\n')
        return mark

    counts = Counter()
    for g in groups:
        g['retrieved'] = retrieved
        if commit('refs/heads/main', g, None):
            counts[g['kind']] += 1
    mark += 1
    when = git_date(retrieved, mark)
    msg = ('Documentation: README, REPRODUCE, LICENSE, corpus.json, laws.csv, data and tools\n\n'
           'Built from the Riksdagen open data and SFSR cache retrieved on %s. No law text changes.\n\n'
           'Kind: documentation\nIn-force: %s\n' % (retrieved, retrieved)).encode()
    w(b'commit refs/heads/main\nmark :%d\n' % mark)
    w(('author %s %s\ncommitter %s %s\n' % (COMMITTER, when, COMMITTER, when)).encode())
    w(b'data %d\n' % len(msg) + msg + b'\n')
    for p, data in sorted(docs.items()):
        w(('M 100644 inline %s\n' % p).encode('utf-8') + b'data %d\n' % len(data) + data + b'\n')
    w(b'\n')
    parent = mark
    fcount = 0
    for g in fgroups:
        g['retrieved'] = retrieved
        m = commit('refs/heads/future', g, parent)
        if m:
            parent = m
            fcount += 1
    w(b'done\n')
    proc.stdin.close()
    if proc.wait():
        sys.exit('git fast-import failed')
    return counts, fcount


def prepare_repo(repo):
    remote = None
    if os.path.exists(repo) and os.listdir(repo):
        if not os.path.exists(os.path.join(repo, '.git')):
            sys.exit('%s is not empty and not a git repository; refusing' % repo)
        r = subprocess.run(['git', '-C', repo, 'log', '--all', '--format=%ae'], capture_output=True, text=True)
        authors = {a for a in r.stdout.split() if not a.endswith('.invalid')}
        if authors - {'sweden-law@lexifina.com', 'world-law@lexifina.com'}:
            sys.exit('%s holds commits by %s; refusing to replace it' % (repo, sorted(authors)))
        remote = subprocess.run(['git', '-C', repo, 'remote', 'get-url', 'origin'], capture_output=True,
                                text=True).stdout.strip() or None

        def onerr(func, path, _):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        for child in os.listdir(repo):
            p = os.path.join(repo, child)
            shutil.rmtree(p, onerror=onerr) if os.path.isdir(p) else os.unlink(p)
    os.makedirs(repo, exist_ok=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', repo], check=True)
    for k, v in (('core.autocrlf', 'false'), ('core.longpaths', 'true'), ('core.quotepath', 'false')):
        subprocess.run(['git', '-C', repo, 'config', k, v], check=True)
    if remote:
        subprocess.run(['git', '-C', repo, 'remote', 'add', 'origin', remote], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo')
    ap.add_argument('--today', required=True)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    if not valid_iso(a.today):
        sys.exit('--today must be YYYY-MM-DD')
    retrieved = open(os.path.join(sfs.CACHE, 'retrieved.txt'), encoding='utf-8').read().strip()
    if retrieved > a.today:
        sys.exit('retrieval day %s is after --today %s' % (retrieved, a.today))
    metas = sorted(sfs.load_docs(), key=lambda m: (sfs.sfs_key(m['beteckning']) or (0, 0), m['beteckning']))
    if a.limit:
        step = max(1, len(metas) // a.limit)
        metas = metas[::step][:a.limit] + [m for m in metas if m['beteckning'] in ('1962:700', '1942:740', '1974:152')]
    with ProcessPoolExecutor(a.workers) as pool:
        laws = list(pool.map(build_law_safe, [(m, a.today) for m in metas], chunksize=16))
    seen = Counter(l['dir'] for l in laws)
    for k, l in enumerate(laws):
        l['sort'] = (k, 0)
        if seen[l['dir']] > 1:
            l['issues'].append([l['bet'], 'folder %s used by two documents' % l['dir']])
    laws_by_dir = {}
    for l in laws:
        if l['dir'] in laws_by_dir:
            l['events'] = []
            continue
        laws_by_dir[l['dir']] = l
    events = [e for l in laws for e in l['events']]
    groups = plan([e for e in events if e['branch'] == 'main'], laws_by_dir)
    fgroups = plan([e for e in events if e['branch'] == 'future'], laws_by_dir)
    import docs as docmod
    docs = docmod.render(laws, groups, fgroups, a.today, retrieved)
    prepare_repo(a.repo)
    counts, fcount = fast_import(a.repo, groups, fgroups, docs, laws_by_dir, retrieved, a.today)
    subprocess.run(['git', '-C', a.repo, 'checkout', '-q', '-f', 'main'], check=True)
    head = subprocess.run(['git', '-C', a.repo, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    n = subprocess.run(['git', '-C', a.repo, 'rev-list', '--count', 'HEAD'], capture_output=True, text=True).stdout.strip()
    print('built %s: %s commits on main %s, future %d, statutes %d, head %s' % (a.repo, n, dict(counts), fcount,
                                                                            len(laws), head), flush=True)


if __name__ == '__main__':
    main()
