#!/usr/bin/env python3
"""Insert a sheet-music score (ABC/MusicXML/MEI or a ready PNG) into an HWP/HWPX
document as an inline (treat-as-char) picture, writing a NEW output file.

usage:
  insert_score.py DOC.hwp SCORE.abc -o OUT.hwp --after N [--section 0]
  insert_score.py DOC.hwp SCORE.png -o OUT.hwp --into N   # use existing empty para N
  options: --width HWPUNIT (default: body text width), --verify-dir DIR

--after N  : a new empty paragraph is inserted right after paragraph N
             (0-based, same numbering as `rhwp search` / `rhwp dump-pages` pi=)
             and the score goes there.  --after -1 puts it at the very start.
--into N   : put the picture at offset 0 of existing paragraph N (should be empty).
"""
import argparse, json, os, re, shutil, struct, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RHWP = shutil.which("rhwp") or os.path.expanduser("~/.local/bin/rhwp")
VENV_PY = os.path.expanduser("~/.cache/hwp-score/venv/bin/python")
RSVG = shutil.which("rsvg-convert") or "/opt/homebrew/bin/rsvg-convert"


def run(*args):
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"command failed: {' '.join(map(str, args))}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def png_size(path):
    with open(path, "rb") as f:
        return struct.unpack(">II", f.read(24)[16:24])


def body_width(doc, section):
    info = run(RHWP, "info", doc)
    m = re.search(rf"구역{section} 용지: (\d+)×(\d+).*?여백: 좌(\d+) 우(\d+)", info)
    if not m:
        sys.exit("could not read page size/margins from `rhwp info`")
    w, _, left, right = map(int, m.groups())
    return w - left - right


def picture_ctrl(doc, section, para, bin_id):
    """Index of the new picture among the paragraph's controls (a section's
    first paragraph also carries section/column-definition controls)."""
    inside = False
    for line in run(RHWP, "dump", doc).splitlines():
        m = re.match(r"--- 문단 (\d+)\.(\d+) ---", line)
        if m:
            inside = (int(m.group(1)), int(m.group(2))) == (section, para)
            continue
        m = re.match(r"\s+\[(\d+)\] 그림: bin_id=(\d+)", line)
        if inside and m and int(m.group(2)) == bin_id:
            return int(m.group(1))
    sys.exit(f"inserted picture (bin_id={bin_id}) not found in paragraph {para}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("score")
    ap.add_argument("-o", "--output", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--after", type=int)
    g.add_argument("--into", type=int)
    ap.add_argument("--section", type=int, default=0)
    ap.add_argument("--width", type=int, help="picture width in HWPUNIT")
    ap.add_argument("--align", choices=["left", "center", "right"], default="left",
                    help="alignment of the score paragraph (default left)")
    ap.add_argument("--verify-dir", help="render the result page(s) here as PNG")
    a = ap.parse_args()

    if os.path.abspath(a.output) == os.path.abspath(a.doc):
        sys.exit("refusing to overwrite the input document; choose a new -o path")
    if os.path.splitext(a.output)[1].lower() != os.path.splitext(a.doc)[1].lower():
        sys.exit("output extension must match input (.hwp -> .hwp, .hwpx -> .hwpx)")

    tmp = tempfile.mkdtemp(prefix="hwp-score-")
    ext = os.path.splitext(a.doc)[1]

    # 1. score -> PNG
    if a.score.lower().endswith((".png", ".jpg", ".jpeg")):
        png = a.score
    else:
        png = os.path.join(tmp, "score.png")
        out = run(VENV_PY, os.path.join(HERE, "score2png.py"), a.score, "-o", png)
        pages = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
        if len(pages) != 1:
            sys.exit(f"score rendered to {len(pages)} pages; insert them one by one: "
                     + ", ".join(p["png"] for p in pages))
    pw, ph = png_size(png)

    # 2. size: fit body width, keep aspect
    width = a.width or body_width(a.doc, a.section)
    height = round(width * ph / pw)

    # 3. target paragraph
    cur = a.doc
    if a.after is not None:
        para = a.after + 1
        nxt = os.path.join(tmp, "s1" + ext)
        run(RHWP, "edit", "insert-paragraph", cur, "--section", a.section,
            "--para", para, "-o", nxt, "--json")
        cur = nxt
        # the new paragraph inherits the previous one's paragraph shape: a heading's
        # "page break before" would put every score on its own page, a right-aligned
        # caption would push short lines to the right. (Compact JSON: rhwp ignores
        # --props that contain spaces.)
        nxt = os.path.join(tmp, "s1b" + ext)
        run(RHWP, "edit", "apply-para-format", cur, "--section", a.section, "--para", para,
            "--props", json.dumps({"pageBreakBefore": False, "alignment": a.align}, separators=(",", ":")),
            "-o", nxt, "--json")
        cur = nxt
    else:
        para = a.into

    # 4. insert picture, then make it inline (treat as char) so it sits in the
    #    text flow inside the margins instead of floating at paper x=0,y=0
    nxt = os.path.join(tmp, "s2" + ext)
    ins = json.loads(run(RHWP, "edit", "insert-picture", cur, "--image", png,
                         "--section", a.section, "--para", para, "--offset", 0,
                         "--width", width, "--height", height, "-o", nxt, "--json"))
    cur = nxt
    ctrl = picture_ctrl(cur, a.section, para, ins["binDataId"])
    run(RHWP, "edit", "set-picture", cur, "--section", a.section, "--para", para,
        "--ctrl", ctrl, "--props", json.dumps({"treatAsChar": True}, separators=(",", ":")),
        "-o", a.output, "--json")

    # 5. locate page and optionally render it
    page = gidx = sec = None
    for line in run(RHWP, "dump-pages", a.output).splitlines():
        m = re.match(r"=== 페이지 \d+ \(global_idx=(\d+), section=(\d+)", line)
        if m:
            gidx, sec = int(m.group(1)), int(m.group(2))
        elif sec == a.section and re.search(rf"Shape\s+pi={para} .*그림 tac=true", line):
            page = gidx
            break
    result = {"output": a.output, "section": a.section, "paragraph": para,
              "width": width, "height": height, "page": page}
    if a.verify_dir and page is not None:
        os.makedirs(a.verify_dir, exist_ok=True)
        run(RHWP, "export-svg", a.output, "-p", page, "-o", a.verify_dir)
        stem = os.path.splitext(os.path.basename(a.output))[0]
        # rhwp names it <stem>_<NNN>.svg, or <stem>.svg for a 1-page document
        svgs = [f for f in (f"{stem}_{page + 1:03d}.svg", f"{stem}.svg")
                if os.path.exists(os.path.join(a.verify_dir, f))]
        if svgs:
            out_png = os.path.join(a.verify_dir, f"page{page + 1}.png")
            run(RSVG, "-w", 900, "-b", "white", os.path.join(a.verify_dir, svgs[0]),
                "-o", out_png)
            result["preview"] = out_png
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
