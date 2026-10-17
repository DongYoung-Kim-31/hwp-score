#!/usr/bin/env python3
"""Find measures (마디) in a score PDF/image and stitch any measure range into one line.

Automates the "그림판에서 마디 이어붙이기" chore: take bars 5–6 from one line and 7–8
from the next, put the clef + key signature in front, and get one clean line image.

Run with ~/.cache/hwp-score/venv/bin/python.

  # 1. number the measures and check the numbering on preview images
  measures.py score.pdf --list --preview DIR [--first-measure 0]
  # 2. stitch measures into one line (several ranges -> several lines)
  measures.py score.pdf --take 5-8 -o line.png
  measures.py score.pdf --take "1-4;5-8;9-12" -o line.png      # line-1.png, line-2.png …
  measures.py score.pdf --reflow 4 [--take 1-16] -o line.png    # re-flow N bars per line

Numbering counts every measure in reading order (pages, then systems, then barlines).
A score that starts with a pickup (못갖춘마디) usually numbers it 0: pass
--first-measure 0. Check the preview — printed measure numbers at the start of
each line (e.g. "6", "11") must match the red numbers.

Prints JSON. Then insert each PNG with insert_score.py (one inline picture per line).
"""
import argparse, json, os, sys
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import split_score as ss  # noqa: E402


def runs(mask):
    return ss.runs(mask)


def staff_line_rows(st):
    rows = set()
    for k in range(5):
        c = st["top"] + k * st["sp"] + 1
        for d in range(-2, 3):
            rows.add(int(round(c + d)))
    return rows


def find_barlines(ink, staves, slant=0):
    """x-ranges of barlines: thin vertical strokes covering every staff of the system
    and not sticking out above/below it (stems do). slant>0 widens each column by
    +-slant px so leaning hand-drawn or photographed barlines still count."""
    if slant:
        wide = ink.copy()
        for d in range(1, slant + 1):
            wide[:, d:] |= ink[:, :-d]
            wide[:, :-d] |= ink[:, d:]
    else:
        wide = ink
    cover = 0.8 if slant else 0.9
    x0 = min(st["x0"] for st in staves)
    x1 = max(st["x1"] for st in staves)
    sp = max(st["sp"] for st in staves)
    ok = np.ones(ink.shape[1], bool)
    ok[:x0] = False
    ok[x1 + 1:] = False
    for st in staves:
        body = wide[st["top"]:st["bottom"] + 1]
        ok &= body.mean(axis=0) >= cover
    top, bot = staves[0]["top"], staves[-1]["bottom"]
    above = ink[max(0, int(top - 0.7 * sp)):max(0, int(top - 0.2 * sp))]
    below = ink[int(bot + 0.2 * sp):int(bot + 0.7 * sp)]
    clean_above = above.mean(axis=0) <= 0.35 if above.size else np.ones_like(ok)
    clean_below = below.mean(axis=0) <= 0.35 if below.size else np.ones_like(ok)
    # Some gaps between staves carry barlines through them (piano span bars), others
    # don't (voice above piano, separate instrument groups). Require the barline to
    # cross exactly those gaps where this line's barlines do - stems never cross.
    span = np.ones_like(ok)
    spanned = 0
    for k in range(len(staves) - 1):
        gap = wide[staves[k]["bottom"] + 1:staves[k + 1]["top"]]
        if not gap.size:
            continue
        g = gap.mean(axis=0) >= cover
        if len(runs(ok & g)) >= 2:
            span &= g
            spanned += 1
    if spanned:
        ok &= span
    else:
        # stems stick out above or below the staff; barlines stop at it
        ok &= clean_above & clean_below
    cols = runs(ok)
    # merge strokes of double / final / repeat barlines (thin+thick, dots) into one
    merged = []
    for a, b in cols:
        if b - a > sp:          # far too thick for a barline: a beam/blob, ignore
            continue
        if merged and a - merged[-1][1] <= 1.2 * sp:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged, x0, x1, sp


