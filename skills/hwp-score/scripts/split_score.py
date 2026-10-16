#!/usr/bin/env python3
"""Cut a printed/scanned score (PDF or image) into one PNG per system (줄 단위 보표).

Run with ~/.cache/hwp-score/venv/bin/python (needs numpy, pillow, pypdfium2).

Detection:
  1. Rows whose dark pixels span most of the page width are staff-line rows.
  2. Adjacent staff-line rows are merged into lines; runs of ~5 evenly spaced lines
     form a staff.
  3. Consecutive staves joined by a vertical line at their left edge (brace/system
     barline, e.g. piano grand staff, choir) form one system. --per-staff disables this.
  4. Each system is cut at the middle of the blank band between it and its neighbour,
     so ledger-line notes, dynamics and lyrics stay with their system; the page title
     above the first system and the footer below the last are left out (--keep-title
     keeps the title with the first system).

Prints JSON: {"systems": [{"png", "page", "staves", "box"}...]}.
"""
import argparse, json, os, sys
import numpy as np
from PIL import Image, ImageOps

INK = 140  # gray level below which a pixel counts as ink


def load_pages(path, dpi):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(path)
        for i in range(len(pdf)):
            yield pdf[i].render(scale=dpi / 72).to_pil().convert("L")
    else:
        img = Image.open(path)
        frame = 0
        while True:  # multi-page TIFF support
            if img.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGBA", img.size, "white")
                bg.alpha_composite(img.convert("RGBA"))
                yield bg.convert("L")
            else:
                yield img.convert("L")
            frame += 1
            try:
                img.seek(frame)
            except EOFError:
                break


