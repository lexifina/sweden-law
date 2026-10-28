# Swedish statutes (SFS), as a git repository

The statutes of Sweden published in Svensk författningssamling (SFS), as consolidated by the Government Offices
(Regeringskansliet, SFST) and published as open data by the Riksdag: 5304 statutes in force on `main`, from the
Regeringsform (`1974/152`), Brottsbalk (`1962/700`) and Rättegångsbalk (`1942/740`) to the newest
förordningar, plus 5812 repealed statutes in history (deleted on their repeal day) and 37 statutes
not yet in force on the `future` branch. Every paragraf is one file, one stycke per line, so `git blame` and
`git diff` work line by line. The text is Swedish, exactly as published.

**Current text only.** The sources keep one consolidation per statute ("t.o.m. SFS ...", up to and including the
latest amending act), not earlier versions. So each statute has one main text, dated by the day the latest amending
act it includes entered into force. Where the publisher prints a provision in two dated versions
("/Upphör att gälla U:2027-01-01/" and "/Träder i kraft I:2027-01-01/"), the change is a real dated
commit credited to the act named at the end of the new text ("Lag (2026:1604)."), and a repealed statute is deleted on
its repeal day by the repealing act. Every paragraf keeps the publisher's note naming the act that last gave it its
wording, and the register of amendments (SFSR: every amending act, what it changed and when it entered into force) is
in [`data/amendments.csv`](data/amendments.csv) (69678 rows, 67679 amending acts).

## A lawyer's questions as git commands

| Question | Command |
| --- | --- |
| What does Brottsbalken 3 kap. 1 § say today? | `python tools/in_force_on.py 2026-10-28 1962/700/kap-3/1.md` |
| The whole Regeringsform, in order | `python tools/in_force_on.py 2026-10-28 1974/152` |
| Since when has this text been in force, and on whose authority? | `git log --format='%(trailers:key=In-force,valueonly,separator=) %an' -- 1962/700/kap-3/1.md` |
| Which act last gave a paragraf its wording? | the note at its end, e.g. `Lag (2019:806).`; every amendment: `grep '^1962:700,' data/amendments.csv` |
| What changes are already decided, and when? | `git log --format='%(trailers:key=In-force,valueonly,separator=) %s' main..future` |
| Everything one act changed, on any branch (here SFS 2026:1602) | `git log --stat --author='2026:1602' --all` |
| When was a statute repealed, and by what? | `git log --diff-filter=D --format='%(trailers:key=In-force,valueonly,separator=) %an' -- 1961/281` |
| The text of a repealed statute | `git log -1 --format=%H --diff-filter=D -- 1961/281` then `git show <that>^:1961/281/1.md` |
| Which statutes cite the Copyright Act (1960:729)? | `git grep -l '(1960:729)'` |
| The list of statutes, with dates and status | [`laws.csv`](laws.csv) |

PowerShell: `$r = git log -1 --format=%H --diff-filter=D -- 1961/281; git show "$($r)^:1961/281/1.md"`.

Files: `<year>/<number>/` per statute (SFS 1962:700 is `1962/700/`; old numbers such as 1736:0123 2 are
`1736/0123-2/`). Paragrafs are `<n>.md`, or `kap-<k>/<n>.md` in statutes with chapters (`kap-3/1.md` is 3 kap. 1 §,
`3a.md` is 3 a §). Text before the first paragraf is `ingress.md`; the transitional provisions
(Övergångsbestämmelser, grouped under the number of each act that added them) are `overgang.md`, and
annexes after them `bilaga-<n>.md`. Headings (avdelning, kapitel, and the headings between paragrafs) are `#` lines at
the top of the paragraf that follows them. `index.txt` gives the reading order. Statutes whose source has no paragraf
markup keep paragrafs split where a stycke begins "<n> §".

## The dates, and which one we use

**Commits are dated by the day a text entered into force**, at noon Stockholm time (CET, or CEST in summer time), in
the `In-force:` trailer and as the commit date. Each statute's text is dated by the in-force day of the latest amending
act its consolidation includes, read from the SFSR register (`Date-source: sfsr-amendment`; the statute's own in-force
day if nothing amended it, `sfsr-in-force`; the issue day where the register gives no in-force day, `issued`). Dated
two-version provisions change on their printed day (`marker`); repeals on the Upphävd day (`upphavd`). Dates before
1970 are clamped to 1970-01-01 in git; the real day is in `In-force:`. `Issued:` is the day the statute was issued
(utfärdad).

`tools/in_force_on.py DATE PATH` reads the `In-force:` trailers, so it does not depend on your time zone (unlike
`git rev-list --before`). Before a statute's record starts it says there is nothing in force. Changes that take effect
after 2026-10-28 are on the `future` branch (485 commits; never merged into `main`). Provisions whose
day is "den dag som regeringen bestämmer" (not yet set) are not applied anywhere; they are listed in
[`data/undated.csv`](data/undated.csv) (623 markers).

