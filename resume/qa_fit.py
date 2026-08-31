"""Will this resume fit on one page, and does it say what we think it says?

LibreOffice cannot load any file in this sandbox, so there is no render to look
at. The layout here is a single column of stacked paragraphs, which is simple
enough to measure directly: wrap each run at the real column width using
Liberation Sans (metric-compatible with Arial), add the paragraph spacing, and
compare the total against the text height of an A4 page.
"""
import sys
from pathlib import Path

from PIL import ImageFont
from docx import Document
from docx.shared import Pt

FONTS = {
    (False, False): "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    (True, False): "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    (False, True): "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
    (True, True): "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
}
SCALE = 8                      # points -> pixels, for measurement precision
_cache = {}


def font(size_pt, bold, italic):
    key = (bool(bold), bool(italic), round(size_pt * SCALE))
    if key not in _cache:
        _cache[key] = ImageFont.truetype(FONTS[key[:2]], key[2])
    return _cache[key]


def measure(text, size_pt, bold, italic):
    return font(size_pt, bold, italic).getlength(text) / SCALE


def wrap_count(text, size_pt, bold, italic, width_pt):
    if not text.strip():
        return 1
    lines, cur = 1, ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if cur and measure(trial, size_pt, bold, italic) > width_pt:
            lines += 1
            cur = word
        else:
            cur = trial
    return lines


def main(path):
    doc = Document(path)
    sec = doc.sections[0]
    page_w = sec.page_width.pt
    page_h = sec.page_height.pt
    col = page_w - sec.left_margin.pt - sec.right_margin.pt
    avail = page_h - sec.top_margin.pt - sec.bottom_margin.pt

    total = 0.0
    print(f"A4 {page_w:.0f}x{page_h:.0f}pt, column {col:.1f}pt, "
          f"text height {avail:.1f}pt\n")
    for p in doc.paragraphs:
        runs = [r for r in p.runs if r.text]
        size = max((r.font.size.pt for r in runs if r.font.size), default=10.0)
        bold = any(r.font.bold for r in runs)
        italic = any(r.font.italic for r in runs)
        text = "".join(r.text for r in runs)
        left = p.paragraph_format.left_indent
        numbered = p._p.find(
            ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr")
        indent = (left.pt if left else 0.0) or (10.0 if numbered is not None else 0.0)
        # a right-aligned tab puts the whole paragraph on one line
        tabbed = "\t" in "".join(r.text for r in p.runs)
        lines = 1 if tabbed else wrap_count(text, size, bold, italic, col - indent)
        before = p.paragraph_format.space_before.pt if p.paragraph_format.space_before else 0
        after = p.paragraph_format.space_after.pt if p.paragraph_format.space_after else 0
        h = lines * size * 1.20 + before + after
        total += h
        flag = "  <-- wraps" if lines > 2 else ""
        print(f"{total:7.1f}  {h:5.1f}pt {lines}L {size:4.1f}pt  "
              f"{text[:62]!r}{flag}")

    print(f"\ntotal {total:.1f}pt of {avail:.1f}pt available "
          f"({total / avail * 100:.0f}% of one page)")
    if total > avail:
        print(f"OVERFLOWS by {total - avail:.1f}pt "
              f"(~{(total - avail) / 12:.1f} lines) - trim it")
    return 0 if total <= avail else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else "Rishabh_Singh_Kushwaha_Resume.docx"))
