"""Readers for the cached Swedish sources: Riksdagen dokumentstatus JSON (HTML + plain text) and SFSR pages.

Everything here is a pure function of the cached bytes, so build.py and check_repo.py get the same data.
"""
import gzip
import html as htmlmod
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, '.cache')

SFS_RE = re.compile(r'(\d{4}):(\d+)')
DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
TAG_RE = re.compile(r'<(/?)([a-zA-Z0-9]+)((?:[^>"]|"[^"]*")*)>', re.S)
WS_RE = re.compile(r'[ \t\r\n\u00a0\u2002\u2003\u2009\u200b\u00ad]+')
BLANK_RE = re.compile(r'\n[ \t\r\u00a0]*\n')
# a validity marker as the publisher prints it: /Upph\u00f6r att g\u00e4lla U:<when>/ or /Tr\u00e4der i kraft I:<when>/,
# optionally led by Kapitlet, Rubriken, Lagen ... (the closing slash is sometimes missing)
PAR_LINE = re.compile(r'^(\d+(?: ?[a-z])?) \u00a7(?: (?=[A-Z\u00c5\u00c4\u00d6\u00c9/0-9\u2013-])|$)')
CH_LINE = re.compile(r'^(\d+(?: ?[a-z])?) kap\.(?: |$)')
MARK_RE = re.compile(r'/\s*([A-Za-z\u00c0-\u00ff ]*?)\s*(upph\u00f6r att g\u00e4lla U|tr\u00e4der i kraft I)\s*:\s*([^/\n]*?)\s*(?:/|(?=\n)|$)',
                     re.I)
NOTE_RE = re.compile(r'(?:Lag|F\u00f6rordning|Kung\u00f6relse|Tillk\u00e4nnagivande|Instruktion|Reglemente|Stadga|'
                     r'Beslut|Riksdagsordning|Br\u00e5dskande lag)\s*\((\d{4}:\d+)\)', re.I)


def sfs_key(s):
    m = SFS_RE.search(s or '')
    return (int(m.group(1)), int(m.group(2))) if m else None


def sfs_str(k):
    return '%d:%d' % k


def slug(bet):
    """Folder name for an SFS number: 1962:700 -> 1962/700; 1736:0123 2 -> 1736/0123-2; 1864:11 s. 101 -> 1864/11-s-101."""
    year, _, num = bet.partition(':')
    s = re.sub(r'[^0-9a-z]+', '-', num.lower()).strip('-')
    return '%s/%s' % (year, s)


def load_docs():
    with open(os.path.join(CACHE, 'docs.json'), encoding='utf-8') as f:
        docs = json.load(f)
    return [d for d in docs if d.get('subtyp') == 'sfst']


def doc_path(dok_id):
    return os.path.join(CACHE, 'doc', dok_id + '.json.gz')


def sfsr_path(bet):
    return os.path.join(CACHE, 'sfsr', bet.replace(':', '-').replace(' ', '_') + '.html.gz')


def read_doc(dok_id):
    p = doc_path(dok_id)
    if not os.path.exists(p):
        return None
    with gzip.open(p, 'rb') as f:
        d = json.loads(f.read().decode('utf-8'))['dokumentstatus']
    doc = d['dokument']
    up = {}
    for k in ('dokuppgift', 'dokumentuppgift'):
        for u in ((d.get(k) or {}).get('uppgift') or []):
            up.setdefault(u['kod'], u.get('text') or '')
    return {'html': doc.get('html') or '', 'text': doc.get('text') or '', 'titel': doc.get('titel') or '',
            'subtitel': doc.get('subtitel') or '', 'organ': doc.get('organ') or '', 'datum': doc.get('datum') or '',
            'uppgift': up}


def read_sfsr(bet):
    p = sfsr_path(bet)
    if not os.path.exists(p):
        return None
    with gzip.open(p, 'rb') as f:
        return parse_sfsr(f.read().decode('utf-8', 'replace'))


LABELS = ('Departement', 'Ikraft', 'F\u00f6rarbeten', 'Omfattning', 'Rubrik', 'Upph\u00e4vd', 'CELEX-nr', '\u00d6vrigt',
          'Tidsbegr\u00e4nsad', 'Ikrafttr\u00e4dande', 'Celexnr', 'Kommentar')


