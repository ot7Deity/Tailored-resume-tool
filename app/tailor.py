"""JD analysis, bullet tailoring (with a hard change budget) and splicing into the .tex."""
import difflib
import math
from typing import Literal

from pydantic import BaseModel

from . import config, llm
from .latex_utils import escape_latex, latex_to_text
from .tex_parser import ParsedResume


# ---------- structured output schemas ----------

class Keyword(BaseModel):
    term: str
    category: Literal["hard_skill", "tool", "soft_skill", "domain", "certification", "other"]
    importance: int  # 3 = must-have, 2 = important, 1 = nice-to-have
    variants: list[str]


class JDAnalysis(BaseModel):
    company: str
    role: str
    summary: str
    keywords: list[Keyword]


class XYZ(BaseModel):
    x_accomplished: str
    y_measured_by: str
    z_by_doing: str


class BulletEdit(BaseModel):
    bullet_id: str
    new_text: str
    xyz: XYZ
    keywords_added: list[str]
    metric_estimated: bool
    estimated_metrics: list[str]
    reason: str


class SkillsEdit(BaseModel):
    line_id: str
    add_keywords: list[str]
    reason: str


class TailorPlan(BaseModel):
    edits: list[BulletEdit]
    skills_edits: list[SkillsEdit]


class Explanation(BaseModel):
    verdict: Literal["tailored", "master", "tie"]
    headline: str
    reasons: list[str]
    advice: list[str]


# ---------- prompts ----------

ANALYZE_SYSTEM = """You are an expert technical recruiter and ATS (applicant tracking system) specialist.
Extract what an ATS and a recruiter would screen for in a job description.

- company: the hiring company's name as it would appear in a filename (e.g. "Google", "Goldman Sachs"). If it truly is not stated, use "Company".
- role: the job title.
- summary: one sentence describing the role.
- keywords: 15-35 concrete, screenable keywords (languages, frameworks, tools, platforms, methodologies, domain terms, certifications, and the few soft skills that are explicitly emphasized). Use the exact wording from the JD for `term`. importance: 3 = required / repeated / in the title, 2 = clearly preferred, 1 = nice-to-have. `variants` lists common alternate spellings or abbreviations an ATS would also accept (e.g. "JavaScript" -> ["JS"], "Amazon Web Services" -> ["AWS"]). Do not list generic filler words like "team" or "communication" unless the JD stresses them."""

TAILOR_SYSTEM = """You tailor an existing resume to a specific job description for ATS alignment while keeping it almost identical to the original.

Hard rules:
1. Change as little as possible. Return AT MOST {max_edits} bullet edits, ranked most impactful first. Pick bullets that are relevant to the job but under-use its keywords. Never touch bullets that are already strong matches.
2. Every rewritten bullet MUST follow the Google XYZ formula: "Accomplished [X] as measured by [Y], by doing [Z]". Start with a strong past-tense action verb, include a concrete measurable result (%, $, time saved, users, scale, latency, etc.), and naturally work in the missing JD keywords that genuinely fit the experience. Keep the original facts, employer context and technologies; do not invent a different project.
3. Keep each rewritten bullet within about ±20% of the original length so the resume keeps its page count. One sentence, no trailing period needed if the original has none.
4. Metrics: reuse the original numbers when they exist. When the original has no number, you may estimate a realistic, conservative metric - then set metric_estimated=true and list each estimated figure in estimated_metrics so the candidate can verify it.
5. new_text is plain text (no LaTeX). You may wrap a short phrase in **double asterisks** for bold only if the original bullet used bold.
6. skills_edits: optionally add up to 5 missing JD keywords in total to EXISTING skills lines (use the line_id), only when the candidate's experience plausibly supports them. Never remove skills.
7. reason: one short sentence on why this change improves the match."""

EXPLAIN_SYSTEM = """You are an ATS expert explaining to a candidate why their tailored resume scores differently from their master resume against a job description.
You are given both resume texts and a deterministic score breakdown. Be concrete and brief:
- verdict: which version aligns better with the job ("tailored", "master", or "tie"), consistent with the scores unless there is a strong reason otherwise.
- headline: one sentence summary.
- reasons: 3-6 bullet-style reasons for the score gap (keywords gained, stronger metrics, better XYZ phrasing, anything still weak).
- advice: 2-4 specific, honest suggestions for closing remaining gaps (e.g. skills the candidate may truly have but did not list)."""


# ---------- LLM steps ----------

def analyze_jd(jd: str) -> JDAnalysis:
    result = llm.call_structured(ANALYZE_SYSTEM, f"<job_description>\n{jd}\n</job_description>", JDAnalysis,
                                 effort="medium")
    for kw in result.keywords:
        kw.importance = max(1, min(3, kw.importance))
    return result


def max_edits_for(parsed: ParsedResume) -> int:
    return max(1, math.ceil(len(parsed.bullets) * config.MAX_BULLET_RATIO))


