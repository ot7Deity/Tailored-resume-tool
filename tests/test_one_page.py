"""One-page guarantee. Uses the real Tectonic compiler (skipped when it isn't installed)."""
from pathlib import Path

import pytest

from app import compiler, one_page

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_resume.tex").read_text(encoding="utf-8")
needs_tectonic = pytest.mark.skipif(not compiler.find_tectonic(), reason="Tectonic not installed")


def _section(start, end):
    i = SAMPLE.index(start)
    return SAMPLE[i:SAMPLE.index(end, i)]


def test_smaller_font_steps_class_option():
    assert "[letterpaper,10pt]" in one_page.level_tex(SAMPLE, 2)
    assert one_page._smaller_font(r"\documentclass{article}", 1) == r"\documentclass{article}"


def test_fit_block_injected_before_document():
    out = one_page.level_tex(SAMPLE, 1)
    assert out.index(one_page.FIT_MARK) < out.index(r"\begin{document}")
    assert out.count(r"\begin{document}") == 1


@needs_tectonic
def test_already_one_page_is_untouched():
    r = one_page.fit_to_one_page(SAMPLE)
    assert r.ok and r.fit_level == 0 and r.tex == SAMPLE and one_page.page_count(r.pdf) == 1


@needs_tectonic
def test_slightly_long_resume_fits_with_latex_tweaks():
    proj = _section(r"\section{Projects}", "%-----------PROGRAMMING")
    tex = SAMPLE.replace(proj, proj + "\n" + proj)
    r = one_page.fit_to_one_page(tex)
    assert r.ok and r.natural_pages == 2 and 0 < r.fit_level < one_page.SCALE_LEVEL
    assert one_page.page_count(r.pdf) == 1


@needs_tectonic
def test_very_long_resume_is_scaled_to_one_page():
    exp = _section(r"\section{Experience}", "%-----------PROJECTS")
    tex = SAMPLE.replace(exp, exp + ("\n" + exp) * 3)
    r = one_page.fit_to_one_page(tex)
    assert r.ok and r.natural_pages >= 3 and r.fit_level == one_page.SCALE_LEVEL
    assert one_page.page_count(r.pdf) == 1
    assert "Technical Skills" in one_page.PdfReader(__import__("io").BytesIO(r.pdf)).pages[0].extract_text()