def notation_mask(ink, staves, sp):
    """ink in and around the staves with the staff lines removed (rows that are ink
    across most of the line are staff lines; measured, not computed, so line
    thickness / anti-aliasing doesn't leave residue)"""
    x0 = min(st["x0"] for st in staves)
    x1 = max(st["x1"] for st in staves)
    keep = np.zeros(ink.shape[0], bool)
    for st in staves:
        keep[max(0, st["top"] - int(sp)):st["bottom"] + int(sp) + 1] = True
    linefrac = ink[:, x0:x1 + 1].mean(axis=1)
    keep &= linefrac < 0.5
    return ink[keep]


def header_end(ink, staves, start, limit, sp):
    """End x of the clef + key signature (+ time signature) at the start of a line.

    The widest blank gap between 2.5 and 10 staff spaces after the line start: the
    space before the first note is wider than the gaps inside a key signature or
    between clef and key."""
    nm = notation_mask(ink, staves, sp)
    lo, hi = start + int(2.5 * sp), min(limit, start + int(10 * sp))
    if hi - lo < 3:
        return None
    prof = nm[:, lo:hi].sum(axis=0) > 0
    gaps = [(b - a, a, b) for a, b in runs(~prof)]
    gaps = [g for g in gaps if g[0] >= max(3, int(0.4 * sp))]
    if not gaps:
        return None
    w, a, b = max(gaps)
    return lo + (a + b) // 2


def analyse(inputs, dpi, first_measure, keep_title=False):
    pages = []
    n = first_measure
    measures = []
    for path in inputs:
        for pno, page in enumerate(ss.load_pages(path, dpi), 1):
            gray, ink, paper, systems = ss.detect_systems(page, keep_title=keep_title)
            pi = len(pages)
            pages.append({"src": os.path.basename(path), "page": pno, "gray": gray,
                          "ink": ink, "paper": paper, "systems": systems})
            for si, sy in enumerate(systems):
                bars, x0, x1, sp = find_barlines(ink, sy["staves"])
                if len(sy["staves"]) > 1:
                    # leaning / wobbly barlines (manuscripts, phone photos). Only where
                    # barlines must also cross the gap between staves - on a single
                    # staff a widened search mistakes stems for barlines.
                    tb = find_barlines(ink, sy["staves"], slant=max(2, int(0.25 * sp)))[0]
                    if len(tb) > len(bars):
                        bars = tb
                start = x0
                if bars and bars[0][0] - x0 <= 1.0 * sp:      # system start line
                    start = bars[0][1]
                    bars = bars[1:]
                limit = bars[0][0] if bars else x1
                he = header_end(ink, sy["staves"], start, limit, sp) or start
                sy.update({"bars": bars, "x0": x0, "x1": x1, "header": (x0, he)})
                lefts = [he] + [b for _, b in bars]
                rights = [b for _, b in bars]
                inner_r = [a_ for a_, _ in bars]   # where the closing barline starts
                if not rights or x1 - rights[-1] > 1.5 * sp:   # line ends without barline
                    rights.append(x1 + 1)
                    inner_r.append(x1 + 1)
                content = notation_mask(ink, sy["staves"], sp)
                first_in_line = True
                for j, (l, r, ri) in enumerate(zip(lefts, rights, inner_r)):
                    if r - l < sp:                               # sliver after final bar
                        continue
                    if r - l < 2.5 * sp:
                        # far too narrow for a measure: a stem or the header's tail was
                        # taken for a barline. At the line start it belongs to the
                        # header, elsewhere to the previous measure.
                        if first_in_line:
                            sy["header"] = (sy["header"][0], int(r))
                        elif measures and measures[-1]["system"] == si and measures[-1]["page_index"] == pi:
                            measures[-1]["x"][1] = int(r)
                        continue
                    # empty gap, e.g. between the clef and a start-repeat barline |:
                    # (look only between the barlines, not at their strokes and dots)
                    inner = content[:, int(l + 0.5 * sp):int(ri - 0.5 * sp)]
                    if inner.size == 0 or inner.sum() < sp:
                        continue
                    measures.append({"n": n, "page_index": pi, "src": pages[pi]["src"],
                                     "page": pno, "system": si, "x": [int(l), int(r)]})
                    first_in_line = False
                    n += 1
    return pages, measures


