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
                g = bg.convert("L")
            else:
                g = img.convert("L")
            if g.width < 1600:
                # screenshots / low-res scans: 1-px staff lines need room to be found
                f = 2000 / g.width
                g = g.resize((2000, int(g.height * f)), Image.BICUBIC)
            yield g
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
    lim = max(4.0, 1.6 * float(np.median(thick))) if len(thick) else 4.0
    thin_c = centers[thick <= lim]
    gaps = np.round(np.diff(thin_c)).astype(int) if thin_c.size > 1 else np.array([], int)
    gaps = gaps[(gaps >= 3) & (gaps <= max_sp)]
    sp_ref = float(np.bincount(gaps).argmax()) if gaps.size else None

    staves = []
    # anchor on pairs of *thin* lines (clean staff lines), then look for 5 evenly spaced
    # covered positions around them — up or down, since beams can swallow the top or
    # the bottom lines of a staff
    order = np.argsort(centers)
    for i in order:
        if used[i] or thick[i] > lim:
            continue
        found = None
        for j in range(i + 1, len(lines)):
            g = centers[j] - centers[i]
            if g > max_sp:
                break
            if g < 3 or used[j] or thick[j] > max(3, 0.5 * g) or thick[i] > max(3, 0.5 * g):
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
                thin = sum(thick[t] <= max(3, 0.5 * g) for t in idx)
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


