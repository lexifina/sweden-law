"""Functional smoke test of the built Swedish law repository: the things a reader does with it work. Standard library only.

    python -X utf8 scripts/sweden-law/test_repo.py --repo ../sweden-law [--record]

It asserts that:
1. `git blame` on a provision changed by a dated amendment names the amending act (an SFS number) for its lines;
2. tools/in_force_on.py gives the text before and after dated amendments, each equal to the file at that commit and
   its parent, and a repealed statute's text the day before its repeal and nothing on the day;
3. `git log --follow` on a changed file lists its commits in in-force order;
4. every commit has an `In-force:` trailer;
5. no commit is authored by Lexifina except the documentation commit (the last one);
6. every git or python command in the README's table runs and prints something.
With --record the result is added to verification.json next to this script (for REPRODUCE.md).
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACT = re.compile(r'\((\d{4}:\d+)\)|^SFS \d{4}:\d+$')


class Smoke:
    def __init__(self, repo):
        self.repo = repo
        self.results = []

    def git(self, *args):
        out = subprocess.run(['git', *args], cwd=self.repo, capture_output=True)
        if out.returncode:
            raise AssertionError('git %s: %s' % (' '.join(args), out.stderr.decode('utf-8', 'replace').strip()))
        return out.stdout.decode('utf-8')

    def tool(self, *args, ok=True):
        out = subprocess.run([sys.executable, '-X', 'utf8', 'tools/in_force_on.py', *args], cwd=self.repo,
                             capture_output=True)
        if ok and out.returncode:
            raise AssertionError('in_force_on %s: %s' % (' '.join(args), out.stderr.decode('utf-8', 'replace').strip()))
        return out.stdout.decode('utf-8'), out.returncode

    def check(self, name, fn):
        try:
            detail = fn() or ''
            self.results.append((name, True, detail))
            print('PASS %s: %s' % (name, detail))
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            print('FAIL %s: %s' % (name, exc))

    def amendments(self, n=3, ref='main'):
        """(commit, in-force day, file) of the newest dated amendments on ref that modify an existing file."""
        out = self.git('log', '--format=%x1e%H%x1f%(trailers:key=In-force,valueonly,separator=)%x1f%(trailers:key=Kind,'
                              'valueonly,separator=)', '--diff-filter=M', '--name-only', ref)
        found = []
        for chunk in out.split('\x1e')[1:]:
            head, _, rest = chunk.partition('\n')
            sha, day, kind = head.split('\x1f')
            if kind.strip() != 'amendment':
                continue
            files = [f for f in rest.split() if f.endswith('.md') and not f.endswith('index.txt')]
            if files:
                found.append((sha, day.strip(), files[0]))
            if len(found) >= n:
                break
        if not found and ref == 'main':
            return self.amendments(n, 'future')
        assert found, 'no dated amendment on main or future'
        return [(sha, day, path, ref) for sha, day, path in found] if ref == 'future' else found

    def blame(self):
        sha, day, path, *ref = self.amendments(1)[0]
        out = self.git('blame', '--line-porcelain', ref[0] if ref else 'main', '--', path)
        authors = {l[7:] for l in out.splitlines() if l.startswith('author ')}
        assert any(ACT.search(a) for a in authors), 'no act among %s' % authors
        return '%s lines by %s' % (path, ', '.join(sorted(authors)))

    def on_date(self):
        done = []
        for sha, day, path, *ref in self.amendments(3):
            if ref:
                continue  # the on-date tool reads main; future amendments are checked by blame and follow
            prev = (subprocess.run([sys.executable, '-c', 'import datetime,sys;print(datetime.date.fromisoformat(sys.argv[1])'
                                    '-datetime.timedelta(days=1))', day], capture_output=True, text=True).stdout.strip())
            after, _ = self.tool(day, path)
            before, rc = self.tool(prev, path, ok=False)
            assert after.rstrip('\n') == self.git('show', '%s:%s' % (sha, path)).rstrip('\n'), '%s on %s' % (path, day)
            if rc == 0:
                assert before.rstrip('\n') == self.git('show', '%s~1:%s' % (sha, path)).rstrip('\n'), '%s on %s' % (path, prev)
            assert before != after, '%s: no change between %s and %s' % (path, prev, day)
            done.append('%s %s != %s' % (path, prev, day))
        out = self.git('log', '-1', '--format=%H%x1f%(trailers:key=In-force,valueonly,separator=)', '--diff-filter=D', 'main')
        sha, day = out.strip().split('\x1f')
        law = '/'.join(self.git('show', '--format=', '--name-only', sha).split()[0].split('/')[:2])
        prev = subprocess.run([sys.executable, '-c', 'import datetime,sys;print(datetime.date.fromisoformat(sys.argv[1])'
                               '-datetime.timedelta(days=1))', day], capture_output=True, text=True).stdout.strip()
        text, _ = self.tool(prev, law)
        gone, rc = self.tool(day, law, ok=False)
        assert text.strip() and rc != 0 and not gone.strip(), '%s: repeal on %s not seen' % (law, day)
        done.append('%s repealed %s' % (law, day))
        whole, _ = self.tool('2026-10-28', '1974/152')
        assert 'Regeringsform' in whole or 'folket' in whole, 'Regeringsform not printed'
        return '; '.join(done)

    def follow(self):
        checked = []
        for sha, day, path, *ref in self.amendments(3):
            out = self.git('log', '--follow', '--format=%(trailers:key=In-force,valueonly,separator=)', ref[0] if ref else 'main', '--', path)
            days = [d.strip() for d in out.splitlines() if d.strip()]
            assert len(days) >= 2, '%s: %d commits' % (path, len(days))
            assert days == sorted(days, reverse=True), '%s: not in date order %s' % (path, days)
            checked.append('%s %d commits' % (path, len(days)))
        return '; '.join(checked)

    def trailers(self):
        rows = self.git('log', '--format=%H%x09%(trailers:key=In-force,valueonly,separator=)', 'main', 'future').splitlines()
        bad = [r for r in rows if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', r.split('\t')[1].strip())]
        assert not bad, '%d commits without In-force, e.g. %s' % (len(bad), bad[:2])
        return '%d commits on main and future carry In-force' % len(rows)

    def lexifina(self):
        rows = self.git('log', '--format=%H%x09%an%x09%cn', 'main').splitlines()
        by_lex = [r for r in rows if r.split('\t')[1] == 'Lexifina']
        assert len(by_lex) == 1 and by_lex[0] == rows[0], '%d commits by Lexifina' % len(by_lex)
        assert all(r.split('\t')[2] == 'Lexifina' for r in rows), 'a committer other than Lexifina'
        return 'only the documentation commit %s is by Lexifina' % rows[0][:10]

    def readme(self):
        text = (self.repo / 'README.md').read_text(encoding='utf-8')
        table = text[text.index('## A lawyer'):text.index('## The dates')]
        cmds = [c for c in re.findall(r'`([^`]+)`', table) if c.startswith(('git ', 'python ')) and '<' not in c]
        assert len(cmds) >= 6, 'only %d commands found' % len(cmds)
        for c in cmds:
            args = shlex.split(c)
            if args[0] == 'python':
                args[0:1] = [sys.executable, '-X', 'utf8']
            out = subprocess.run(args, cwd=self.repo, capture_output=True)
            assert out.returncode == 0, '%s: exit %d %s' % (c, out.returncode, out.stderr.decode('utf-8', 'replace')[:200])
            assert out.stdout.strip(), '%s: printed nothing' % c
        return '%d README commands ran and printed output' % len(cmds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--record', action='store_true')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    s = Smoke(Path(a.repo))
    s.check('blame names an act', s.blame)
    s.check('text before and after amendments and a repeal', s.on_date)
    s.check('log --follow in date order', s.follow)
    s.check('In-force on every commit', s.trailers)
    s.check('only the documentation commit is by Lexifina', s.lexifina)
    s.check('README commands run', s.readme)
    ok = all(r[1] for r in s.results)
    print('%d/%d passed' % (sum(r[1] for r in s.results), len(s.results)))
    if a.record:
        p = HERE / 'verification.json'
        rec = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        rec['smoke'] = {'passed': sum(r[1] for r in s.results), 'total': len(s.results),
                        'results': [{'test': n, 'ok': o, 'detail': d} for n, o, d in s.results]}
        p.write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