def parse_sfsr(page):
    """The SFSR register: {'title', 'fields': {label: value}, 'entries': [{'sfs', 'fields'}]}."""
    s = re.sub(r'<script.*?</script>|<style.*?</style>', '', page, flags=re.S | re.I)
    s = re.sub(r'<[^>]+>', '\n', s)
    s = htmlmod.unescape(s)
    lines = [WS_RE.sub(' ', x).strip() for x in s.split('\n')]
    lines = [x for x in lines if x]
    try:
        start = next(i for i, x in enumerate(lines) if x == 'Visa fulltext') + 1
    except StopIteration:
        start = next((i for i, x in enumerate(lines) if x.startswith('SFS-nummer')), 0) + 1
    try:
        end = max(i for i, x in enumerate(lines) if x == 'S\u00f6kresultat')
    except ValueError:
        end = len(lines)
    if end <= start:
        end = len(lines)
    body = lines[start:end]
    out = {'title': '', 'fields': {}, 'entries': []}
    cur = out['fields']
    label = None
    title = []
    for x in body:
        m = re.match(r'^\u00c4ndring, SFS (.+)$', x)
        if m:
            e = {'sfs': m.group(1).strip(), 'fields': {}}
            out['entries'].append(e)
            cur, label = e['fields'], None
            continue
        if x.endswith(':') and x[:-1] in LABELS or (x.endswith(':') and len(x) < 30 and ' ' not in x[:-1].strip()):
            label = x[:-1]
            cur.setdefault(label, '')
            continue
        if label is None and cur is out['fields'] and not out['entries']:
            title.append(x)
            continue
        if label is None:
            continue
        cur[label] = (cur[label] + ' ' + x).strip() if cur[label] else x
    out['title'] = ' '.join(title)
    return out


# ------------------------------------------------------------------------------------------------ HTML body

def split_header(html):
    """(header fields, body html). The header is the publisher's metadata block (SFS nr, Utf\u00e4rdad, \u00c4ndrad, ...)."""
    m = re.search(r'Fulltext \(Regeringskansliet\)</a>\s*(?:<br\s*/?>\s*)*(?:<hr\s*/?>)?', html)
    if not m:
        m = re.search(r'<hr\s*/?>', html)
    head, body = (html[:m.start()], html[m.end():]) if m else ('', html)
    fields = {}
    for fm in re.finditer(r'<b>([^<]+)</b>\s*:\s*(.*?)<br\s*/?>', head, re.S):
        fields[WS_RE.sub(' ', fm.group(1)).strip()] = WS_RE.sub(' ', htmlmod.unescape(re.sub(r'<[^>]+>', '', fm.group(2)))).strip()
    body = re.sub(r'<style.*?</style>', '', body, flags=re.S | re.I)
    body = re.sub(r'<div class="sfstoc">.*?</ul>\s*</div>', '', body, flags=re.S)
    return fields, body


def clean_line(s):
    return WS_RE.sub(' ', s).strip()


