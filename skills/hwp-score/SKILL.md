---
name: hwp-score
description: 한글(HWP/HWPX) 문서에 악보를 넣는다. ABC 악보(또는 MusicXML/MEI)를 verovio로 그려 PNG로 만들고 rhwp로 원하는 문단 뒤에 본문 글자처럼 취급(treat-as-char) 그림으로 끼운 뒤, 결과 쪽을 렌더링해 확인한다. Use when the user wants sheet music / 악보 / 계이름 / 동요·가곡 악보 / 가사 붙은 악보를 HWP·HWPX 문서(과제, 학습지, 교안 등)에 삽입하려 할 때. Also cuts an existing score PDF / scan / image into one picture per system (줄·보표 단위로 자르기) and inserts them in order, and stitches chosen measures (마디) from different lines into one line with the clef in front (마디 이어붙이기, 그림판 노가다 자동화) — use for '악보 PDF를 줄별로 잘라서 한글에 넣어줘', 악보 캡처·자르기 노가다 자동화. Always writes a new output file.
---

# hwp-score — HWP/HWPX 문서에 악보 넣기

Chain: ABC or MusicXML → **verovio** (Python) SVG → **rsvg-convert** PNG → **rhwp**
`edit insert-picture` + `edit set-picture {"treatAsChar":true}` → render check with
`rhwp export-svg`. Everything runs locally on macOS; Hancom 한글 does not need to be open.

## Rules

- **Never overwrite the user's document.** Always write `-o <이름>_악보.hwp` (same
  extension as the input: .hwp→.hwp, .hwpx→.hwpx). The script refuses `-o` == input.
- Always look at the rendered preview page before telling the user it's done.
- Keep the `.abc` file next to the output so the user can tweak and re-run.

## Setup (once; check before first use)

`<skill-dir>` below = this skill's **Base directory** (shown when the skill loads).
Run the setup script once per machine; it is idempotent and prints what it did:

```bash
bash <skill-dir>/scripts/setup.sh
```