def preview(pages, measures, outdir):
    os.makedirs(outdir, exist_ok=True)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    files = []
    for pi, pg in enumerate(pages):
        im = pg["gray"].convert("RGB")
        d = ImageDraw.Draw(im)
        for sy in pg["systems"]:
            hx0, hx1 = sy["header"]
            d.rectangle([hx0, sy["staves"][0]["top"], hx1, sy["staves"][-1]["bottom"]],
                        outline=(0, 160, 0), width=2)
        for m in measures:
            if m["page_index"] != pi:
                continue
            sy = pg["systems"][m["system"]]
            l, r = m["x"]
            y = sy["staves"][0]["top"]
            d.rectangle([l, y - 4, r, sy["staves"][-1]["bottom"] + 4], outline=(220, 0, 0), width=2)
            d.text((l + 4, y - 34), str(m["n"]), fill=(220, 0, 0), font=font)
        f = os.path.join(outdir, f"{os.path.splitext(pg['src'])[0]}-p{pg['page']}-measures.png")
        im.save(f)
        files.append(f)
    return files


def stitch(pages, measures, nums, out_png):
    by_n = {m["n"]: m for m in measures}
    missing = [k for k in nums if k not in by_n]
    if missing:
        raise SystemExit(f"measures not found: {missing} (detected {measures[0]['n']}–{measures[-1]['n']})")
    picks = [by_n[k] for k in nums]
    ref_pg = pages[picks[0]["page_index"]]
    ref = ref_pg["systems"][picks[0]["system"]]
    nst = len(ref["staves"])
    sp = ref["sp"]

    def slabs(sy):
        """vertical slab per staff: [start, end) rows of the system band"""
        st = sy["staves"]
        cuts = [sy["y0"]] + [(st[k]["bottom"] + st[k + 1]["top"]) // 2 for k in range(len(st) - 1)] + [sy["y1"]]
        return [(cuts[k], cuts[k + 1]) for k in range(len(st))]

    ref_slabs = slabs(ref)
    H = ref["y1"] - ref["y0"]
    pieces = []
    # header (clef/key) from the first picked measure's line, plus its start line/brace.
    # The piece's first line also carries the time signature: unless the stitched line
    # starts the piece, borrow the clef + key header of the next line instead.
    hpg, hsy = ref_pg, ref
    if picks[0]["page_index"] == 0 and picks[0]["system"] == 0 and picks[0]["n"] != measures[0]["n"]:
        nxt = (pages[0]["systems"][1] if len(pages[0]["systems"]) > 1 else
               pages[1]["systems"][0] if len(pages) > 1 and pages[1]["systems"] else None)
        if nxt is not None and len(nxt["staves"]) == nst:
            hpg = pages[0] if len(pages[0]["systems"]) > 1 else pages[1]
            hsy = nxt
    hx0, hx1 = hsy["header"]
    band = hpg["ink"][hsy["y0"]:hsy["y1"], :hx0]
    lead = np.where(band.any(axis=0))[0]
    hx0 = int(lead[0]) if lead.size and hx0 - lead[0] < 4 * sp else hx0
    pieces.append((hpg, hsy, hx0, hx1))
    for m in picks:
        pg = pages[m["page_index"]]
        sy = pg["systems"][m["system"]]
        if len(sy["staves"]) != nst:
            raise SystemExit(f"measure {m['n']} has {len(sy['staves'])} staves, expected {nst}")
        pieces.append((pg, sy, m["x"][0], m["x"][1]))
    W = sum(r - l for _, _, l, r in pieces)
    canvas = Image.new("L", (W, H + 2 * int(4 * sp)), 255)
    off_y = int(4 * sp)  # headroom: other lines may carry taller markings
    x = 0
    for pi_, (pg, sy, l, r) in enumerate(pieces):
        for k, (a, b) in enumerate(slabs(sy)):
            # align this staff's top line with the reference line's staff k
            dy = (ref["staves"][k]["top"] - ref["y0"]) - (sy["staves"][k]["top"] - a)
            part = pg["gray"].crop((l, a, r, b))
            part = part.point(lambda v, t=pg["paper"] - 40: 255 if v >= t else v)
            if pi_ == 0 and k == 0:
                # header: drop the source line's printed bar number, which sits above
                # the line start (left of / above the clef), and anything high above
                st0 = sy["staves"][0]
                left_w = int(st0["x0"] - l + 0.6 * sy["sp"])
                above = int(st0["top"] - 0.3 * sy["sp"]) - a
                if above > 0 and left_w > 0:
                    part.paste(255, (0, 0, min(left_w, part.width), above))
                high = int(st0["top"] - 2 * sy["sp"]) - a
                if high > 0:
                    part.paste(255, (0, 0, part.width, high))
            y = off_y + dy
            box = (x, y, x + (r - l), y + (b - a))
            under = canvas.crop(box)
            canvas.paste(ImageChops.darker(under, part), box[:2])
        x += r - l
    arr = np.asarray(canvas) < 200
    rows = np.where(arr.any(axis=1))[0]
    cols = np.where(arr.any(axis=0))[0]
    canvas = canvas.crop((int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1))
    from PIL import ImageOps
    ImageOps.expand(canvas, border=int(sp), fill=255).save(out_png)
    return {"png": out_png, "measures": nums,
            "from": sorted({(m["page"], m["system"]) for m in picks})}


def parse_range(text):
    out = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--dpi", type=int, default=300, help="render resolution (blog tip: 200–300%%)")
    ap.add_argument("--first-measure", type=int, default=1,
                    help="number of the first measure (0 when the piece starts with a pickup)")
    ap.add_argument("--list", action="store_true", help="print detected measures")
    ap.add_argument("--preview", help="write page images with numbered measure boxes here")
    ap.add_argument("--take", help='measure range(s): "5-8" or "1-4;5-8" (one line each)')
    ap.add_argument("--reflow", type=int, help="N measures per line over --take (or all)")
    ap.add_argument("-o", "--output", help="output PNG (numbered -1, -2 … for several lines)")
    a = ap.parse_args()

    pages, measures = analyse(a.inputs, a.dpi, a.first_measure)
    if not measures:
        sys.exit("no measures found")
    result = {"measures": len(measures), "first": measures[0]["n"], "last": measures[-1]["n"],
              "lines": [{"page": p["page"], "system": i, "measures":
                         [m["n"] for m in measures if m["page_index"] == pi and m["system"] == i]}
                        for pi, p in enumerate(pages) for i in range(len(p["systems"]))]}
    if a.preview:
        result["previews"] = preview(pages, measures, a.preview)
    groups = []
    if a.take or a.reflow:
        if a.take and ";" in a.take:
            groups = [parse_range(g) for g in a.take.split(";")]
        else:
            nums = parse_range(a.take) if a.take else [m["n"] for m in measures]
            if a.reflow:
                groups = [nums[i:i + a.reflow] for i in range(0, len(nums), a.reflow)]
            else:
                groups = [nums]
        if not a.output:
            sys.exit("-o is required with --take/--reflow")
        stem, ext = os.path.splitext(a.output)
        os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
        result["outputs"] = []
        for gi, g in enumerate(groups, 1):
            out = a.output if len(groups) == 1 else f"{stem}-{gi}{ext or '.png'}"
            result["outputs"].append(stitch(pages, measures, g, out))
    print(json.dumps(result, ensure_ascii=False, indent=1, default=int))


if __name__ == "__main__":
    main()
