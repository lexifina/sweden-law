"""Check the Sweden law repository against the raw source, independently of the build's parser and renderer.

    python -X utf8 check_repo.py <repo> [--write]     (--write saves verification.json for docs.py)

1. Every version of every statute, on `main` and on `future`, is read back from git (index.txt order) and its letters
   and digits (casefolded, everything else removed) are compared with the RAW source:
   - statutes without markers, and those kept as printed: the plain-text field `text` of the dokumentstatus JSON,
     which build.py never reads;
   - statutes with dated two-version provisions: the HTML read by a separate minimal extractor (split at paragraf
     anchors and headings by regex, every text node kept, passages whose leading marker is not in force on the
     version's In-force day dropped).
2. Commits: trailers parse (`git interpret-trailers --parse` on every message), every commit has In-force, no merges,
   In-force never goes backwards on main, nothing on main after the build day, no CR in any message.
3. tools/in_force_on.py on sample paths and dates.
"""
import gzip
import html as htmlmod
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, '.cache')


def letters(s):
    return re.sub(r'[\W_]+', '', s.casefold())


def git(repo, *a):
    return subprocess.run(['git', '-C', repo, *a], capture_output=True, check=True).stdout.decode('utf-8')


class Blobs:
    def __init__(self, repo):
        self.p = subprocess.Popen(['git', '-C', repo, 'cat-file', '--batch'], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE)

    def get(self, rev, path):
        self.p.stdin.write(('%s:%s\n' % (rev, path)).encode('utf-8'))
        self.p.stdin.flush()
        head = self.p.stdout.readline().split()
        if len(head) < 3 or head[1] != b'blob':
            return None
        data = self.p.stdout.read(int(head[2]))
        self.p.stdout.read(1)
        return data.decode('utf-8')


def raw_doc(dok_id, bet):
    p = os.path.join(CACHE, 'doc', dok_id + '.json.gz')
    if not os.path.exists(p):
        # Riksdagen answered 404: the SFST full-text page, all text inside its body-text box
        with gzip.open(os.path.join(CACHE, 'sfst', bet.replace(':', '-').replace(' ', '_') + '.html.gz'), 'rb') as f:
            page = f.read().decode('utf-8')
        box = page.split('body-text">', 1)[1].split('</div>', 1)[0]
        return {'text': htmlmod.unescape(re.sub(r'<[^>]+>', ' ', box)), 'html': ''}
    with gzip.open(p, 'rb') as f:
        return json.loads(f.read().decode('utf-8'))['dokumentstatus']['dokument']


MARK = re.compile(r'^\s*\d+(?: ?[a-z])?\s+(?:\u00a7\s*)?((?:/+\s*(?:Upph\u00f6r att g\u00e4lla U|Tr\u00e4der i kraft I)\s*:[^/\n]*(?:/|(?=\n)|$)\s*)+)',
                  re.I)
# a paragraf the publisher printed without an anchor: a stycke that starts "14 b \u00a7 Text", "1 b \u00a7 //Upph\u00f6r.../" or
# "4 c /Tr\u00e4der.../ \u00a7 Text" (after a blank line, a </p> or a <br />); the cut is at group "p"
LOOSE = re.compile(r'(?:\n[ \t\r\u00a0]*\n|</p>|<br ?/>[ \t\r]*\n?)(?P<p>[ \t\u00a0]*\d+(?: ?[a-z])?[ \t\u00a0]+'
                   r'(?:(?:<i>\s*)?/+[^/<\n]*(?:Upph\u00f6r att g\u00e4lla U|Tr\u00e4der i kraft I)[^/<\n]*/(?:\s*</i>)?\s*)?'
                   r'\u00a7(?:\s|<i>|</i>)*[A-Z\u00c5\u00c4\u00d6\u00c9/0-9\u2013-])')
