"""Guarantee a one-page PDF.

Compile; if the result is longer than one page, tighten the layout in steps
(spacing -> margins -> font size). If it still overflows, lay it out on one tall
page and scale that page down onto a single sheet - this last step always works
and keeps the text selectable for ATS.
"""
import io
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter, Transformation

from . import compiler
from .latex_utils import mask_comments

FIT_MARK = "% --- auto one-page fit"

# Cumulative LaTeX tweaks, injected right before \begin{document}. List spacing is left
# alone on purpose: templates like Jake's already pull lists up with negative \vspace,
# and shrinking topsep/itemsep on top of that makes headings overlap the bullets.
_MARGINS_1 = (r"\addtolength{\oddsidemargin}{-0.1in}\addtolength{\evensidemargin}{-0.1in}"
              r"\addtolength{\textwidth}{0.2in}\addtolength{\topmargin}{-0.1in}\addtolength{\textheight}{0.2in}")
_MARGINS_2 = (r"\addtolength{\oddsidemargin}{-0.1in}\addtolength{\evensidemargin}{-0.1in}"
              r"\addtolength{\textwidth}{0.2in}\addtolength{\topmargin}{-0.1in}\addtolength{\textheight}{0.25in}")

LEVELS = [
    # (description, preamble lines, font steps down)
    ("as written", [], 0),
    ("slightly tighter line spacing and margins", [r"\linespread{0.97}", _MARGINS_1], 0),
    ("a smaller font size", [r"\linespread{0.97}", _MARGINS_1], 1),
    ("a smaller font with tighter margins and line spacing", [r"\linespread{0.94}", _MARGINS_1, _MARGINS_2], 1),
]
SCALE_LEVEL = len(LEVELS)  # final fallback


@dataclass
class OnePageResult:
    ok: bool
    pdf: bytes | None
    log: str
    tex: str  # the .tex that produced the PDF (fit tweaks included)
    natural_pages: int = 0  # pages before any fitting
    fit_level: int = 0
    fit_note: str = ""
    scale: float = 1.0


def page_count(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _inject(tex: str, lines: list[str], tag: str) -> str:
    if not lines:
        return tex
    m = re.search(r"\\begin\{document\}", mask_comments(tex))
    if not m:
        return tex
    block = f"{FIT_MARK}: {tag} ---\n" + "\n".join(lines) + f"\n{FIT_MARK} end ---\n"
    return tex[:m.start()] + block + tex[m.start():]


def _smaller_font(tex: str, steps: int) -> str:
    """Step the \\documentclass size option down (12pt -> 11pt -> 10pt)."""
    if steps <= 0:
        return tex
    m = re.search(r"\\documentclass\s*(\[[^\]]*\])?", tex)
    if not m:
        return tex
    opts = m.group(1) or ""
    size = re.search(r"\b(1[012])pt\b", opts)
    if not size:  # no size option: already the 10pt default
        return tex
    new_opts = opts.replace(size.group(0), f"{max(10, int(size.group(1)) - steps)}pt")
    return tex[:m.start(1)] + new_opts + tex[m.end(1):]


def level_tex(tex: str, level: int) -> str:
    desc, lines, font_steps = LEVELS[level]
    return _inject(_smaller_font(tex, font_steps), lines, f"level {level}, {desc}")


def fit_to_one_page(tex: str, assets: Path | None = None) -> OnePageResult:
    first = compiler.compile_tex(tex, assets)
    if not first.ok:
        return OnePageResult(False, None, first.log, tex)
    natural = page_count(first.pdf)
    if natural <= 1:
        return OnePageResult(True, first.pdf, first.log, tex, natural, 0, "Fits on one page as written.")

    last_ok = (tex, first)
    for level in range(1, len(LEVELS)):
        candidate = level_tex(tex, level)
        res = compiler.compile_tex(candidate, assets)
        if not res.ok:
            continue  # a tweak the template doesn't support; try the next one
        last_ok = (candidate, res)
        if page_count(res.pdf) <= 1:
            return OnePageResult(True, res.pdf, res.log, candidate, natural, level,
                                 f"Was {natural} pages; fit to one page by {LEVELS[level][0]}.")

    # Final fallback: one tall page, scaled down onto a single sheet.
    base_tex, base_res = last_ok
    try:
        pdf, scale = _scale_to_one_page(base_tex, base_res.pdf, assets)
    except _ScaleError as e:
        return OnePageResult(False, None, f"Could not fit the resume on one page: {e}", base_tex, natural)
    return OnePageResult(True, pdf, base_res.log, base_tex, natural, SCALE_LEVEL,
                         f"Was {natural} pages; content scaled to {scale:.0%} to fit one page. "
                         "Consider shortening a few bullets for a crisper look "
                         "(the .tex download is not scaled).", scale)


class _ScaleError(RuntimeError):
    pass


def _scale_to_one_page(tex: str, normal_pdf: bytes, assets: Path | None) -> tuple[bytes, float]:
    ref = PdfReader(io.BytesIO(normal_pdf)).pages[0]
    target_w, target_h = float(ref.mediabox.width), float(ref.mediabox.height)

    extra_in = 40
    tall = _inject(tex, [
        "\\raggedbottom",
        f"\\addtolength{{\\paperheight}}{{{extra_in}in}}\\addtolength{{\\textheight}}{{{extra_in}in}}",
        r"\AtBeginDocument{\ifdefined\pdfpageheight\setlength{\pdfpageheight}{\paperheight}\fi}",
    ], "tall page for scaling")
    res = compiler.compile_tex(tall, assets)
    if not res.ok:
        raise _ScaleError(res.log)
    reader = PdfReader(io.BytesIO(res.pdf))
    if len(reader.pages) != 1:
        raise _ScaleError("content is longer than the tall layout")
    page = reader.pages[0]
    page_w, page_h = float(page.mediabox.width), float(page.mediabox.height)

    ys: list[float] = []

    def visit(text, cm, tm, font_dict, font_size):
        if text.strip():
            ys.append(tm[4] * cm[1] + tm[5] * cm[3] + cm[5])

    page.extract_text(visitor_text=visit)
    if not ys:
        raise _ScaleError("no text found in the PDF")
    content_bottom = max(0.0, min(ys) - 36.0)  # keep a 0.5in bottom margin
    used_h = page_h - content_bottom
    scale = min(1.0, target_h / used_h, target_w / page_w)

    out_page = PageObject.create_blank_page(width=target_w, height=target_h)
    tx = (target_w - page_w * scale) / 2
    op = Transformation().translate(0, -content_bottom).scale(scale, scale).translate(tx, 0)
    out_page.merge_transformed_page(page, op)
    writer = PdfWriter()
    writer.add_page(out_page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), scale
