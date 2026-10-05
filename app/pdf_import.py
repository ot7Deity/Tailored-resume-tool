"""PDF input: convert a resume PDF to an editable LaTeX master, and extract JD text."""
import base64
import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from . import config, llm, storage

TEMPLATE_PATH = config.ROOT / "examples" / "sample_resume.tex"

PDF_TO_LATEX_SYSTEM = """You convert a resume PDF into a LaTeX source file that uses the exact template provided.

Rules:
1. Reproduce ALL of the resume's content: every section, heading, date, location, link and bullet. Keep the wording verbatim. Do not rewrite, shorten, embellish or reorder anything, and do not invent content.
2. Use the template's preamble unchanged, and its commands for structure: \\resumeSubheading for jobs/education, \\resumeProjectHeading for projects, \\resumeItem{...} for every bullet, and the Technical Skills block (\\textbf{Label}{: a, b, c}) for skills.
3. Map each section of the PDF to a \\section{...} with the PDF's own section titles.
4. Escape LaTeX special characters in the content (& % $ # _ { } ~ ^).
5. Output only the complete .tex file, from \\documentclass to \\end{document}. No commentary, no Markdown code fences."""


class PDFError(ValueError):
    pass


def _check_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF"):
        raise PDFError("That file is not a valid PDF.")
    if len(data) > 30 * 1024 * 1024:
        raise PDFError("PDF is too large (max 30 MB).")


def extract_text(data: bytes) -> str:
    """Local text extraction (no API call). Empty for scanned/image-only PDFs."""
    _check_pdf(data)
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError) as e:
        raise PDFError(f"Could not read the PDF: {e}") from e
    text = "\n\n".join(p.strip() for p in pages)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_jd_text(data: bytes) -> str:
    text = extract_text(data)
    if len(text) >= 50:
        return text
    # Likely a scanned PDF: let Claude read it (once per distinct file).
    cached = storage.cache_get("jd_text", data)
    if cached is not None:
        return cached
    text = llm.call_text(
        "Transcribe the job description in this PDF as plain text. Output only the text, nothing else.",
        [_pdf_block(data), {"type": "text", "text": "Transcribe this job description."}],
        effort="low", max_tokens=16000).strip()
    storage.cache_put("jd_text", data, text)
    return text


def resume_pdf_to_latex(data: bytes) -> str:
    _check_pdf(data)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    tex = llm.call_text(PDF_TO_LATEX_SYSTEM, [
        _pdf_block(data),
        {"type": "text", "text": f"<template>\n{template}\n</template>\n\n"
                                 "Convert the attached resume PDF into a .tex file using this template."},
    ], effort="medium")
    tex = re.sub(r"^\s*```(?:latex|tex)?\s*\n", "", tex)
    tex = re.sub(r"\n```\s*$", "", tex).strip() + "\n"
    if "\\begin{document}" not in tex or "\\end{document}" not in tex:
        raise PDFError("The PDF could not be converted to a complete LaTeX document. Try again.")
    return tex


def _pdf_block(data: bytes) -> dict:
    return {"type": "document",
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.standard_b64encode(data).decode("ascii")}}
