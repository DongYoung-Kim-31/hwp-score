#!/usr/bin/env python3
"""Cut a score PDF/image into systems (줄 단위) and insert them into an HWP/HWPX in order.

  python3 insert_split.py IN.hwp score.pdf [more.png ...] -o OUT.hwp --after N
      [--pages 1-2] [--per-staff] [--keep-title] [--width HWPUNIT] [--keep-crops DIR]
      [--verify-dir DIR]

Each system becomes its own inline (treat-as-char) picture paragraph, placed one after
another starting after paragraph N. All systems share one scale: the widest system
fills the body width (or --width) and shorter ones (e.g. the last line of a piece) keep
their true relative size instead of being stretched.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import insert_score as ins  # noqa: E402  (reuses VENV_PY, body_width, png_size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("inputs", nargs="+", help="score PDF(s) / image(s), in reading order")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--after", type=int, required=True,
                    help="insert after this paragraph (0-based; -1 = top of section)")
    ap.add_argument("--section", type=int, default=0)
    ap.add_argument("--width", type=int, help="width of the widest system in HWPUNIT")
    ap.add_argument("--align", choices=["left", "center", "right"], default="left")
    ap.add_argument("--pages", help="PDF pages to use, e.g. 1-2,4")
    ap.add_argument("--per-staff", action="store_true")
    ap.add_argument("--group", type=int, default=1, help="N lines (systems) per picture")
    ap.add_argument("--keep-title", action="store_true")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--keep-crops", help="also save the cropped system PNGs here")
    ap.add_argument("--measures", help='insert stitched measure ranges instead of whole lines: '
                    '"5-8" or "1-4;5-8" (see measures.py; numbers = red ids in its preview)')
    ap.add_argument("--reflow", type=int, help="with --measures (or alone): N measures per line")
    ap.add_argument("--first-measure", type=int, default=1, help="number of the first measure")
    ap.add_argument("--verify-dir", help="render pages showing the inserted systems here")
    a = ap.parse_args()

    if os.path.abspath(a.output) == os.path.abspath(a.doc):
        sys.exit("refusing to overwrite the input document; choose a new -o path")
    ext = os.path.splitext(a.doc)[1]
    if os.path.splitext(a.output)[1].lower() != ext.lower():
        sys.exit("output extension must match input (.hwp -> .hwp, .hwpx -> .hwpx)")

    tmp = tempfile.mkdtemp(prefix="hwp-split-")
    crops = a.keep_crops or os.path.join(tmp, "crops")
    if a.measures or a.reflow:
        os.makedirs(crops, exist_ok=True)
        cmd = [ins.VENV_PY, os.path.join(HERE, "measures.py"), *a.inputs,
               "--first-measure", str(a.first_measure), "-o", os.path.join(crops, "line.png")]
        if a.measures:
            cmd += ["--take", a.measures]
        if a.reflow:
            cmd += ["--reflow", str(a.reflow)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            sys.exit(p.stderr.strip() or p.stdout.strip())
        m = json.loads(p.stdout)
        systems = [{"png": o["png"], "page": None, "staves": None, "measures": o["measures"]}
                   for o in m["outputs"]]
        split = {"systems": systems, "pages_without_staves": []}
    else:
        cmd = [ins.VENV_PY, os.path.join(HERE, "split_score.py"), *a.inputs, "-o", crops,
               "--dpi", str(a.dpi)]
        if a.pages:
            cmd += ["--pages", a.pages]
        if a.per_staff:
            cmd.append("--per-staff")
        if a.keep_title:
            cmd.append("--keep-title")
        if a.group > 1:
            cmd += ["--group", str(a.group)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            sys.exit(p.stderr.strip() or p.stdout.strip())
        split = json.loads(p.stdout)
        systems = split["systems"]

    body = a.width or ins.body_width(a.doc, a.section)
    max_px = max(ins.png_size(s["png"])[0] for s in systems)
    scale = body / max_px

    cur, after, placed = a.doc, a.after, []
    for k, s in enumerate(systems, 1):
        pw, _ = ins.png_size(s["png"])
        nxt = os.path.join(tmp, f"step{k}{ext}")
        out = subprocess.run([sys.executable, os.path.join(HERE, "insert_score.py"), cur,
                              s["png"], "-o", nxt, "--after", str(after),
                              "--section", str(a.section), "--width", str(round(pw * scale)),
                              "--align", a.align],
                             capture_output=True, text=True)
        if out.returncode:
            sys.exit(f"system {k} ({s['png']}): {out.stderr.strip() or out.stdout.strip()}")
        r = json.loads(out.stdout.strip().splitlines()[-1])
        placed.append({"system": k, "source_page": s["page"], "staves": s["staves"],
                       "measures": s.get("measures"),
                       "paragraph": r["paragraph"], "page": r["page"], "width": r["width"]})
        cur, after = nxt, r["paragraph"]
    shutil.copyfile(cur, a.output)

    result = {"output": a.output, "systems": placed,
              "pages_without_staves": split["pages_without_staves"]}
    if a.verify_dir:
        os.makedirs(a.verify_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(a.output))[0]
        previews = []
        for page in sorted({x["page"] for x in placed if x["page"] is not None}):
            ins.run(ins.RHWP, "export-svg", a.output, "-p", page, "-o", a.verify_dir)
            for f in (f"{stem}_{page + 1:03d}.svg", f"{stem}.svg"):
                svg = os.path.join(a.verify_dir, f)
                if os.path.exists(svg):
                    png = os.path.join(a.verify_dir, f"page{page + 1}.png")
                    ins.run(ins.RSVG, "-w", 900, "-b", "white", svg, "-o", png)
                    previews.append(png)
                    break
        result["previews"] = previews
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
