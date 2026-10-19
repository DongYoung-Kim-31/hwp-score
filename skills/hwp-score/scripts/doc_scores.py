#!/usr/bin/env python3
"""List the score pictures hwp-score already put into an HWP/HWPX document.

  doc_scores.py DOC.hwp        -> JSON list, one entry per tagged picture:
     {section, paragraph, ctrl, bin_id, width, height, png_w, sp, gap_mm}

insert_split.py tags every PNG it inserts with a PNG text chunk holding its staff
spacing in pixels (KEY below). The PNG bytes are embedded unchanged, so the tag can be
read back here (HWP: OLE stream BinData/BINxxxx.*, HWPX: zip BinData/*) and combined
with the picture's current size from `rhwp dump` to get its staff gap on paper — also
after the user resized it in 한글. Pictures without the tag are ignored.
"""
import json, os, re, struct, subprocess, sys, zipfile, zlib

KEY = b"hwp-score-staff-px"
HWPUNIT_PER_MM = 7200 / 25.4
RHWP = os.path.expanduser("~/.local/bin/rhwp")


def png_tag(data):
    """(png width px, staff px) from a tagged PNG, else None."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    w = struct.unpack(">I", data[16:20])[0]
    i = 8
    while i + 8 <= len(data):
        n, typ = struct.unpack(">I4s", data[i:i + 8])
        if typ == b"tEXt":
            k, _, v = data[i + 8:i + 8 + n].partition(b"\0")
            if k == KEY:
                return w, float(v)
        if typ == b"IDAT":
            break   # insert_split writes the tag before the image data
        i += 12 + n
    return None


def add_png_tag(src, dst, sp):
    """Copy PNG src -> dst with the staff-spacing text chunk right after IHDR."""
    data = open(src, "rb").read()
    body = KEY + b"\0" + f"{sp:.3f}".encode()
    chunk = struct.pack(">I", len(body)) + b"tEXt" + body + \
        struct.pack(">I", zlib.crc32(b"tEXt" + body) & 0xFFFFFFFF)
    ihdr_end = 8 + 12 + struct.unpack(">I", data[8:12])[0]
    with open(dst, "wb") as f:
        f.write(data[:ihdr_end] + chunk + data[ihdr_end:])


def bin_data(doc):
    """{bin_id: bytes} of embedded pictures."""
    out = {}
    if zipfile.is_zipfile(doc):
        with zipfile.ZipFile(doc) as z:
            hpf = z.read("Contents/content.hpf").decode("utf-8", "replace")
            hrefs = re.findall(r'<opf:item [^>]*href="(BinData/[^"]+)"', hpf)
            for k, href in enumerate(hrefs, 1):      # rhwp numbers manifest items 1..n
                out[k] = z.read(href)
        return out
    try:
        import olefile
    except ImportError:
        print("doc_scores: olefile missing (run setup.sh); earlier scores not matched",
              file=sys.stderr)
        return out
    with olefile.OleFileIO(doc) as o:
        for e in o.listdir():
            if len(e) == 2 and e[0] == "BinData":
                m = re.match(r"BIN([0-9A-Fa-f]{4})\.", e[1])
                if not m:
                    continue
                raw = o.openstream(e).read()
                try:
                    raw = zlib.decompress(raw, -15)   # compressed documents
                except zlib.error:
                    pass
                out[int(m.group(1), 16)] = raw
    return out


def scores(doc):
    blobs = bin_data(doc)
    dump = subprocess.run([RHWP, "dump", doc], capture_output=True, text=True).stdout
    found, where = [], None
    for line in dump.splitlines():
        m = re.match(r"--- 문단 (\d+)\.(\d+) ---", line)
        if m:
            where = (int(m.group(1)), int(m.group(2)))
            continue
        m = re.match(r"\s+\[(\d+)\] 그림: bin_id=(\d+), common=(\d+)×(\d+)", line)
        if m and where:
            ctrl, bid, w, h = map(int, m.groups())
            tag = png_tag(blobs.get(bid, b""))
            if tag:
                pw, sp = tag
                found.append({"section": where[0], "paragraph": where[1], "ctrl": ctrl,
                              "bin_id": bid, "width": w, "height": h, "png_w": pw, "sp": sp,
                              "gap_mm": round(w * sp / pw / HWPUNIT_PER_MM, 3)})
    return found


if __name__ == "__main__":
    print(json.dumps(scores(sys.argv[1]), ensure_ascii=False, indent=1))