## What "author" means

- **The publisher** (`Regeringskansliet SFST (via Riksdagen open data)`), `Kind: consolidated`: a statute's
  consolidated text, which many acts wrote. `Last-amended-by:` names the act whose in-force day dates it and
  `Amended-through:` the last act included. Use the per-paragraf notes and `data/amendments.csv` for who wrote what.
- **The statute itself** (`Brottsbalk (1962:700) <sfs-1962-700@sfs.invalid>`), `Kind: enacted`: a statute the
  register shows unamended.
- **An amending act** (`Lag (2026:1604) om ändring i brottsbalken <sfs-2026-1604@sfs.invalid>`), `Kind: amendment`:
  a dated two-version provision changing on its day; the act is the one named at the end of the new text.
- **A repealing act**, `Kind: repeal`: the statute's files are deleted on its repeal day.
- **The publisher**, `Kind: unexplained`: a dated change no single act can be named for (`Candidate:` lists the
  possibilities).

Commits by kind on `main`: amendment 3, consolidated 6669, enacted 4447, repeal 4416; on `future`: amendment 427, enacted 37, repeal 17, unexplained 4. Blame credits whole stycken; use
`git log -p --word-diff` for the words.

## What it does not show

- Any text before the current consolidation: no earlier wording of any statute.
- Changes to a statute that its consolidation does not yet include (the publisher's text can lag the register).
- Statutes the publisher prints with undatable two-version passages around chapters or headings: they are kept as
  printed, both versions, the second copy in a file named `~2` (`Text: as-printed`; text classes: as-printed 42, dated-versions 427, plain 10684).
- Agency regulations (myndighetsföreskrifter), EU law, case law; the 133 nineteenth-century Riksdag regulations in
  the same open-data series (OCR text, not SFS statutes).
- Tables and formatting (italics); statutes that could not be read (387 rows in
  [`data/unreadable.csv`](data/unreadable.csv)).

This is a research aid. The authoritative text is the one published in Svensk författningssamling.

## How it is checked

check_repo.py read back every version of every statute (11541 versions, 20276783 words, on `main` and `future`) and compared its letters and digits with the raw source: the plain-text field of the Riksdagen JSON for statutes without dated versions (text build.py never reads), and for statutes with dated two-version provisions the HTML through a separate minimal extractor, keeping the passages in force on the version's day: 0 mismatches. 15536 commits on main and 485 on future: 0 whose trailers git cannot parse, 0 without In-force, 0 merges, 0 In-force dates going backwards, 0 after the build day on main, 0 CR characters in messages. test_repo.py (blame, the on-date tool before and after dated amendments and a repeal, log --follow order, In-force on every commit, authorship, every README command): 6/6 passed. These results are from the previous build of the same cache; the rebuild that wrote this file was hash-identical.

## How it is built

`fetch.py` reads Riksdagen's document list (doktyp=sfs), each statute's `dokumentstatus` JSON (the consolidated text as
HTML and as plain text) and its SFSR page at rkrattsbaser.gov.se. `build.py` splits each text at its paragraf anchors,
dates it from the register, and writes everything with one `git fast-import` stream. No AI model takes part in
building or checking it. See [`REPRODUCE.md`](REPRODUCE.md).

## Licence

Källa: Sveriges riksdag (Source: The Swedish Parliament), Riksdagens öppna data, https://data.riksdagen.se,
whose terms read "Riksdagens öppna data är fria att användas och spridas vidare så länge du anger
källa." The consolidated texts are Regeringskansliet's (SFST, https://rkrattsbaser.gov.se), and the amendment register
is SFSR from the same site. This service is not provided or endorsed by Sveriges riksdag or Regeringskansliet. Statutes
carry no copyright in Sweden: upphovsrättslagen (1960:729) 1 kap. 9 §, as in this repository
(`1960/729/kap-1/9.md`): "9 § Upphovsrätt gäller inte till 1. författningar, 2. beslut av myndigheter, 3. yttranden av svenska myndigheter och 4. officiella översättningar av sådant som avses i 1-3. Upphovsrätt gäller dock till verk vilka ingår i en handling som avses i första stycket och är av följande slag: 1. kartor, 2. alster av bildkonst, 3. musikaliska verk eller 4. diktverk. Upphovsrätt gäller även till ett verk som ingår i en bilaga till ett beslut av en myndighet, om beslutet avser rätten att ta del av den allmänna handling där verket ingår. Lag (2000:92)." The wording was not changed; it was only split into files and lines. **This repository
is private.** Lexifina's own contributions (the tools, the arrangement and the commit messages) are CC0. See
[`LICENSE`](LICENSE).