def runs(mask):
    """[(start, end_exclusive)] of True runs in a 1-D bool array."""
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def find_staves(ink, min_span, line_ink=None):
    """line_ink: looser mask for staff-line detection (thin anti-aliased lines)."""
    h, w = ink.shape
    if line_ink is None:
        line_ink = ink
    cols = np.where(ink.any(axis=0))[0]
    if cols.size == 0:
        return []
    content_w = cols[-1] - cols[0] + 1
    # longest horizontal ink run per row: a staff line is one long unbroken stroke,
    # even when the system is short (last line of a piece)
    padded = np.zeros((h, w + 2), dtype=np.int8)
    padded[:, 1:-1] = line_ink
    d = np.diff(padded, axis=1)
    longest = np.zeros(h, dtype=np.int64)
    for y in np.where(line_ink.any(axis=1))[0]:
        starts, ends = np.where(d[y] == 1)[0], np.where(d[y] == -1)[0]
        longest[y] = (ends - starts).max()
    line_rows = longest >= min_span * content_w
    lines = [((a + b - 1) / 2, a, b) for a, b in runs(line_rows)]
    if len(lines) < 5:
        return []
    # A staff = 5 thin lines at equal spacing. Other long horizontal strokes (volta
    # brackets, beams, hairpins, ottava lines, text underlines) can sit right next to
    # or between staff lines, so search for an evenly spaced 5-line ladder instead of
    # taking consecutive lines.
    max_sp = content_w * 0.03          # staff spacing is ~1% of the line width
    centers = np.array([c for c, _, _ in lines])
    starts = np.array([a for _, a, _ in lines])
    ends = np.array([b for _, _, b in lines])
    used = np.zeros(len(lines), bool)
    thick = (ends - starts)

    def cover(pos, tol):
        """index of an unused line run covering pos (lines fused with beams are thick)"""
        near = np.where((starts - tol <= pos) & (pos <= ends - 1 + tol) & ~used)[0]
        return int(near[np.argmin(np.abs(centers[near] - pos))]) if near.size else None

    # typical staff-line spacing on this page = most common gap between consecutive
    # thin lines; ladders far from it (a beam + the next staff's lines, 4x apart) are
    # not staves
    thin_c = centers[thick <= 4]
    gaps = np.round(np.diff(thin_c)).astype(int) if thin_c.size > 1 else np.array([], int)
    gaps = gaps[(gaps >= 3) & (gaps <= max_sp)]
    sp_ref = float(np.bincount(gaps).argmax()) if gaps.size else None

    staves = []
    # anchor on pairs of *thin* lines (clean staff lines), then look for 5 evenly spaced
    # covered positions around them — up or down, since beams can swallow the top or
    # the bottom lines of a staff
    order = np.argsort(centers)
    for i in order:
        if used[i] or thick[i] > 4:
            continue
        found = None
        for j in range(i + 1, len(lines)):
            g = centers[j] - centers[i]
            if g > max_sp:
                break
            if g < 3 or used[j] or thick[j] > max(3, 0.4 * g):
                continue
            if sp_ref and abs(g - sp_ref) > max(1.5, 0.3 * sp_ref):
                continue
            tol = max(1.5, 0.2 * g)
            best = None
            for k0 in range(-3, 1):          # i is line k0+? of the staff: try all windows
                ks = [k0 + t for t in range(5)]
                if 0 not in ks or 1 not in ks:
                    continue
                idx = [cover(centers[i] + k * g, tol) for k in ks]
                if None in idx or len(set(idx)) < 4:
                    continue
                thin = sum(thick[t] <= max(3, 0.4 * g) for t in idx)
                if thin >= 3 and (best is None or thin > best[0]):
                    best = (thin, idx, centers[i] + ks[0] * g, g)
            if best:
                found = best
                break
        if found:
            _, idx, top_c, g = found
            used[idx] = True
            half = max(1, int(round(g * 0.15)))
            staves.append((int(top_c) - half, int(top_c + 4 * g) + half + 1, g))
    out = []
    for top, bottom, sp in staves:
        # left/right extent of the staff: columns where at least 3 of its 5 lines have
        # ink (instrument names on a line's height don't qualify; noisy scans with
        # broken lines still do), small gaps bridged, longest stretch wins
        votes = np.zeros(w, np.int16)
        for k in range(5):
            y = int(round(top + 1 + k * sp))
            votes += line_ink[max(0, y - 1):y + 2].any(axis=0)
        good = votes >= 3
        gap_runs = [(a_, b_) for a_, b_ in runs(~good) if b_ - a_ <= sp]
        for a_, b_ in gap_runs:
            good[a_:b_] = True
        rr = runs(good)
        if not rr:
            continue
        a_, b_ = max(rr, key=lambda r: r[1] - r[0])
        best = (b_ - a_, a_, b_)
        out.append({"top": top, "bottom": bottom, "sp": float(sp),
                    "x0": int(best[1]), "x1": int(best[2]) - 1})
    if not out:
        return out
    # drop false "staves" (stacks of beams, cross-hatching): keep ladders whose spacing
    # matches the page's staves (small cue/ossia staves are dropped too), and
    # never let two staves overlap
    sps = np.array([o["sp"] for o in out])
    ref = np.median(sps)
    keep = [o for o in out if abs(o["sp"] - ref) <= 0.25 * ref]
    keep.sort(key=lambda o: o["top"])
    final = []
    for o in keep:
        if final and o["top"] <= final[-1]["bottom"]:
            # overlapping: keep the one closer to the reference spacing
            if abs(o["sp"] - ref) < abs(final[-1]["sp"] - ref):
                final[-1] = o
            continue
        final.append(o)
    return final


def connected(ink, a, b):
    """True if a vertical stroke joins staff a to staff b near their left edge."""
    x0 = min(a["x0"], b["x0"])
    reach = int(max(a["sp"], b["sp"]) * 3)
    lo, hi = a["bottom"], b["top"]
    if hi <= lo:
        return True
    band = ink[lo:hi, max(0, x0 - reach):x0 + reach]
    if band.size == 0:
        return False
    return bool((band.mean(axis=0) > 0.9).any())


def cut_between(ink, lo, hi, min_blank, prefer="middle"):
    """Row index to cut at inside [lo, hi): middle of the best blank run."""
    if hi - lo < 2:
        return (lo + hi) // 2
    blank = ~ink[lo:hi].any(axis=1)
    rs = [(a, b) for a, b in runs(blank) if b - a >= min_blank]
    if not rs:
        rs = runs(blank) or [(0, hi - lo)]
    if prefer == "middle":
        # Ink between two systems (low notes, pedal marks, lyrics, dynamics, measure
        # numbers, volta brackets) forms horizontal "islands". Markings hug the staff
        # they belong to, so cut at the widest blank between neighbouring islands; on a
        # tie favour the upper system (pedal lines and lyrics sit *below* their staff).
        n = hi - lo
        isl = runs(ink[lo:hi].any(axis=1))
        if not isl:
            return lo + n // 2
        edges = [0] + [x for ab in isl for x in ab] + [n]   # 0, a1, b1, a2, b2, ..., n
        best_gap, best_cut = -1, n // 2
        for t in range(len(isl) + 1):
            top = edges[2 * t]                 # end of island t-1 (or 0)
            bot = edges[2 * t + 1]             # start of island t (or n)
            gap = bot - top
            if gap >= best_gap:                # >= : later split = more to the upper
                best_gap, best_cut = gap, (top + bot) // 2
        return lo + best_cut
    if prefer == "low":   # nearest to hi (just above the next staff)
        a, b = rs[-1]
    else:                   # "high": nearest to lo (just below the previous staff)
        a, b = rs[0]
    return lo + (a + b) // 2


