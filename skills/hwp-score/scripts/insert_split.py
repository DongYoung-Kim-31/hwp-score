#!/usr/bin/env python3
"""Cut a score PDF/image into systems (줄 단위) and insert them into an HWP/HWPX in order.

  python3 insert_split.py IN.hwp score.pdf [more.png ...] -o OUT.hwp --after N
      [--pages 1-2] [--per-staff] [--keep-title] [--staff-gap MM] [--width HWPUNIT]
      [--keep-crops DIR] [--verify-dir DIR]

Each system becomes its own inline (treat-as-char) picture paragraph, placed one after
another starting after paragraph N. Pictures are sized by their staff spacing, not by
their pixel width: every picture is scaled so the distance between two staff lines is
one gap in the document, so lines cut from PDFs, scans and photos of different
resolution all get the same staff size.

Which gap: --staff-gap MM if given; else the gap of the score pictures earlier runs
already put into this document (each inserted PNG carries its staff spacing in a PNG
text chunk, read back by doc_scores.py); else 1.3 mm (staff height 5.2 mm). If a line
would then be wider than the body (or --width), the gap is lowered until it fits and
the score pictures already in the document are resized to the same gap, so every
score in the document keeps one staff size.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import insert_score as ins  # noqa: E402  (reuses VENV_PY, body_width, png_size)
import doc_scores as ds  # noqa: E402  (PNG staff tag; reading it back needs the venv)

DEFAULT_GAP_MM = 1.3          # ≈ the ABC (verovio) pictures on A4 with 30 mm margins
HWPUNIT_PER_MM = 7200 / 25.4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("inputs", nargs="+", help="score PDF(s) / image(s), in reading order")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--after", type=int, required=True,
                    help="insert after this paragraph (0-based; -1 = top of section)")
    ap.add_argument("--section", type=int, default=0)
    ap.add_argument("--staff-gap", type=float,
                    help="distance between two staff lines in the document, mm (default: "
                         f"that of scores already in the document, else {DEFAULT_GAP_MM})")
    ap.add_argument("--no-match", action="store_true",
                    help="don't look at / resize score pictures already in the document")
    ap.add_argument("--width", type=int,
                    help="max picture width in HWPUNIT (default: body text width)")
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
        systems = [{"png": o["png"], "page": None, "staves": None, "measures": o["measures"],
                    "sp": o.get("sp")}
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
    existing = [] if a.no_match else json.loads(
        ins.run(ins.VENV_PY, os.path.join(HERE, "doc_scores.py"), a.doc))
    if a.staff_gap:
        want_mm = a.staff_gap
    elif existing:
        want_mm = sorted(e["gap_mm"] for e in existing)[len(existing) // 2]
    else:
        want_mm = DEFAULT_GAP_MM
    gap = want_mm * HWPUNIT_PER_MM
    sizes = [ins.png_size(s["png"])[0] for s in systems]
    if all(s.get("sp") for s in systems):
        widths = [pw * gap / s["sp"] for pw, s in zip(sizes, systems)]
    else:  # no staff spacing known: old behaviour, one pixel scale for the run
        widths = [pw * body / max(sizes) for pw in sizes]
        gap = None
    shrink = min(1.0, body / max(widths))
    widths = [round(w * shrink) for w in widths]
    used_gap_mm = round(gap * shrink / HWPUNIT_PER_MM, 3) if gap else None

    cur, after, placed = a.doc, a.after, []
    # bring score pictures from earlier runs to the same staff gap (resizing doesn't
    # renumber paragraphs, so their addresses from doc_scores stay valid)
    resized = []
    if gap:
        for k, e in enumerate(existing):
            w = round(e["png_w"] * used_gap_mm * HWPUNIT_PER_MM / e["sp"])
            if abs(w - e["width"]) <= 0.005 * e["width"]:
                continue
            h = round(e["height"] * w / e["width"])
            nxt = os.path.join(tmp, f"resize{k}{ext}")
            ins.run(ins.RHWP, "edit", "set-picture", cur, "--section", e["section"],
                    "--para", e["paragraph"], "--ctrl", e["ctrl"],
                    "--props", json.dumps({"width": w, "height": h}), "-o", nxt, "--json")
            resized.append({"section": e["section"], "paragraph": e["paragraph"],
                            "from_mm": e["gap_mm"], "to_mm": used_gap_mm})
            cur = nxt
    for k, (s, width) in enumerate(zip(systems, widths), 1):
        nxt = os.path.join(tmp, f"step{k}{ext}")
        png = s["png"]
        if s.get("sp"):   # tag the picture with its staff spacing for later runs
            png = os.path.join(tmp, f"tagged{k}.png")
            ds.add_png_tag(s["png"], png, s["sp"])
        out = subprocess.run([sys.executable, os.path.join(HERE, "insert_score.py"), cur,
                              png, "-o", nxt, "--after", str(after),
                              "--section", str(a.section), "--width", str(width),
                              "--align", a.align],
                             capture_output=True, text=True)
        if out.returncode:
            sys.exit(f"system {k} ({s['png']}): {out.stderr.strip() or out.stdout.strip()}")
        r = json.loads(out.stdout.strip().splitlines()[-1])
        placed.append({"system": k, "source_page": s["page"], "staves": s["staves"],
                       "measures": s.get("measures"), "staff_px": s.get("sp"),
                       "paragraph": r["paragraph"], "page": r["page"], "width": r["width"]})
        cur, after = nxt, r["paragraph"]
    shutil.copyfile(cur, a.output)

    result = {"output": a.output, "staff_gap_mm": used_gap_mm,
              "resized_existing": resized, "systems": placed,
              "pages_without_staves": split["pages_without_staves"]}
    if gap and shrink < 1.0:
        result["note"] = (f"widest line did not fit the body at {want_mm} mm, so all scores "
                          f"use {used_gap_mm} mm; for bigger staves split long lines "
                          "(--measures ... / --reflow N)")
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