It creates the Python venv `~/.cache/hwp-score/venv` (verovio, pypdfium2, numpy,
pillow), installs `librsvg` with Homebrew if `rsvg-convert` is missing, and installs the
`rhwp` CLI (github.com/edwardkim/rhwp release, SHA-256 checked) to `~/.local/bin/rhwp`
if it is not on PATH. Ask the user before running it the first time (it installs
software). Do **not** use cairosvg (needs libcairo) or PySide6/QtSvg (can't render
verovio's nested SVG) — librsvg is the rasterizer.

## Fast path (one command)

```bash
python3 <skill-dir>/scripts/insert_score.py IN.hwp song.abc -o IN_악보.hwp --after N --verify-dir /tmp/score-check
```

- `--after N` — inserts a new empty paragraph after paragraph N and puts the score in it
  (`--after -1` = top of the section). `--into N` — use an existing (empty) paragraph N.
- `--section S` (default 0), `--width HWPUNIT` (default: page width − left − right
  margin read from `rhwp info`; height keeps the PNG aspect ratio).
- The score input may also be a ready `.png`/`.jpg`.
- Prints JSON: `output, paragraph, width, height, page (0-based), preview`. Then
  **Read the `preview` PNG** to confirm the score sits inside the margins between the
  right paragraphs.

## Existing score PDF / scan / image → cut per system (줄 단위) → insert

For a score the user already has (PDF from IMSLP/MuseScore/a publisher, phone photo,
scanned page, screenshot) — replaces "capture, crop each line, paste, resize" by hand.

```bash
python3 <skill-dir>/scripts/insert_split.py IN.hwp score.pdf -o IN_악보.hwp --after N --verify-dir /tmp/split-check
```

- Multiple inputs are taken in order (`score.pdf p2.jpg p3.jpg`). `--pages 1-2,4` limits PDF pages.
- One inline picture paragraph per **system** (a staff, or staves joined by a brace /
  system barline — piano grand staff, choir SATB stay together). `--per-staff` cuts every
  staff separately instead.
- One common scale: the widest system fills the body width (`--width` to override); a
  short last line keeps its real length. Systems flow across pages and are never split.
- Page header/title above the first system and footer/page number below the last are
  dropped; `--keep-title` keeps the header with the first system. Lyrics, dynamics,
  ledger-line notes and measure numbers stay with their system (cut = middle of the
  blank band between systems).
- Scans: tilt up to ±3° is corrected, speckle removed, grey paper whitened.
- Prints JSON with each system's `paragraph`/`page` and `previews` — **Read the previews**
  and check the count of systems matches the source before reporting done.
- Just the crops (no HWP): `~/.cache/hwp-score/venv/bin/python <skill-dir>/scripts/split_score.py score.pdf -o crops/`
  (or `--keep-crops DIR` on insert_split) — e.g. to hand-pick lines, then insert single
  PNGs with `insert_score.py`.
- Needs `pypdfium2 numpy pillow` in the venv:
  `~/.cache/hwp-score/venv/bin/pip install pypdfium2 numpy pillow`.
- If it reports `no staves found` or merges/misses staves: very faint/low-res scan → raise
  `--dpi 300`; heavily broken staff lines → `--min-span 0.06`; strongly skewed photo
  (>3°) or perspective-distorted phone shot → ask the user for a flatter scan. Only
  standard 5-line staves are detected (no TAB-only or percussion 1-line staves).

- Markings between systems (pedal Ped./✱, lyrics, dynamics, volta brackets, measure
  numbers) go with the staff they hug: cut = widest blank between ink blocks, ties go
  to the upper system. The first system loses the page header **and** any tempo text
  above it unless `--keep-title`. Small cue/ossia staves (spacing ≠ the page's staves)
  are ignored.

Tested: real engraved PDFs from mutopiaproject.org — Für Elise
(3 p, 19 systems, beams fused into staff lines, pedal lines), Rondo alla Turca (5 p, 26),
Bach Prelude C (2 p, 12), Anna Magdalena Minuet (1 p, 6): every system found, 2 staves
each, in order → inserted into an HWPX copy; plus synthetic grand-staff/lyrics PDF and a
1.2°-tilted noisy JPEG scan.

## Measures (마디): pick bars, stitch bars from several lines into one line

Automates the "아크로뱃 스냅숏 → 그림판에서 마디 잘라 이어붙이기" chore, e.g. "5–6마디(윗줄)
+ 7–8마디(아랫줄)를 한 줄로", "4마디씩 한 줄로 다시 배치".

1. **Number the measures and look at the preview first** (always):
   ```bash
   ~/.cache/hwp-score/venv/bin/python <skill-dir>/scripts/measures.py score.pdf --list --preview /tmp/mprev [--first-measure 0]
   ```
   Read the `*-measures.png` previews: every red box = one measure with its **red id**;
   the green box = the line's clef/key header. Ids count boxes in reading order.
   Compare with the **printed** bar numbers at each line start (small number above
   the clef): editions number 1st/2nd endings (1. 2.) and mid-bar repeat signs
   differently, so red ids can drift from printed numbers after such spots. When the
   user names printed bar numbers, translate them to red ids from the preview.
   `--first-measure 0` when the piece starts with a pickup (못갖춘마디).
2. **Stitch & insert** (one inline picture per resulting line, common scale):
   ```bash
   python3 <skill-dir>/scripts/insert_split.py IN.hwpx score.pdf -o OUT.hwpx --after N --first-measure 0 --measures "4-7"          # one line
   python3 <skill-dir>/scripts/insert_split.py IN.hwpx score.pdf -o OUT.hwpx --after N --measures "1-4;5-8;9-12"                # several lines
   python3 <skill-dir>/scripts/insert_split.py IN.hwpx score.pdf -o OUT.hwpx --after N --measures 1-16 --reflow 4               # 4 bars per line
   ```
   Just the PNGs: `measures.py score.pdf --take 4-7 -o line.png` (or `--reflow 4`).
3. Read the stitched PNG / preview page before reporting.

What the stitcher does: renders at 300 dpi (blog tip: 200–300% zoom), puts the clef +
key signature in front (the time signature and tempo only when the line starts the
piece), drops the borrowed line's printed bar number, aligns **each staff separately**
(treble–bass distance differs between lines), keeps markings that belong to each bar
(pedal, fingering, dynamics). Barlines = vertical strokes across every staff of the
line; where the edition draws them through the gap between piano staves that is
required (stems never do), otherwise they must stop at the staff (stems stick out).
Limits: all picked measures must come from one score with the same number of staves
per line; clef/key changes inside the range aren't re-checked (the header is the first
line's); a volta bracket hook or slur end may leave a small stray mark at a cut.

Tested on mutopia PDFs: Bach Minuet BWV Anh.114 → 32 measures, Bach Prelude BWV 846
→ 35 (both exact); Für Elise 4–7 (across a line break) and reflowed lines → HWPX.

## Writing the ABC

```abc
X:1
T:작은 별
C:모차르트            % optional composer (shown right)
M:4/4
L:1/4                 % default note length
Q:1/4=100             % optional tempo
K:C                   % key: C, G, F, D, Bb, Am, Em ...
C C G G | A A G2 | F F E E | D D C2 |
w:반 짝 반 짝 작 은 별 아 름 답 게 비 치 네
G G F F | E E D2 | G G F F | E E D2 |
w:동 쪽 하 늘 에 서 도 서 쪽 하 늘 에 서 도
```

- Notes: `C D E F G A B` = 도~시 (middle octave), `c d e…` = one octave up, `c'` higher,
  `C,` lower. `^F` sharp, `_B` flat, `=F` natural. `z` rest.
- Lengths (relative to `L:`): `C2` double, `C/2` or `C/` half, `C3/2` dotted, `C>D` dotted
  pair. Bars `|`, end `|]`, repeats `|: … :|`, first/second endings `|1 … :|2 …`.
  Ties `C-C`, slurs `(CDE)`, chords `[CEG]`, triplet `(3CDE)`.
- **Korean lyrics: one `w:` line right after each music line**, one syllable per note,
  separated by spaces. `-` splits a syllable across notes (`사-랑`), `_` holds the previous
  syllable over the next note (melisma), `*` skips a note, `|` jumps to the next bar.
  For a second verse add another `w:` line directly below the first.
- Line breaks in the ABC = system (staff line) breaks in the picture; 4 bars per line
  reads well on A4.
- Two voices / piano: `V:1` / `V:2 clef=bass` headers with `[V:1]` … `[V:2]` lines.
- If verovio rejects the file the script prints verovio's log — fix the ABC, re-run.
- MusicXML (`.musicxml`/`.xml`/`.mxl`, e.g. exported from MuseScore) and MEI also work.

## Choosing the insertion paragraph

```bash
~/.local/bin/rhwp search IN.hwp "검색할 문구" --json        # gives section/paragraph/page
~/.local/bin/rhwp dump-pages IN.hwp -p PAGE                  # pi=<paragraph>, text preview per page
```

Pick the paragraph the score should follow (e.g. the "다음 악보를 보고…" question) and
pass it as `--after`. Paragraph numbers are 0-based and shift by one after each insert,
so when adding several scores, go **from the bottom of the document upward** or re-run
`dump-pages` on the new file. `--after` inherits the previous paragraph's style (like
pressing Enter in 한글); if that paragraph has a large left indent, pass a smaller
`--width` so the picture doesn't overflow.

## Manual / MCP path (what the script does)

1. PNG: `~/.cache/hwp-score/venv/bin/python <skill-dir>/scripts/score2png.py song.abc -o song.png`
   (verovio options pageWidth 2100, adjustPageHeight, scale 45, footer none; `rsvg-convert -w 1800 -b white`).
   A multi-page score is written as `song-1.png`, `song-2.png`… — insert each.
2. Width W = paper width − left − right margin from `rhwp info` (A4, 30 mm sides →
   42520; 20 mm sides → 48190). H = W × png_h / png_w.
3. `rhwp edit insert-paragraph IN.hwp --para N+1 -o s1.hwp` (MCP: `hwp_insert_paragraph`).
4. `rhwp edit insert-picture s1.hwp --image song.png --para N+1 --offset 0 --width W --height H -o s2.hwp --json`
   (MCP: `hwp_insert_picture`). Note the `binDataId` in the result.
5. **Make it inline**: the picture is inserted floating at paper x=0, y=0 (top-left corner
   of the page, outside the margins). Fix with
   `rhwp edit set-picture s2.hwp --section 0 --para N+1 --ctrl K --props '{"treatAsChar":true}' -o OUT.hwp`
   (MCP: `hwp_set_picture`). K is the picture's control index in that paragraph — 0 for a
   fresh paragraph, but a section's first paragraph also holds section/column controls
   (there it was 2): find the `[K] 그림: bin_id=<binDataId>` line under
   `--- 문단 S.P ---` in `rhwp dump s2.hwp`. Don't try to fix it with x/y — treat-as-char
   makes it flow with the text and push following paragraphs down.

## Verify

```bash
~/.local/bin/rhwp dump-pages OUT.hwp -p PAGE | grep "그림 tac=true"
~/.local/bin/rhwp export-svg OUT.hwp -p PAGE -o /tmp/score-check
rsvg-convert -w 900 -b white /tmp/score-check/OUT_00{PAGE+1}.svg -o /tmp/score-check/page.png   # single-page docs: OUT.svg
```

Read the PNG. Boxes reading `FF A0` in the preview are rhwp's renderer missing a glyph
for no-break spaces already in the document — they're in the untouched original too
and do not appear in 한글.

Tested: `<skill-dir>/examples/twinkle.abc` into a 35-page HWP (20 mm margins), an HWPX, and a
blank HWP (30 mm margins); the score renders inline inside the margins in all three.
