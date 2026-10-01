"""End-to-end tailoring pipeline: analyze -> tailor -> splice -> score -> compile -> explain."""
from datetime import datetime

from . import compiler, config, storage, tailor
from .latex_utils import latex_to_text
from .llm import LLMError
from .scorer import compare, score_resume
from .tex_parser import ParsedResume, parse_resume


def resolve_name(parsed: ParsedResume) -> tuple[str, str]:
    profile = storage.read_profile()
    return (profile.get("first_name") or parsed.first_name,
            profile.get("last_name") or parsed.last_name)


def _score_pair(parsed: ParsedResume, run: dict, tailored_tex: str) -> None:
    keywords = run["keywords"]
    master = score_resume(parsed.plain_text(), [b.text for b in parsed.bullets], keywords)
    tailored_text = latex_to_text(tailored_tex[parsed.body_start:])
    tailored = score_resume(tailored_text, tailor.tailored_bullets(parsed, run["edits"]), keywords)
    run["scores"] = {"master": master, "tailored": tailored}
    run["comparison"] = compare(master, tailored, keywords)
    accepted = {e["bullet_id"]: e["new_text"] for e in run["edits"] if e["accepted"]}
    adds = {s["line_id"]: s["add_keywords"] for s in run["skills_edits"] if s["accepted"]}
    run["change_ratio"] = round(tailor.change_ratio(parsed, accepted, adds), 4)
    run["bullets_changed"] = len(accepted)


def _compile(run: dict, tex: str) -> bytes | None:
    result = compiler.compile_tex(tex, storage.assets_dir())
    run["compile"] = {"ok": result.ok, "log": result.log}
    return result.pdf


def _explain(parsed: ParsedResume, run: dict, tailored_tex: str) -> None:
    try:
        exp = tailor.explain(parsed.plain_text(), latex_to_text(tailored_tex[parsed.body_start:]),
                             run["scores"]["master"], run["scores"]["tailored"], run["comparison"],
                             {"company": run["company"], "role": run["role"]})
        run["explanation"] = exp.model_dump()
        run["explanation_error"] = None
    except LLMError as e:
        run["explanation"] = None
        run["explanation_error"] = str(e)
    run["explanation_stale"] = False


def run_tailor(jd: str, company_override: str = "") -> dict:
    master_tex = storage.read_master()
    if not master_tex:
        raise ValueError("No master resume saved yet.")
    parsed = parse_resume(master_tex)
    if not parsed.bullets:
        raise ValueError("No bullets found in the master resume (expected \\resumeItem{...} or \\item ...).")

    analysis = tailor.analyze_jd(jd)
    keywords = [k.model_dump() for k in analysis.keywords]
    master_score = score_resume(parsed.plain_text(), [b.text for b in parsed.bullets], keywords)

    plan = tailor.plan_tailoring(parsed, jd, analysis, master_score["missing"])
    edits, skills = tailor.enforce_budget(parsed, plan)

    first, last = resolve_name(parsed)
    company = company_override.strip() or analysis.company
    run = {
        "run_id": storage.new_run_id(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "jd": jd,
        "company": company,
        "role": analysis.role,
        "summary": analysis.summary,
        "first_name": first,
        "last_name": last,
        "filename": compiler.build_filename(first, last, company),
        "keywords": keywords,
        "edits": edits,
        "skills_edits": skills,
        "bullet_count": len(parsed.bullets),
        "max_edits": tailor.max_edits_for(parsed),
        "max_change_ratio": config.MAX_CHANGE_RATIO,
    }
    tex = tailor.build_tex(parsed, edits, skills)
    _score_pair(parsed, run, tex)
    pdf = _compile(run, tex)
    _explain(parsed, run, tex)
    storage.save_run(run, master_tex=master_tex, tailored_tex=tex, pdf=pdf)
    return run


def apply_changes(run_id: str, edits: list[dict], skills: list[dict], company: str | None,
                  filename: str | None) -> dict:
    """Re-splice with the user's accept/reject toggles and text overrides. No new tailoring call."""
    run = storage.load_run(run_id)
    master_tex = storage.run_file(run_id, "master.tex").read_text(encoding="utf-8")
    parsed = parse_resume(master_tex)

    by_id = {e["bullet_id"]: e for e in edits}
    for e in run["edits"]:
        upd = by_id.get(e["bullet_id"])
        if upd is None:
            continue
        e["accepted"] = bool(upd.get("accepted"))
        text = (upd.get("new_text") or "").strip()
        if text and text != e["new_text"]:
            e["new_text"] = text
            e["user_edited"] = True
    s_by_id = {s["line_id"]: s for s in skills}
    for s in run["skills_edits"]:
        upd = s_by_id.get(s["line_id"])
        if upd is not None:
            s["accepted"] = bool(upd.get("accepted"))

    if company is not None and company.strip():
        run["company"] = company.strip()
    run["filename"] = (filename.strip() if filename and filename.strip()
                       else compiler.build_filename(run["first_name"], run["last_name"], run["company"]))
    if not run["filename"].lower().endswith(".pdf"):
        run["filename"] += ".pdf"

    tex = tailor.build_tex(parsed, run["edits"], run["skills_edits"])
    _score_pair(parsed, run, tex)
    pdf = _compile(run, tex)
    run["explanation_stale"] = True
    storage.save_run(run, tailored_tex=tex, pdf=pdf)
    return run


def refresh_explanation(run_id: str) -> dict:
    run = storage.load_run(run_id)
    parsed = parse_resume(storage.run_file(run_id, "master.tex").read_text(encoding="utf-8"))
    tex = storage.run_file(run_id, "tailored.tex").read_text(encoding="utf-8")
    _explain(parsed, run, tex)
    storage.save_run(run)
    return run