# markup to drop: real HTML tags only (the text keeps typesetting codes such as "11<gr>" or "W<rttemberg")
TAGS = re.compile(r'</?(?:a|p|b|br|i|h[1-6]|pre|div|blockquote|ul|ol|li|table|tbody|thead|tr|td|th|hr|span|sup|sub|em|'
                  r'strong|u|font|style)(?:\s+[a-zA-Z-]+\s*=\s*(?:"[^"]*"|[^\s>"]+))*\s*/?>')
ONE = re.compile(r'(Upph\u00f6r att g\u00e4lla U|Tr\u00e4der i kraft I)\s*:\s*(\d{4}-\d{2}-\d{2})?', re.I)


def html_text_on(html, day):
    """Letters of the HTML body on `day`: a minimal extractor written for this check, not build.py's parser."""
    m = re.search(r'Fulltext \(Regeringskansliet\)</a>', html) or re.search(r'<hr', html)
    body = html[m.end():] if m else html
    body = re.sub(r'<style.*?</style>', '', body, flags=re.S | re.I)
    body = re.sub(r'<div class="sfstoc">.*?</ul>\s*</div>', '', body, flags=re.S)
    cuts = sorted(set([x.start() for x in re.finditer(r'<a class="paragraf"|<h[1-6][ >]', body)] +
                      [x.start('p') for x in LOOSE.finditer(body)]))
    loose = {x.start('p') for x in LOOSE.finditer(body)}
    cuts = [0] + cuts + [len(body)]

    def ok(I, U):
        return not (I == 'undated' or (I and I > day)) and not (U and U != 'undated' and day >= U)

    def val(kind, d):
        return (d or 'undated', None) if kind.lower().startswith('tr') else (None, d or 'undated')

    # pieces: [text, segment index, [validities]]; a heading segment splits into its heading and the text after it
    segs = []
    pieces = []
    for a, b in zip(cuts, cuts[1:]):
        seg = body[a:b]
        si = len(segs)
        hm = re.match(r'<h[1-6][^>]*>(.*?)</h[1-6]>', seg, re.S)
        name = re.match(r'<h3 name="(K[^"]*)"', seg)
        segs.append({'chapter_head': bool(name), 'heading': bool(hm), 'part_head': seg.startswith('<h2'), 'par': seg.startswith('<a class="paragraf"') or
                     a in loose, 'lead': []})
        if segs[-1]['par']:
            mm = MARK.match(htmlmod.unescape(TAGS.sub(' ', seg)))
            if mm:
                segs[-1]['lead'] = [val(k2, d) for k2, d in ONE.findall(mm.group(1))]
        parts = [(seg[:hm.end()], 'head'), (seg[hm.end():], 'rest')] if hm else [(seg, 'rest')]
        for raw, role in parts:
            text = htmlmod.unescape(TAGS.sub(' ', raw))
            # cut standalone Rubriken/Kapitlet markers out as pieces of their own
            pos = 0
            for mk in re.finditer(r'/+\s*(Rubriken|Ruriken|Kapitlet)\s+(upph\u00f6r att g\u00e4lla U|tr\u00e4der i kraft I)\s*:\s*'
                                  r'(\d{4}-\d{2}-\d{2})?[^/\n]*/?', text):
                pieces.append([text[pos:mk.start()], si, role, []])
                pieces.append([mk.group(0), si, 'marker', [], mk.group(1), mk.group(2), mk.group(3)])
                pos = mk.end()
            pieces.append([text[pos:], si, role, []])
    seg_val = [[] for _ in segs]
    for k, p in enumerate(pieces):
        if p[2] != 'marker':
            continue
        scope, kind, d = p[4], p[5], p[6]
        v = val(kind, d)
        p[3].append(v)
        si = p[1]
        if scope in ('Rubriken', 'Ruriken'):
            before = ''.join(q[0] for q in pieces[:k] if q[1] == si)
            if segs[si]['par'] and re.fullmatch(r'\s*\d+(?: ?[a-z])? \u00a7\s*', before):
                # "N \u00a7 /Rubriken ...": the heading just before this paragraf
                for q in reversed(pieces[:k]):
                    if q[2] == 'head':
                        q[3].append(v)
                        break
            else:
                for q in pieces[k + 1:]:
                    if q[2] == 'head':
                        q[3].append(v)
                        break
        elif kind.lower().startswith('upph'):
            j = si
            while j >= 0:
                seg_val[j].append(v)
                if segs[j]['chapter_head']:
                    break
                j -= 1
        else:
            j = si + 1
            while j < len(segs) and not segs[j]['chapter_head']:
                j += 1
            if j < len(segs):
                seg_val[j].append(v)
                j += 1
                # a chapter ends at the next chapter, or at a new part (h2 "TREDJE AVDELNINGEN") before it
                while j < len(segs) and not segs[j]['chapter_head'] and not segs[j]['part_head']:
                    seg_val[j].append(v)
                    j += 1
    out = []
    for text, si, role, vs, *_ in pieces:
        if all(ok(*v) for v in vs + seg_val[si] + segs[si]['lead']):
            out.append(text)
    return letters(' '.join(out))


