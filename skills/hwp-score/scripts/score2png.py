#!/usr/bin/env python3
"""Render an ABC / MusicXML / MEI score to PNG for pasting into HWP.

usage: score2png.py INPUT [-o OUT.png] [--width 1800] [--svg OUT.svg]

Uses verovio (Python, from ~/.cache/hwp-score/venv) for SVG and
rsvg-convert (Homebrew librsvg) for rasterizing. Multi-page scores are
written as OUT-1.png, OUT-2.png, ...  Prints one JSON line per page:
{"png": path, "width_px": w, "height_px": h}
"""
import argparse, json, os, shutil, struct, subprocess, sys, tempfile

try:
    import verovio
except ImportError:
    sys.exit("verovio not found: run with ~/.cache/hwp-score/venv/bin/python "
             "(setup: python3 -m venv ~/.cache/hwp-score/venv && "
             "~/.cache/hwp-score/venv/bin/pip install verovio)")


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--width", type=int, default=1800, help="PNG width in px")
    ap.add_argument("--svg", help="also keep the SVG at this path")
    ap.add_argument("--scale", type=int, default=45)
    ap.add_argument("--page-width", type=int, default=2100)
    a = ap.parse_args()

    rsvg = shutil.which("rsvg-convert") or "/opt/homebrew/bin/rsvg-convert"
    if not os.path.exists(rsvg):
        sys.exit("rsvg-convert not found: brew install librsvg")

    out = a.output or os.path.splitext(a.input)[0] + ".png"
    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": a.page_width, "adjustPageHeight": True,
                   "scale": a.scale, "footer": "none"})
    if not tk.loadFile(os.path.abspath(a.input)):
        sys.exit(f"verovio could not parse {a.input}:\n{tk.getLog()}")
    n = tk.getPageCount()
    base, ext = os.path.splitext(out)
    with tempfile.TemporaryDirectory() as tmp:
        for p in range(1, n + 1):
            svg = tk.renderToSVG(p)
            svg_path = os.path.join(tmp, f"p{p}.svg")
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg)
            if a.svg:
                s = a.svg if n == 1 else f"{os.path.splitext(a.svg)[0]}-{p}.svg"
                shutil.copy(svg_path, s)
            png = out if n == 1 else f"{base}-{p}{ext}"
            subprocess.run([rsvg, "-w", str(a.width), "-b", "white",
                            "-o", png, svg_path], check=True)
            w, h = png_size(png)
            print(json.dumps({"png": png, "width_px": w, "height_px": h}))


if __name__ == "__main__":
    main()