def plan_tailoring(parsed: ParsedResume, jd: str, analysis: JDAnalysis, missing: list[str]) -> TailorPlan:
    max_edits = max_edits_for(parsed)
    bullets = "\n".join(f"[{b.id}] ({b.section}) {b.text}" for b in parsed.bullets)
    skills = "\n".join(f"[{s.id}] {s.label}: {s.text}" for s in parsed.skills) or "(no skills section found)"
    keywords = "\n".join(f"- {k.term} (importance {k.importance}, {k.category})" for k in analysis.keywords)
    user = f"""<job_description>
{jd}
</job_description>

<jd_keywords>
{keywords}
</jd_keywords>

<keywords_missing_from_resume>
{", ".join(missing) or "(none)"}
</keywords_missing_from_resume>

<resume_bullets>
{bullets}
</resume_bullets>

<resume_skills_lines>
{skills}
</resume_skills_lines>

Return at most {max_edits} bullet edits."""
    return llm.call_structured(TAILOR_SYSTEM.format(max_edits=max_edits), user, TailorPlan, effort="high")


def explain(master_text: str, tailored_text: str, master_score: dict, tailored_score: dict,
            comparison: dict, analysis: dict) -> Explanation:
    user = f"""Role: {analysis['role']} at {analysis['company']}

<scores>
master: {master_score}
tailored: {tailored_score}
comparison: {comparison}
</scores>

<master_resume>
{master_text}
</master_resume>

<tailored_resume>
{tailored_text}
</tailored_resume>"""
    return llm.call_structured(EXPLAIN_SYSTEM, user, Explanation, effort="low")


# ---------- budget enforcement ----------

def _words(text: str) -> list[str]:
    return text.lower().split()


def change_ratio(parsed: ParsedResume, bullet_texts: dict[str, str], skill_adds: dict[str, list[str]]) -> float:
    """Share of the resume's words that differ from the master."""
    total = len(_words(parsed.plain_text())) or 1
    changed = 0
    for bid, new in bullet_texts.items():
        b = parsed.bullet(bid)
        if not b:
            continue
        sm = difflib.SequenceMatcher(a=_words(b.text), b=_words(new), autojunk=False)
        same = sum(block.size for block in sm.get_matching_blocks())
        changed += max(len(_words(b.text)), len(_words(new))) - same
    for adds in skill_adds.values():
        changed += sum(len(k.split()) for k in adds)
    return changed / total


def enforce_budget(parsed: ParsedResume, plan: TailorPlan) -> tuple[list[dict], list[dict]]:
    """Validate the plan and trim it to the bullet-count and word-change budgets.

    Returns (edits, skills_edits) as plain dicts with `accepted`/`dropped_reason` fields.
    """
    max_edits = max_edits_for(parsed)
    edits: list[dict] = []
    seen = set()
    kept_texts: dict[str, str] = {}
    for e in plan.edits:
        b = parsed.bullet(e.bullet_id)
        text = e.new_text.strip()
        if not b or e.bullet_id in seen or not text or text == b.text:
            continue
        seen.add(e.bullet_id)
        item = e.model_dump()
        item.update(original_text=b.text, section=b.section, new_text=text, accepted=True, dropped_reason=None)
        if len(kept_texts) >= max_edits:
            item.update(accepted=False, dropped_reason=f"Over the {max_edits}-bullet change limit")
        else:
            trial = {**kept_texts, e.bullet_id: text}
            if kept_texts and change_ratio(parsed, trial, {}) > config.MAX_CHANGE_RATIO:
                item.update(accepted=False,
                            dropped_reason=f"Would exceed the {config.MAX_CHANGE_RATIO:.0%} change budget")
            else:
                kept_texts[e.bullet_id] = text
        edits.append(item)

    skills: list[dict] = []
    budget_left = 5
    for s in plan.skills_edits:
        line = parsed.skill(s.line_id)
        if not line:
            continue
        existing = {x.strip().lower() for x in line.text.split(",")}
        adds = []
        for k in s.add_keywords:
            k = k.strip()
            if k and k.lower() not in existing and k.lower() not in line.text.lower() and budget_left > 0:
                adds.append(k)
                existing.add(k.lower())
                budget_left -= 1
        if adds:
            skills.append({"line_id": line.id, "label": line.label, "original_text": line.text,
                           "add_keywords": adds, "reason": s.reason, "accepted": True})
    return edits, skills


# ---------- splicing ----------

def build_tex(parsed: ParsedResume, edits: list[dict], skills: list[dict]) -> str:
    """Replace only the accepted spans; everything else stays byte-identical."""
    replacements: list[tuple[int, int, str]] = []
    for e in edits:
        if not e.get("accepted"):
            continue
        b = parsed.bullet(e["bullet_id"])
        if b:
            replacements.append((b.start, b.end, escape_latex(e["new_text"])))
    for s in skills:
        if not s.get("accepted") or not s.get("add_keywords"):
            continue
        line = parsed.skill(s["line_id"])
        if line:
            addition = ", " + ", ".join(escape_latex(k) for k in s["add_keywords"])
            replacements.append((line.end, line.end, addition))

    src = parsed.source
    for start, end, text in sorted(replacements, key=lambda r: r[0], reverse=True):
        src = src[:start] + text + src[end:]
    return src


def tailored_bullets(parsed: ParsedResume, edits: list[dict]) -> list[str]:
    new = {e["bullet_id"]: e["new_text"] for e in edits if e.get("accepted")}
    return [latex_to_text(escape_latex(new[b.id])) if b.id in new else b.text for b in parsed.bullets]
