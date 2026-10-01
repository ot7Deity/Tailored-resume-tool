from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import compiler, config, pdf_import, pipeline, storage
from .llm import LLMError
from .tex_parser import parse_resume

app = FastAPI(title="Tailored Resume Tool")


# ---------- master ----------

def _master_summary(tex: str | None) -> dict:
    if not tex:
        return {"exists": False, "tex": "", "bullets": [], "skills": []}
    parsed = parse_resume(tex)
    first, last = pipeline.resolve_name(parsed)
    return {
        "exists": True,
        "tex": tex,
        "detected_name": {"first": parsed.first_name, "last": parsed.last_name},
        "name": {"first": first, "last": last},
        "bullets": [{"id": b.id, "section": b.section, "text": b.text} for b in parsed.bullets],
        "skills": [{"id": s.id, "label": s.label, "text": s.text} for s in parsed.skills],
        "backups": storage.list_backups()[:10],
        "tectonic": bool(compiler.find_tectonic()),
    }


class MasterIn(BaseModel):
    tex: str


class ProfileIn(BaseModel):
    first_name: str = ""
    last_name: str = ""


@app.get("/api/master")
def get_master():
    return _master_summary(storage.read_master())


@app.put("/api/master")
def put_master(body: MasterIn):
    if "\\begin{document}" not in body.tex:
        raise HTTPException(400, "That doesn't look like a full LaTeX document (no \\begin{document}).")
    storage.save_master(body.tex)
    return _master_summary(body.tex)


def _is_pdf(file: UploadFile, raw: bytes) -> bool:
    return raw.startswith(b"%PDF") or (file.filename or "").lower().endswith(".pdf")


@app.post("/api/master/upload")
def upload_master(file: UploadFile = File(...)):
    raw = file.file.read()
    if _is_pdf(file, raw):
        # Convert to LaTeX but don't save: the user reviews it in the editor first.
        try:
            tex = pdf_import.resume_pdf_to_latex(raw)
        except pdf_import.PDFError as e:
            raise HTTPException(400, str(e))
        except LLMError as e:
            raise HTTPException(502, str(e))
        return _master_summary(tex) | {"unsaved": True, "converted_from_pdf": True}
    try:
        tex = raw.decode("utf-8")
    except UnicodeDecodeError:
        tex = raw.decode("latin-1")
    return put_master(MasterIn(tex=tex))


@app.get("/api/master/backups/{name}")
def get_backup(name: str):
    tex = storage.read_backup(name)
    if tex is None:
        raise HTTPException(404, "Backup not found")
    return {"name": name, "tex": tex}


@app.put("/api/profile")
def put_profile(body: ProfileIn):
    storage.save_profile({"first_name": body.first_name.strip(), "last_name": body.last_name.strip()})
    return _master_summary(storage.read_master())


@app.get("/api/master/pdf")
def master_pdf():
    tex = storage.read_master()
    if not tex:
        raise HTTPException(404, "No master resume saved")
    result = compiler.compile_tex(tex, storage.assets_dir())
    if not result.ok:
        raise HTTPException(422, result.log)
    return Response(result.pdf, media_type="application/pdf")


# ---------- tailoring ----------

class TailorIn(BaseModel):
    jd: str
    company: str = ""


class EditToggle(BaseModel):
    bullet_id: str
    accepted: bool
    new_text: str = ""


class SkillToggle(BaseModel):
    line_id: str
    accepted: bool


class ApplyIn(BaseModel):
    edits: list[EditToggle] = []
    skills: list[SkillToggle] = []
    company: str | None = None
    filename: str | None = None


@app.post("/api/tailor")
def tailor(body: TailorIn):
    if len(body.jd.strip()) < 50:
        raise HTTPException(400, "Paste the full job description.")
    try:
        return pipeline.run_tailor(body.jd, body.company)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(502, str(e))


@app.post("/api/jd/extract")
def extract_jd(file: UploadFile = File(...)):
    raw = file.file.read()
    if not _is_pdf(file, raw):
        try:
            return {"text": raw.decode("utf-8")}
        except UnicodeDecodeError:
            raise HTTPException(400, "Upload a PDF or a plain-text file.")
    try:
        return {"text": pdf_import.extract_jd_text(raw)}
    except pdf_import.PDFError as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(502, str(e))


@app.get("/api/runs")
def runs():
    return storage.list_runs()


def _load(run_id: str) -> dict:
    try:
        return storage.load_run(run_id)
    except FileNotFoundError:
        raise HTTPException(404, "Run not found")


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _load(run_id)


@app.post("/api/runs/{run_id}/apply")
def apply(run_id: str, body: ApplyIn):
    _load(run_id)
    return pipeline.apply_changes(run_id, [e.model_dump() for e in body.edits],
                                  [s.model_dump() for s in body.skills], body.company, body.filename)


@app.post("/api/runs/{run_id}/explain")
def explain(run_id: str):
    _load(run_id)
    return pipeline.refresh_explanation(run_id)


@app.get("/api/runs/{run_id}/pdf")
def run_pdf(run_id: str):
    _load(run_id)
    p = storage.run_file(run_id, "tailored.pdf")
    if not p.exists():
        raise HTTPException(404, "PDF not compiled")
    return FileResponse(p, media_type="application/pdf", content_disposition_type="inline")


@app.get("/api/runs/{run_id}/download")
def download(run_id: str):
    run = _load(run_id)
    p = storage.run_file(run_id, "tailored.pdf")
    if not p.exists():
        raise HTTPException(404, "PDF not compiled")
    return FileResponse(p, media_type="application/pdf", filename=run["filename"])


@app.get("/api/runs/{run_id}/tex")
def download_tex(run_id: str):
    run = _load(run_id)
    return FileResponse(storage.run_file(run_id, "tailored.tex"), media_type="application/x-tex",
                        filename=run["filename"][:-4] + ".tex")


@app.get("/")
def index():
    return RedirectResponse("/index.html")


app.mount("/", StaticFiles(directory=config.STATIC_DIR), name="static")