def parse_body(body):
    """Units in document order: [{'kind', 'key', 'chapter', 'para', 'lines'}].

    kind: ingress (text before the first provision), par (a paragraf, from <a class="paragraf" name="K1P2a">),
    overgang (\u00d6verg\u00e5ngsbest\u00e4mmelser, everything after its heading), bilaga, text (a document without paragraf
    markup). Headings (h2/h3/h4) become '#'-lines at the top of the next unit. Every stycke and every <br> line is one
    line; a blank line in the text also separates stycken."""
    units = []
    cur = None
    pending = []          # heading lines waiting for the next unit
    buf = []              # text of the current line
    chapter = None
    in_overgang = False
    heading = None        # (level, name, [text]) while inside <hN>
    anchored = 'class="paragraf"' in body

    def new_unit(kind, key, para=None):
        nonlocal cur
        cur = {'kind': kind, 'key': key, 'chapter': chapter, 'para': para, 'lines': list(pending)}
        pending.clear()
        units.append(cur)

    def flush():
        nonlocal cur, chapter
        text = ''.join(buf)
        buf.clear()
        parts = [clean_line(p) for p in BLANK_RE.split(text.replace('\r', ''))]
        parts = [p for p in parts if p]
        if not parts:
            return
        if pending:
            if cur is None:
                new_unit('ingress', 'ingress')
            else:
                cur['lines'].extend(pending)
                pending.clear()
        if cur is None:
            new_unit('ingress', 'ingress')
        for p in parts:
            if in_overgang:
                if re.fullmatch(r'Bilaga(?: [0-9]+[a-z]?| [A-Z])?(?: till [^.]{0,80})?', p) and cur['kind'] != 'bilaga':
                    n = sum(1 for u in units if u['kind'] == 'bilaga') + 1
                    new_unit('bilaga', 'bilaga-%d' % n)
                cur['lines'].append(p)
                continue
            # a paragraf the publisher left without an anchor (or a document with no anchors at all)
            pm = PAR_LINE.match(p)
            if pm and cur['kind'] in ('par', 'ingress') and any(not l.startswith('#') for l in cur['lines']):
                new_unit('par', 'split', para=pm.group(1).replace(' ', ''))
                cur['chapter'] = chapter
                cur['split'] = True
            elif not anchored and CH_LINE.match(p) and '\u00a7' not in p and len(p) < 200:
                chapter = CH_LINE.match(p).group(1).replace(' ', '')
                pending.append(p)
                continue
            elif pm and cur['kind'] == 'ingress' and not any(not l.startswith('#') for l in cur['lines']):
                cur.update(kind='par', key='split', para=pm.group(1).replace(' ', ''), chapter=chapter, split=True)
            cur['lines'].append(p)

    pos = 0
    for m in TAG_RE.finditer(body):
        txt = body[pos:m.start()]
        pos = m.end()
        if heading is not None:
            heading[2].append(txt)
        else:
            buf.append(htmlmod.unescape(txt))
        close, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            if not close:
                flush()
                nm = re.search(r'name="([^"]*)"', attrs)
                heading = (int(tag[1]), nm.group(1) if nm else '', [])
            elif heading is not None:
                level, name, parts = heading
                heading = None
                text = clean_line(htmlmod.unescape(re.sub(r'<[^>]+>', '', ''.join(parts))))
                if name == 'overgang':
                    in_overgang = True
                    pending.append('#' * max(1, level - 1) + ' ' + text)
                    new_unit('overgang', 'overgang')
                    continue
                if re.fullmatch(r'K\d+[a-zA-Z]?', name):
                    chapter = name[1:]
                if text:
                    pending.append('#' * max(1, level - 1) + ' ' + text)
            continue
        if heading is not None:
            continue
        if tag == 'a' and not close and 'class="paragraf"' in attrs:
            flush()
            nm = re.search(r'name="([^"]*)"', attrs).group(1)
            if in_overgang:
                continue  # paragrafs inside the transitional provisions stay in overgang
            pm = re.fullmatch(r'(?:K(\d+[a-zA-Z]?))?P(\d+[a-zA-Z]?)', nm)
            if pm:
                if pm.group(1):
                    chapter = pm.group(1)
                new_unit('par', nm, para=pm.group(2))
                cur['chapter'] = pm.group(1)
            else:
                new_unit('par', nm, para=re.sub(r'[^0-9a-zA-Z]', '', nm))
                cur['chapter'] = None
            continue
        if tag in ('p', 'br', 'div', 'pre', 'li', 'ul', 'table', 'tr', 'hr'):
            flush()
            continue
        if tag in ('td', 'th'):
            buf.append(' ')
            continue
    buf.append(htmlmod.unescape(body[pos:]))
    flush()
    if pending:
        if cur is None:
            new_unit('ingress', 'ingress')
        else:
            cur['lines'].extend(pending)
            pending.clear()
    return units


def unit_path(u):
    if u['kind'] == 'par':
        p = u['para'].lower()
        return ('kap-%s/%s.md' % (u['chapter'].lower(), p)) if u['chapter'] else ('%s.md' % p)
    return u['key'] + '.md'


def markers(text):
    """[(scope word, 'I' or 'U', when-text)] for every validity marker in the text."""
    out = []
    for m in MARK_RE.finditer(text):
        kind = 'U' if m.group(2).lower().startswith('upph') else 'I'
        out.append((m.group(1).strip(), kind, m.group(3).strip()))
    return out


def when_date(when):
    m = re.match(r'(\d{4}-\d{2}-\d{2})\b', when)
    return m.group(1) if m else None


def raw_letters(s):
    """Letters and digits only, casefolded: the independent comparison stream."""
    return re.sub(r'[\W_]+', '', s.casefold())
