"""Draw the resume to a PNG so it can be looked at before it is sent.

Nothing in this sandbox can render a .docx - LibreOffice refuses every input,
including a plain .txt. The layout is one column of stacked paragraphs, so
replaying it with PIL and Liberation Sans (metric-compatible with Arial) gives a
preview close enough to catch a bad wrap, a collision, or a line past the margin.
"""
import sys

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.oxml.ns import qn

FONTS = {
    (False, False): "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    (True, False): "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    (False, True): "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
    (True, True): "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
}
Z = 2.0                        # pixels per point
RIGHT_TAB = object()           # marker: everything after this hugs the right margin
_cache = {}


def font(pt, bold, italic):
    key = (bool(bold), bool(italic), max(4, round(pt * Z)))
    if key not in _cache:
        _cache[key] = ImageFont.truetype(FONTS[key[:2]], key[2])
    return _cache[key]


def items_of(paragraph):
    """Runs in document order, hyperlinks included, tabs as markers."""
    out = []
    for child in paragraph._p:
        if child.tag == qn("w:r"):
            nodes = [child]
        elif child.tag == qn("w:hyperlink"):
            nodes = list(child.findall(qn("w:r")))
        else:
            continue
        for r in nodes:
            if r.findall(qn("w:tab")):
                out.append(RIGHT_TAB)
            text = "".join(t.text or "" for t in r.findall(qn("w:t")))
            if not text.strip():
                continue
            rPr = r.find(qn("w:rPr"))
            sz, bold, italic, colour, under = 10.0, False, False, (0, 0, 0), False
            if rPr is not None:
                def on(tag):
                    el = rPr.find(qn(tag))
                    return el is not None and el.get(qn("w:val")) not in ("false", "0")
                el = rPr.find(qn("w:sz"))
                if el is not None:
                    sz = int(el.get(qn("w:val"))) / 2
                bold, italic, under = on("w:b"), on("w:i"), on("w:u")
                col = rPr.find(qn("w:color"))
                if col is not None and col.get(qn("w:val")) not in (None, "auto"):
                    v = col.get(qn("w:val"))
                    colour = tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
            out.append((text, font(sz, bold, italic), colour, under, sz))
    return out


def render(path, out_png):
    doc = Document(path)
    sec = doc.sections[0]
    W, H = int(sec.page_width.pt * Z), int(sec.page_height.pt * Z)
    left = sec.left_margin.pt * Z
    right = (sec.page_width.pt - sec.right_margin.pt) * Z
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    y = sec.top_margin.pt * Z
    overflow = None

    def draw(pieces, x0):
        for text, f, colour, under in pieces:
            d.text((x0, y), text, font=f, fill=colour)
            w = f.getlength(text)
            if under:
                d.line([(x0, y + f.size * 1.02), (x0 + w, y + f.size * 1.02)],
                       fill=colour, width=1)
            x0 += w

    for p in doc.paragraphs:
        items = items_of(p)
        pf = p.paragraph_format
        y += (pf.space_before.pt if pf.space_before else 0) * Z
        sizes = [i[4] for i in items if i is not RIGHT_TAB]
        size = max(sizes) if sizes else 10.0
        lh = size * 1.20 * Z
        numbered = p._p.find(f".//{qn('w:numPr')}") is not None
        indent = (pf.left_indent.pt * Z if pf.left_indent else 0) or (
            10 * Z if numbered else 0)
        centred = "CENTER" in str(pf.alignment or "")
        x0 = left + indent

        if not sizes:
            y += lh + (pf.space_after.pt if pf.space_after else 0) * Z
            continue
        if numbered:
            d.text((left, y), "•", font=font(size, False, False), fill=(0, 0, 0))

        line, width, tail = [], 0.0, None
        for it in items:
            if it is RIGHT_TAB:
                tail = []
                continue
            text, f, colour, under, _ = it
            for word in text.split(" "):
                piece = word + " "
                w = f.getlength(piece)
                if tail is not None:
                    tail.append((piece, f, colour, under))
                    continue
                if line and width + w > right - x0:
                    start = left + (right - left - width) / 2 if centred else x0
                    draw(line, start)
                    y += lh
                    line, width = [], 0.0
                line.append((piece, f, colour, under))
                width += w
        start = left + (right - left - width) / 2 if centred else x0
        draw(line, start)
        if tail:
            tw = sum(f.getlength(t) for t, f, _, _ in tail)
            draw(tail, right - tw)
        y += lh + (pf.space_after.pt if pf.space_after else 0) * Z
        if y > (sec.page_height.pt - sec.bottom_margin.pt) * Z and overflow is None:
            overflow = p.text[:50]

    d.line([(left, (sec.page_height.pt - sec.bottom_margin.pt) * Z),
            (right, (sec.page_height.pt - sec.bottom_margin.pt) * Z)],
           fill=(220, 60, 60), width=1)
    img.save(out_png)
    print(f"{out_png} {img.size}  content ends at {y / Z:.0f}pt of "
          f"{sec.page_height.pt - sec.bottom_margin.pt:.0f}pt")
    if overflow:
        print(f"  OVERFLOWS the page from: {overflow!r}")


if __name__ == "__main__":
    render(sys.argv[1] if len(sys.argv) > 1 else "Rishabh_Singh_Kushwaha_Resume.docx",
           sys.argv[2] if len(sys.argv) > 2 else "resume-preview.png")
