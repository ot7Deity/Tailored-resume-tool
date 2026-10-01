"""Compile .tex -> PDF with Tectonic."""
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass
class CompileResult:
    ok: bool
    pdf: bytes | None
    log: str


def find_tectonic() -> str | None:
    if config.TECTONIC_PATH and Path(config.TECTONIC_PATH).exists():
        return config.TECTONIC_PATH
    local = config.TOOLS_DIR / "tectonic.exe"
    if local.exists():
        return str(local)
    return shutil.which("tectonic")


def xetex_compat(tex: str) -> str:
    """Tectonic runs XeTeX; guard pdfTeX-only primitives common in resume templates
    (e.g. Jake's Resume uses \\input{glyphtounicode} and \\pdfgentounicode=1)."""
    tex = re.sub(r"^(\s*)(\\input\s*\{glyphtounicode(?:\.tex)?\})",
                 r"\1\\ifdefined\\pdfglyphtounicode\2\\fi", tex, flags=re.M)
    return re.sub(r"^(\s*)(\\pdfgentounicode\s*=\s*1)", r"\1\\ifdefined\\pdfgentounicode\2\\fi",
                  tex, flags=re.M)


def compile_tex(tex: str, assets: Path | None = None) -> CompileResult:
    exe = find_tectonic()
    if not exe:
        return CompileResult(False, None,
                             "Tectonic not found. Install it (see README) or set TECTONIC_PATH in .env.")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        if assets and assets.exists():
            for f in assets.iterdir():
                if f.is_file():
                    shutil.copy2(f, tmpdir / f.name)
        (tmpdir / "resume.tex").write_text(xetex_compat(tex), encoding="utf-8")
        try:
            proc = subprocess.run([exe, "-X", "compile", "resume.tex"], cwd=tmpdir,
                                  capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return CompileResult(False, None, "Tectonic timed out after 5 minutes.")
        log = (proc.stdout + "\n" + proc.stderr).strip()
        pdf = tmpdir / "resume.pdf"
        if proc.returncode != 0 or not pdf.exists():
            return CompileResult(False, None, "\n".join(log.splitlines()[-40:]))
        return CompileResult(True, pdf.read_bytes(), "\n".join(log.splitlines()[-10:]))


def build_filename(first: str, last: str, company: str, term: str | None = None) -> str:
    """Last_First_Company_Spring2028.pdf"""
    def clean(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", s or "")
    parts = [clean(last), clean(first), clean(company) or "Company", clean(term or config.TERM_LABEL)]
    return "_".join(p for p in parts if p) + ".pdf"