def deskew(gray, max_deg=3.0):
    """Rotate a slightly tilted scan so staff lines are horizontal.

    Picks the angle whose row-ink profile is sharpest (staff lines line up in rows).
    """
    # half resolution keeps 1-px staff lines; quarter resolution blurs them away
    small = gray.resize((max(1, gray.width // 2), max(1, gray.height // 2)))
    paper = float(np.percentile(np.asarray(small), 75))

    def score(deg):
        r = np.asarray(small.rotate(deg, resample=Image.BILINEAR, fillcolor=255)) < paper - 50
        return float(np.var(r.sum(axis=1)))
    flat = score(0.0)
    best, best_score = 0.0, flat
    for deg in np.arange(-max_deg, max_deg + 1e-9, 0.2):   # coarse, then refine
        sc = score(deg)
        if sc > best_score:
            best, best_score = float(deg), sc
    for deg in np.arange(best - 0.15, best + 0.151, 0.05):
        sc = score(deg)
        if sc > best_score:
            best, best_score = float(deg), sc
    # leave straight pages alone: rotating resamples and blurs 1-px staff lines
    if abs(best) < 0.2 or best_score < flat * 1.15:
        return gray, 0.0
    return gray.rotate(best, resample=Image.BICUBIC, expand=False, fillcolor=255), round(best, 2)


def detect_systems(gray, min_span=0.12, per_staff=False, keep_title=False, no_deskew=False):
    """Clean the page and find its systems.

    Returns (gray, ink, paper, systems) where each system is
    {"staves": [staff...], "y0": cut_top, "y1": cut_bottom}.
    """
    from PIL import ImageFilter
    raw = np.asarray(gray)
    paper_px = raw[raw > np.percentile(raw, 60) - 30]
    if paper_px.std() > 4:  # speckled scan; a clean render's paper is flat (and a
        gray = gray.filter(ImageFilter.MedianFilter(3))  # median would erase 1-px lines)
    if not no_deskew:
        gray, _ = deskew(gray)
    a = np.asarray(gray)
    paper = float(np.percentile(a, 75))           # background (paper) gray level
    ink = a < min(INK, paper - 80)
    loose = a < min(215, paper - 35)
    # staff lines are often 1 px and anti-aliased (gray, or split over two rows):
    # detect them on a looser mask with 1-row vertical gaps bridged
    line_ink = loose.copy()
    line_ink[1:] |= loose[:-1]
    staves = find_staves(ink, min_span, line_ink)
    if not staves:
        return gray, ink, paper, []
    groups = [[staves[0]]]
    for st in staves[1:]:
        if not per_staff and connected(ink, groups[-1][-1], st):
            groups[-1].append(st)
        else:
            groups.append([st])
    h, w = ink.shape
    systems = []
    for i, sys_ in enumerate(groups):
        sp = max(st["sp"] for st in sys_)
        top, bottom = sys_[0]["top"], sys_[-1]["bottom"]
        min_blank = max(2, int(sp * 0.8))
        if i == 0:
            lo = 0 if keep_title else max(0, int(top - sp * 8))
            y0 = lo if keep_title else cut_between(ink, lo, top, min_blank, "low")
        else:
            y0 = cut_between(ink, groups[i - 1][-1]["bottom"], top, min_blank, "middle")
        if i == len(groups) - 1:
            hi = min(h, int(bottom + sp * 8))
            y1 = cut_between(ink, bottom, hi, min_blank, "high")
        else:
            y1 = cut_between(ink, bottom, groups[i + 1][0]["top"], min_blank, "middle")
        systems.append({"staves": sys_, "y0": int(y0), "y1": int(y1), "sp": float(sp)})
    return gray, ink, paper, systems


def clean_crop(gray, paper, box, pad):
    """Crop, whiten scan paper, add white margin."""
    cut = gray.crop(box)
    cut = cut.point(lambda v, t=paper - 40: 255 if v >= t else v)
    return ImageOps.expand(cut, border=pad, fill=255) if pad else cut


def split_page(gray, page_no, outdir, stem, args, counter):
    gray, ink, paper, systems = detect_systems(gray, args.min_span, args.per_staff,
                                               args.keep_title, args.no_deskew)
    results = []
    for sy in systems:
        y0, y1 = sy["y0"], sy["y1"]
        band = ink[y0:y1]
        cols = np.where(band.any(axis=0))[0]
        rows = np.where(band.any(axis=1))[0]
        # crop strictly inside this system's cut lines (never past them into the
        # neighbour's markings), then add white margin around the ink
        x0, x1 = int(cols[0]), int(cols[-1]) + 1
        yy0, yy1 = y0 + int(rows[0]), y0 + int(rows[-1]) + 1
        counter[0] += 1
        png = os.path.join(outdir, f"{stem}-{counter[0]:02d}.png")
        clean_crop(gray, paper, (x0, yy0, x1, yy1), int(sy["sp"])).save(png)
        results.append({"png": png, "page": page_no, "staves": len(sy["staves"]),
                        "box": [int(x0), int(yy0), int(x1), int(yy1)]})
    return results


def group_systems(systems, n, outdir):
    """Stack every n consecutive system crops into one image (works across page
    breaks). Each line keeps its original horizontal position on the page, so the
    first line's indent / instrument names line up as in the source."""
    grouped = []
    for gi in range(0, len(systems), n):
        part = systems[gi:gi + n]
        ims = [Image.open(s["png"]) for s in part]
        left = min(s["box"][0] for s in part)
        # crops carry a white margin of `pad` px; recover it from size vs. box
        offs = [s["box"][0] - left for s in part]
        width = max(o + im.width for o, im in zip(offs, ims))
        gap = max(4, ims[0].height // 12)
        height = sum(im.height for im in ims) + gap * (len(ims) - 1)
        canvas = Image.new("L", (width, height), 255)
        y = 0
        for o, im in zip(offs, ims):
            canvas.paste(im.convert("L"), (o, y))
            y += im.height + gap
        stem = os.path.splitext(os.path.basename(part[0]["png"]))[0].rsplit("-", 1)[0]
        png = os.path.join(outdir, f"{stem}-g{gi // n + 1:02d}.png")
        canvas.save(png)
        for s, im in zip(part, ims):
            im.close()
            os.remove(s["png"])
        grouped.append({"png": png, "page": part[0]["page"], "pages": sorted({s["page"] for s in part}),
                        "staves": part[0]["staves"], "lines": len(part)})
    return grouped


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("inputs", nargs="+", help="PDF or image files (png/jpg/tif), in order")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--dpi", type=int, default=200, help="PDF render resolution")
    ap.add_argument("--pages", help="PDF pages to use, 1-based, e.g. 1-2,4")
    ap.add_argument("--per-staff", action="store_true", help="one image per staff, ignore braces")
    ap.add_argument("--no-deskew", action="store_true", help="skip tilt correction for scans")
    ap.add_argument("--keep-title", action="store_true", help="keep page header with first system")
    ap.add_argument("--group", type=int, default=1,
                    help="put N consecutive systems (lines) in one image, e.g. 2 = two lines per picture")
    ap.add_argument("--min-span", type=float, default=0.12,
                    help="shortest unbroken horizontal stroke counted as a staff line, "
                         "as a fraction of the page's content width")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    wanted = None
    if args.pages:
        wanted = set()
        for part in args.pages.split(","):
            lo, _, hi = part.partition("-")
            wanted.update(range(int(lo), int(hi or lo) + 1))
    out, counter, empty = [], [0], []
    for path in args.inputs:
        stem = os.path.splitext(os.path.basename(path))[0]
        for n, page in enumerate(load_pages(path, args.dpi), 1):
            if wanted and n not in wanted:
                continue
            got = split_page(page, n, args.outdir, stem, args, counter)
            if not got:
                empty.append(f"{os.path.basename(path)} p{n}")
            out += got
    if args.group > 1 and out:
        out = group_systems(out, args.group, args.outdir)
    print(json.dumps({"systems": out, "pages_without_staves": empty}, ensure_ascii=False, indent=1))
    if not out:
        sys.exit("no staves found — scan too faint/skewed? try --min-span 0.06 or a higher --dpi")


if __name__ == "__main__":
    main()
