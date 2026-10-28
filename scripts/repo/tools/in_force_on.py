"""Print Swedish statutes as this repository has them in force on a date.

    python tools/in_force_on.py 2026-10-28 1962/700                 # Brottsbalk (1962:700), whole text
    python tools/in_force_on.py 2026-10-28 1962/700/kap-3/1.md      # 3 kap. 1 \u00a7
    python tools/in_force_on.py 2026-10-28 1974/152                 # Regeringsform (1974:152)
    python tools/in_force_on.py 1995-01-01 1962                     # every statute of 1962 the record has then

The path is any folder or file: a year (SFS year), a statute (year/number), or one provision. Versions are picked by
the `In-force:` trailer (the real day; commit dates before 1970 are clamped to 1970-01-01), never by commit time.
This corpus is current-only: a statute's record starts on the in-force day of the latest amending act its
consolidation includes, so an earlier date has no version here. Standard library only; one `git log` and one
`git cat-file --batch`, so whole years print quickly.
"""
import subprocess
import sys


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, check=True).stdout.decode('utf-8')


def versions_on(day, path):
    """{file: commit} for every file under path as of day (deleted files left out)."""
    out = git('log', '--first-parent', '--no-merges', '--format=%x1e%H%x1f%(trailers:key=In-force,valueonly,separator=)',
              '--name-status', '--no-renames', 'main', '--', path)
    picked = {}
    gone = set()
    for chunk in out.split('\x1e')[1:]:
        head, _, rest = chunk.partition('\n')
        sha, _, when = head.partition('\x1f')
        when = when.strip()
        if not when or when > day:
            continue
        for line in rest.splitlines():
            if not line.strip():
                continue
            st, _, f = line.partition('\t')
            if f in picked or f in gone:
                continue
            if st.startswith('D'):
                gone.add(f)
            else:
                picked[f] = sha
    return picked


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    day, path = sys.argv[1], sys.argv[2].strip('/')
    picked = versions_on(day, path)
    if not picked:
        sys.exit('%s: nothing in force on %s in this record' % (path, day))
    laws = {}
    for f in picked:
        parts = f.split('/')
        if len(parts) >= 3:
            laws.setdefault('/'.join(parts[:2]), []).append(f)
    proc = subprocess.Popen(['git', 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def blob(sha, f):
        proc.stdin.write(('%s:%s\n' % (sha, f)).encode('utf-8'))
        proc.stdin.flush()
        header = proc.stdout.readline().split()
        if len(header) < 3:
            return ''
        data = proc.stdout.read(int(header[2]))
        proc.stdout.read(1)
        return data.decode('utf-8')

    def law_key(law):
        y, _, n = law.partition('/')
        digits = ''.join(c for c in n if c.isdigit()) or '0'
        return (y, int(digits[:9]), n)

    for law in sorted(laws, key=law_key):
        files = laws[law]
        idx = law + '/index.txt'
        order = []
        if idx in picked:
            order = [law + '/' + n for n in blob(picked[idx], idx).splitlines() if n]
        rest = sorted(f for f in files if f not in order and not f.endswith('index.txt'))
        for f in [f for f in order if f in picked] + rest:
            sys.stdout.write(blob(picked[f], f))
            sys.stdout.write('\n')
    proc.stdin.close()
    proc.wait()


if __name__ == '__main__':
    main()