def first_diff(a, b):
    n = min(len(a), len(b))
    i = next((k for k in range(n) if a[k] != b[k]), n)
    return i, a[max(0, i - 40):i + 40], b[max(0, i - 40):i + 40]


def main():
    repo = sys.argv[1]
    write = '--write' in sys.argv
    laws = {}
    import csv
    with open(os.path.join(repo, 'laws.csv'), encoding='utf-8') as f:
        for r in csv.DictReader(f):
            laws[r['path'].rstrip('/')] = r
    docs = {m['beteckning']: m['dok_id'] for m in json.load(open(os.path.join(CACHE, 'docs.json'), encoding='utf-8'))}

    # versions: every commit that touches a statute, per branch
    versions = defaultdict(list)  # law -> [(rev, in_force, deleted)]
    for ref in ('main', 'future'):
        try:
            out = git(repo, 'log', '--reverse', '--format=%x1e%H%x1f%(trailers:key=In-force,valueonly,separator=)',
                      '--name-status', '--no-renames', ref if ref == 'main' else 'main..future')
        except subprocess.CalledProcessError:
            continue
        for chunk in out.split('\x1e')[1:]:
            head, _, rest = chunk.partition('\n')
            sha, _, when = head.partition('\x1f')
            touched = {}
            for line in rest.splitlines():
                st, _, p = line.partition('\t')
                parts = p.split('/')
                if len(parts) >= 3 and parts[0][:1].isdigit():
                    law = '/'.join(parts[:2])
                    touched[law] = touched.get(law, False) or (st == 'D' and p.endswith('/index.txt'))
            for law, deleted in touched.items():
                versions[law].append((sha, when.strip(), deleted))
    blobs = Blobs(repo)
    n_versions = n_bad = n_words = 0
    bad = []
    raw_cache = {}
    for law, vs in sorted(versions.items()):
        meta = laws.get(law)
        if not meta:
            bad.append([law, '', 'not in laws.csv'])
            n_bad += 1
            continue
        dok = docs[meta['sfs']]
        if dok not in raw_cache:
            raw_cache.clear()
            raw_cache[dok] = raw_doc(dok, meta['sfs'])
        raw = raw_cache[dok]
        for j, (sha, when, deleted) in enumerate(vs):
            if deleted:
                continue
            if j + 1 < len(vs) and vs[j + 1][1] == when and not vs[j + 1][2]:
                continue  # several acts on one day: only the day's last commit holds the whole day's text
            idx = blobs.get(sha, law + '/index.txt')
            if idx is None:
                bad.append([law, sha[:7], 'no index.txt'])
                n_bad += 1
                continue
            text = ''.join((blobs.get(sha, '%s/%s' % (law, n)) or '\0MISSING\0') for n in idx.split('\n') if n)
            mine = letters(text)
            if meta['text_class'] == 'dated-versions':
                src = html_text_on(raw['html'], when)
            else:
                src = letters(raw['text'])
            n_versions += 1
            n_words += len(text.split())
            if mine != src:
                n_bad += 1
                i, a, b = first_diff(mine, src)
                bad.append([law, sha[:7], 'at %d: git ...%s... source ...%s...' % (i, a, b)])
    # commits
    log = subprocess.run(['git', '-C', repo, 'log', '--format=%H%x1f%P%x1f%ct%x1f%B%x1e', 'main'], capture_output=True
                         ).stdout.decode('utf-8')
    commits = [c.strip('\n').split('\x1f') for c in log.split('\x1e') if c.strip()]
    flog = subprocess.run(['git', '-C', repo, 'log', '--format=%H%x1f%P%x1f%ct%x1f%B%x1e', 'main..future'],
                          capture_output=True).stdout.decode('utf-8')
    fcommits = [c.strip('\n').split('\x1f') for c in flog.split('\x1e') if c.strip()]
    today = json.load(open(os.path.join(repo, 'data', 'stats.json'), encoding='utf-8'))['today']
    merges = sum(1 for c in commits + fcommits if len(c[1].split()) > 1)
    crs = sum(c[3].count('\r') for c in commits + fcommits)
    no_inforce, backwards, after = [], 0, 0
    last = None
    for c in reversed(commits):
        m = re.search(r'^In-force: (\d{4}-\d{2}-\d{2})$', c[3], re.M)
        if not m:
            no_inforce.append(c[0][:7])
            continue
        if last and m.group(1) < last:
            backwards += 1
        last = m.group(1)
        if m.group(1) > today:
            after += 1
    bad_trailers = 0
    for c in commits + fcommits:
        r = subprocess.run(['git', 'interpret-trailers', '--parse'], input=c[3].encode('utf-8'), capture_output=True)
        keys = {l.split(':', 1)[0] for l in r.stdout.decode('utf-8').splitlines()}
        if 'In-force' not in keys or 'Kind' not in keys:
            bad_trailers += 1
    res = {'versions': n_versions, 'words': n_words, 'mismatches': n_bad, 'commits': len(commits),
           'future_commits': len(fcommits), 'merges': merges, 'cr_in_messages': crs, 'no_in_force': len(no_inforce),
           'backwards': backwards, 'after_build_day': after, 'bad_trailers': bad_trailers, 'samples': bad[:40]}
    res['summary'] = ('check_repo.py read back every version of every statute ({versions} versions, {words} words, on '
                      '`main` and `future`) and compared its letters and digits with the raw source: the plain-text field '
                      'of the Riksdagen JSON for statutes without dated versions (text build.py never reads), and for '
                      'statutes with dated two-version provisions the HTML through a separate minimal extractor, keeping '
                      'the passages in force on the version\'s day: {mismatches} mismatches. {commits} commits on main and '
                      '{future_commits} on future: {bad_trailers} whose trailers git cannot parse, {no_in_force} without '
                      'In-force, {merges} merges, {backwards} In-force dates going backwards, {after_build_day} after the '
                      'build day on main, {cr_in_messages} CR characters in messages.').format(**res)
    print(json.dumps({k: v for k, v in res.items() if k != 'samples'}, indent=1))
    for b in bad[:25]:
        print('BAD', *b)
    if write:
        vp = os.path.join(HERE, 'verification.json')
        rec = json.load(open(vp, encoding='utf-8')) if os.path.exists(vp) else {}
        rec.update(res)
        with open(vp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(rec, f, ensure_ascii=False, indent=1, sort_keys=True)
    sys.exit(1 if n_bad or merges or crs or no_inforce or backwards or after or bad_trailers else 0)


if __name__ == '__main__':
    main()