def normalize_light(gray):
    """Flatten paper colour and uneven lighting (aged paper, phone photos): divide by
    a smooth estimate of the local background. Clean renders stay unchanged."""
    from PIL import ImageFilter
    small = gray.resize((max(1, gray.width // 8), max(1, gray.height // 8)))
    bg = small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(4))
    bg = np.asarray(bg.resize(gray.size, Image.BILINEAR)).astype(np.float32)
    a = np.asarray(gray).astype(np.float32)
    out = np.clip(a / np.maximum(bg, 1) * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def find_staves_strips(ink, line_ink, nstrips=16):
    """Staff finder for hand-ruled, wavy or perspective-distorted lines: find short
    5-line ladders in vertical strips, then chain them across strips."""
    h, w = ink.shape
    cols = np.where(ink.any(axis=0))[0]
    if cols.size == 0:
        return []
    c0, c1 = int(cols[0]), int(cols[-1]) + 1
    sw = max(40, (c1 - c0) // nstrips)
    dil = line_ink.copy()
    dil[1:] |= line_ink[:-1]
    dil[:-1] |= line_ink[1:]
    per_strip = []
    for si, x in enumerate(range(c0, c1 - sw // 2, sw)):
        frac = dil[:, x:x + sw].mean(axis=1)
        rr = runs(frac >= 0.55)
        cen = np.array([(a_ + b_ - 1) / 2 for a_, b_ in rr])
        thk = np.array([b_ - a_ for a_, b_ in rr])
        used = np.zeros(len(cen), bool)
        found = []
        for i in range(len(cen)):
            if used[i]:
                continue
            for j in range(i + 1, len(cen)):
                g = cen[j] - cen[i]
                if g > (c1 - c0) * 0.03:
                    break
                # staff lines are thin relative to their spacing (upscaled / photographed
                # lines can be 8-12 px thick); beams fused onto a line are not
                if g < 3 or used[j] or thk[i] > max(4, 0.6 * g) or thk[j] > max(4, 0.6 * g):
                    continue
                tol = max(1.5, 0.22 * g)
                idx = [i, j]
                for k in (2, 3, 4):
                    near = np.where((np.abs(cen - (cen[i] + k * g)) <= tol) & ~used)[0]
                    if near.size == 0:
                        break
                    idx.append(int(near[np.argmin(np.abs(cen[near] - (cen[i] + k * g)))]))
                if len(idx) == 5:
                    used[idx] = True
                    found.append((float(cen[i]), float((cen[idx[-1]] - cen[i]) / 4)))
                    break
        per_strip.append((x, found))
    # chain strip ladders left to right
    chains = []
    for si, (x, found) in enumerate(per_strip):
        for top, sp in found:
            best = None
            for ch in chains:
                ls, lx, ltop, lsp = ch[-1]
                if 0 < si - ls <= 2 and abs(top - ltop) <= 1.2 * max(sp, lsp) and abs(sp - lsp) <= 0.3 * lsp:
                    if best is None or abs(top - ltop) < abs(top - best[-1][2]):
                        best = ch
            if best is not None:
                best.append((si, x, top, sp))
            else:
                chains.append([(si, x, top, sp)])
    out = []
    need = max(3, int(0.15 * len(per_strip)))
    for ch in chains:
        if len(ch) < need:
            continue
        sps = np.array([c[3] for c in ch])
        sp = float(np.median(sps))
        tops = np.array([c[2] for c in ch])
        out.append({"top": int(tops.min()) - 1, "bottom": int(tops.max() + 4 * sp) + 2, "sp": sp,
                    "x0": int(ch[0][1]), "x1": int(min(w - 1, ch[-1][1] + sw)), "wavy": True})
    if not out:
        return out
    ref = np.median([o["sp"] for o in out])
    out = [o for o in out if abs(o["sp"] - ref) <= 0.3 * ref]
    out.sort(key=lambda o: o["top"])
    final = []
    for o in out:
        if final and o["top"] <= final[-1]["bottom"]:
            if (o["x1"] - o["x0"]) > (final[-1]["x1"] - final[-1]["x0"]):
                final[-1] = o
            continue
        final.append(o)
    return final


def notation_density(ink, st):
    """Share of the staff's band covered by notation (ink minus long horizontal strokes,
    i.e. minus the staff lines themselves)."""
    sp = st["sp"]
    band = ink[max(0, st["top"]):st["bottom"] + 1, st["x0"]:st["x1"] + 1]
    if band.size == 0:
        return 0.0
    long_run = int(2.5 * sp)
    keep = band.copy()
    for y in range(band.shape[0]):
        for a_, b_ in runs(band[y]):
            if b_ - a_ >= long_run:
                keep[y, a_:b_] = False
    return float(keep.mean())


def merge_staves(*lists):
    """Union of staff lists from different finders; overlapping finds are one staff
    (keep the widest)."""
    allst = sorted([st for lst in lists for st in lst], key=lambda o: o["top"])
    out = []
    for o in allst:
        if out and o["top"] <= out[-1]["bottom"] - o["sp"]:
            prev = out[-1]
            # prefer the straight-line finder's staff (exact start x, used to find
            # braces); take the strip finder's only if it is clearly wider
            if prev.get("wavy") and not o.get("wavy"):
                if (prev["x1"] - prev["x0"]) < 1.3 * (o["x1"] - o["x0"]):
                    out[-1] = o
            elif o.get("wavy") and not prev.get("wavy"):
                if (o["x1"] - o["x0"]) > 1.3 * (prev["x1"] - prev["x0"]):
                    out[-1] = o
            elif (o["x1"] - o["x0"]) > (prev["x1"] - prev["x0"]):
                out[-1] = o
            continue
        out.append(o)
    if out:
        ref = float(np.median([o["sp"] for o in out]))
        out = [o for o in out if abs(o["sp"] - ref) <= 0.3 * ref]
    return out


def connected(ink, a, b, soft=None):
    """True if a vertical stroke joins staff a to staff b near their left edge."""
    x0 = min(a["x0"], b["x0"])
    reach = int(max(a["sp"], b["sp"]) * 3)
    lo, hi = a["bottom"], b["top"]
    if hi <= lo:
        return True
    band = ink[lo:hi, max(0, x0 - reach):x0 + reach]
    if band.size and (band.mean(axis=0) > 0.9).any():
        return True
    # no brace/system line at the left (common in manuscripts): barlines drawn
    # through the gap between the two staves also join them; allow slight slant
    if hi - lo > 6 * max(a["sp"], b["sp"]):
        return False
    xa, xb = max(a["x0"], b["x0"]), min(a["x1"], b["x1"])
    gap = (soft if soft is not None else ink)[lo:hi, xa:xb]
    if gap.size == 0:
        return False
    # hand-drawn barlines lean and wobble: widen each column by +-3 px
    g2 = gap.copy()
    for d in (1, 2, 3):
        g2[:, d:] |= gap[:, :-d]
        g2[:, :-d] |= gap[:, d:]
    hits = runs(g2.mean(axis=0) > 0.85)
    return len(hits) >= 2


def cut_between(ink, lo, hi, min_blank, prefer="middle", left_x=None, sp=None):
    """Row index to cut at inside [lo, hi): middle of the best blank run.
    left_x/sp (middle cuts): start x and spacing of the lower system's staff, used to
    hand its bar number (small mark at the line start, just above the staff) to it."""
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
        if left_x is not None and sp:
            # a bar number / rehearsal box at the start of the lower line belongs to it
            for k, (a_, b_) in enumerate(isl):
                if b_ > best_cut or n - b_ > 3.5 * sp:
                    continue
                cols = np.where(ink[lo + a_:lo + b_].any(axis=0))[0]
                if cols.size and cols[-1] < left_x + 3 * sp and cols[-1] - cols[0] < 4 * sp:
                    prev_end = isl[k - 1][1] if k > 0 else 0
                    best_cut = (prev_end + a_) // 2
                    break
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
    gray = normalize_light(gray)
    if not no_deskew:
        gray, _ = deskew(gray)
    a = np.asarray(gray)
    paper = float(np.percentile(a, 75))           # background (paper) gray level
    ink = a < min(INK, paper - 80)
    # staff lines are often 1 px and anti-aliased (gray, or split over two rows), or
    # blurred and widened (upscaled screenshots, photos). Try a few "is this part of a
    # line" thresholds, bridge 1-row gaps, run both finders and merge; keep the best.
    staves = []
    # 245/235: faint hand-ruled lines on manuscript paper; 185/165: blurred lines
    for lt in (215, 245, 235, 185, 165):
        loose = a < min(lt, paper - 8)
        line_ink = loose.copy()
        line_ink[1:] |= loose[:-1]
        found = merge_staves(find_staves(ink, min_span, line_ink),
                             find_staves_strips(ink, line_ink),
                             find_staves_strips(ink, line_ink, 24))
        if len(found) > len(staves):
            staves = found
    soft = a < paper - 60          # faint hand-drawn barlines still count here
    if len(staves) >= 3:
        # blank ruled staves (manuscript paper) carry no notation
        dens = [notation_density(ink, st) for st in staves]
        med = float(np.median(dens))
        staves = [st for st, d in zip(staves, dens) if d >= 0.12 * med and d > 0.002]
    if not staves:
        return gray, ink, paper, []
    groups = [[staves[0]]]
    for st in staves[1:]:
        if not per_staff and connected(ink, groups[-1][-1], st, soft):
            groups[-1].append(st)
        else:
            groups.append([st])
    if not per_staff and len(staves) >= 4 and all(len(g) == 1 for g in groups):
        # nothing joins the staves (manuscripts without braces): fall back to spacing.
        # Staves of one system sit closer together than the systems do.
        gaps = np.array([staves[i + 1]["top"] - staves[i]["bottom"] for i in range(len(staves) - 1)], float)
        lo_g, hi_g = gaps.min(), gaps.max()
        if hi_g > 1.6 * max(lo_g, 1):
            thr = (lo_g + hi_g) / 2
            for _ in range(10):                      # 2-means on the gap sizes
                small, big = gaps[gaps < thr], gaps[gaps >= thr]
                if not small.size or not big.size:
                    break
                thr = (small.mean() + big.mean()) / 2
            groups = [[staves[0]]]
            for i, st in enumerate(staves[1:]):
                if gaps[i] < thr:
                    groups[-1].append(st)
                else:
                    groups.append([st])
    sizes = [len(g) for g in groups]
    if len(groups) >= 3:
        k = max(set(sizes), key=sizes.count)
        if (k > 1 and sizes.count(k) >= len(groups) / 2) or (k == 1 and sizes.count(1) >= 0.75 * len(groups)):
            # a group that is a whole multiple of the usual system size joined two
            # systems (a stray stroke across the gap): split it back
            fixed = []
            for g in groups:
                if len(g) > k and len(g) % k == 0:
                    fixed += [g[i:i + k] for i in range(0, len(g), k)]
                else:
                    fixed.append(g)
            groups = fixed
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
            y0 = cut_between(ink, groups[i - 1][-1]["bottom"], top, min_blank, "middle",
                             left_x=min(st["x0"] for st in sys_), sp=sp)
        if i == len(groups) - 1:
            hi = min(h, int(bottom + sp * 8))
            y1 = cut_between(ink, bottom, hi, min_blank, "high")
        else:
            y1 = cut_between(ink, bottom, groups[i + 1][0]["top"], min_blank, "middle",
                             left_x=min(st["x0"] for st in groups[i + 1]),
                             sp=max(st["sp"] for st in groups[i + 1]))
        systems.append({"staves": sys_, "y0": int(y0), "y1": int(y1), "sp": float(sp)})
    return gray, ink, paper, systems


def clean_crop(gray, paper, box, pad):
    """Crop, whiten scan paper, add white margin."""
    cut = gray.crop(box)
    cut = cut.point(lambda v, t=paper - 40: 255 if v >= t else v)
    return ImageOps.expand(cut, border=pad, fill=255) if pad else cut


def own_ink(ink, systems, i, r0, r1):
    """Ink in rows [r0, r1) that belongs to system i. Needed when lines of notes
    interleave (manuscripts, dense scores) and no straight cut separates them.

    Staff lines are taken out first (otherwise every note touching a line merges
    with that whole staff into one blob); the remaining blobs - notes, stems, beams,
    slurs, text - go to the staff band they overlap most, floating ones to the nearer
    staff; finally this system's own staff lines are added back."""
    from scipy import ndimage
    region = ink[r0:r1]
    sp = max(st["sp"] for st in systems[i]["staves"])
    # staff-line pixels = long horizontal runs
    lines = np.zeros_like(region)
    long_run = int(2.5 * sp)
    for y in range(region.shape[0]):
        for a_, b_ in runs(region[y]):
            if b_ - a_ >= long_run:
                lines[y, a_:b_] = True
    notes = region & ~lines
    # label on a slightly thickened copy so the pieces of one mark (digits of a bar
    # number, a broken box, letters of a word) count as one, then map back
    grow = max(1, int(round(0.25 * sp)))
    lab, n = ndimage.label(ndimage.binary_dilation(notes, iterations=grow),
                           structure=np.ones((3, 3), bool))
    lab = np.where(notes, lab, 0)
    pad = int(0.5 * sp)
    own = [(st["top"] - r0 - pad, st["bottom"] - r0 + pad) for st in systems[i]["staves"]]
    nb = []
    if i > 0:
        nb += [(st["top"] - r0 - pad, st["bottom"] - r0 + pad) for st in systems[i - 1]["staves"]]
    if i + 1 < len(systems):
        nb += [(st["top"] - r0 - pad, st["bottom"] - r0 + pad) for st in systems[i + 1]["staves"]]
    keep = np.zeros(n + 1, bool)
    if n:
        rows_of = np.arange(region.shape[0])
        def band_mask(bands):
            m = np.zeros(region.shape[0], bool)
            for a_, b_ in bands:
                m[max(0, a_):max(0, b_ + 1)] = True
            return m
        in_own = band_mask(own)[:, None] & notes
        in_nb = band_mask(nb)[:, None] & notes
        cnt_own = ndimage.sum(in_own, lab, index=np.arange(1, n + 1))
        cnt_nb = ndimage.sum(in_nb, lab, index=np.arange(1, n + 1))
        objs = ndimage.find_objects(lab)
        floating = []
        for k in range(1, n + 1):
            co, cn = cnt_own[k - 1], cnt_nb[k - 1]
            if co or cn:
                keep[k] = co >= cn
            elif objs[k - 1] is not None:
                floating.append((objs[k - 1][0].start, objs[k - 1][0].stop, k))
        # floating marks (pedal Ped./release, lyrics, dynamics, bar numbers) that share
        # a row are one row of markings and go together. A row stays with the upper
        # line unless clearly (1.4x) closer to the lower one: pedal marks, lyrics and
        # dynamics sit under their staff, bar numbers just above theirs.
        floating.sort()
        rows_ = []
        for ya, yb, k in floating:
            if rows_ and ya < rows_[-1][1]:
                rows_[-1][1] = max(rows_[-1][1], yb)
                rows_[-1][2].append(k)
            else:
                rows_.append([ya, yb, [k]])
        own_c = sum((a_ + b_) / 2 for a_, b_ in own) / len(own)
        for ya, yb, ks in rows_:
            mid = (ya + yb) / 2
            d_own = min(min(abs(mid - a_), abs(mid - b_)) for a_, b_ in own)
            d_nb = min((min(abs(mid - a_), abs(mid - b_)) for a_, b_ in nb), default=1e9)
            if mid > own_c:      # row is below our staff: we are the upper line
                take = d_own <= 1.4 * d_nb
            else:                # row is above our staff: we are the lower line
                take = 1.4 * d_own < d_nb
            for k in ks:
                keep[k] = take
    mine = keep[lab] & notes
    own_rows = np.zeros(region.shape[0], bool)
    for st in systems[i]["staves"]:
        own_rows[max(0, st["top"] - r0 - 2):st["bottom"] - r0 + 3] = True
    return mine | (lines & own_rows[:, None])


def split_page(gray, page_no, outdir, stem, args, counter):
    gray, ink, paper, systems = detect_systems(gray, args.min_span, args.per_staff,
                                               args.keep_title, args.no_deskew)
    results = []
    h = ink.shape[0]
    for i, sy in enumerate(systems):
        y0, y1 = sy["y0"], sy["y1"]
        # Between two lines, hand every separate mark to the nearer staff (bar numbers,
        # rehearsal letters, pedal marks, lyrics, interleaved notes) instead of trusting
        # one straight cut; the outer edges of the page keep the plain cuts.
        if len(systems) > 1 and not args.per_staff:
            # the straight cut runs through notes: separate the lines blob by blob
            # within the span between the neighbouring systems' staves
            # label over a span that includes the neighbours' nearest staves, so
            # their notes are seen touching them
            r0 = systems[i - 1]["staves"][-1]["top"] if i > 0 else y0
            r1 = systems[i + 1]["staves"][0]["bottom"] + 1 if i + 1 < len(systems) else y1
            mine = own_ink(ink, systems, i, r0, r1)
            if i == 0:
                # page number printed at the outer corner above the first line
                from scipy import ndimage
                st0 = sy["staves"][0]
                top_rel = max(0, st0["top"] - r0)
                top_part = mine[:top_rel]
                g = max(1, int(round(0.3 * sy["sp"])))
                lab, nl = ndimage.label(ndimage.binary_dilation(top_part, iterations=g),
                                        structure=np.ones((3, 3), bool))
                lab = np.where(top_part, lab, 0)
                for k, sl in enumerate(ndimage.find_objects(lab), 1):
                    if sl is None:
                        continue
                    xa, xb = sl[1].start, sl[1].stop
                    xc = (xa + xb) / 2
                    if (xc > st0["x1"] - 2.5 * sy["sp"] or xb < st0["x0"]) and \
                            xb - xa < 4 * sy["sp"] and sl[0].stop - sl[0].start < 2.5 * sy["sp"]:
                        top_part[lab == k] = False
            cols = np.where(mine.any(axis=0))[0]
            rows = np.where(mine.any(axis=1))[0]
            x0, x1 = int(cols[0]), int(cols[-1]) + 1
            yy0, yy1 = r0 + int(rows[0]), r0 + int(rows[-1]) + 1
            # whiten everyone else's ink (and its anti-aliased rim) inside the box
            from scipy import ndimage
            other = ink[r0:r1] & ~mine
            other = ndimage.binary_dilation(other, iterations=2) & ~ndimage.binary_dilation(mine, iterations=1)
            arr = np.asarray(gray).copy()
            sub = arr[r0:r1]
            sub[other] = 255
            arr[r0:r1] = sub
            src = Image.fromarray(arr)
        else:
            band = ink[y0:y1]
            if i == 0:
                # a page number printed at the outer corner above the first line
                st0 = sy["staves"][0]
                sp0 = sy["sp"]
                above = band[:max(0, st0["top"] - y0)]
                for a_, b_ in runs(above.any(axis=1)):
                    cc = np.where(above[a_:b_].any(axis=0))[0]
                    if cc.size and cc[-1] - cc[0] < 3 * sp0 and b_ - a_ < 2.5 * sp0 and \
                            (cc[0] > st0["x1"] - 3 * sp0 or cc[-1] < st0["x0"] + 1 * sp0):
                        band = band.copy()
                        band[a_:b_, cc[0]:cc[-1] + 1] = False
                        arr = np.asarray(gray).copy()
                        arr[y0 + a_:y0 + b_, cc[0]:cc[-1] + 1] = 255
                        gray = Image.fromarray(arr)
            cols = np.where(band.any(axis=0))[0]
            rows = np.where(band.any(axis=1))[0]
            # crop strictly inside this system's cut lines (never past them into the
            # neighbour's markings), then add white margin around the ink
            x0, x1 = int(cols[0]), int(cols[-1]) + 1
            yy0, yy1 = y0 + int(rows[0]), y0 + int(rows[-1]) + 1
            src = gray
        counter[0] += 1
        png = os.path.join(outdir, f"{stem}-{counter[0]:02d}.png")
        clean_crop(src, paper, (x0, yy0, x1, yy1), int(sy["sp"])).save(png)
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
