"""JD analysis, bullet tailoring (with a hard change budget) and splicing into the .tex."""
import difflib
import math
import re
from typing import Literal

from pydantic import BaseModel

from . import config, llm
from .latex_utils import escape_latex, latex_to_text, mask_comments
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

TAILOR_SYSTEM = """You tailor an existing resume to a specific job description to maximize its ATS match score. The target is a score of at least {target}/100.

How the score works: 60% keyword coverage (JD keywords found anywhere in the resume, weighted by importance), 20% must-have hard skills, 20% bullet quality (share of bullets with a number, a strong past-tense action verb first, and an XYZ connector such as "by", "using", "through", "via", "resulting in").

Rules:
1. Budget: return AT MOST {max_edits} bullet edits, and the words changed in bullets must stay under {max_change:.0%} of the resume (skills additions do not count toward this). Use that budget fully and spread the JD keywords across as many bullets as possible rather than piling them into one. Prefer bullets that are relevant to the job, miss its keywords, or lack a metric or action verb. Small, targeted rewordings that slot in 1-3 keywords each are better than full rewrites.
2. Keep each original bullet's project, employer and core facts, and keep as much of its wording as you can. You may widen its scope: larger scale, more ownership, more of the stack touched. Work the missing JD keywords into bullets wherever that project could plausibly have involved them, including technologies the candidate does not list yet, and use the exact JD wording for each keyword.
3. Every rewritten bullet follows the Google XYZ formula: "Accomplished [X] as measured by [Y], by doing [Z]". Start with a strong past-tense action verb, include a concrete measurable result (%, $, time saved, users, scale, latency, etc.) and use an XYZ connector.
4. Metrics: reuse the original numbers when they exist. When there are none, invent a realistic, specific metric the candidate could credibly defend in an interview, then set metric_estimated=true and list each invented figure in estimated_metrics.
5. Length: keep the resume on one page. A rewritten bullet may be at most about 25% longer than the original. One sentence, with no trailing period if the original has none.
6. new_text is plain text (no LaTeX). You may wrap a short phrase in **double asterisks** for bold only if the original bullet used bold.
7. skills_edits: add up to {max_skills} missing JD keywords in total to EXISTING skills lines (use the line_id), putting each on the best-fitting line. Prioritize importance-3 and importance-2 keywords and hard skills/tools. Never remove skills.
8. reason: one short sentence on why this change improves the match."""

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


def plan_tailoring(parsed: ParsedResume, jd: str, analysis: JDAnalysis, missing: list[str],
                   feedback: str = "") -> TailorPlan:
    """`feedback` describes a previous attempt (its edits, score and remaining gaps) for a refinement round."""
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

{feedback}
Return at most {max_edits} bullet edits."""
    system = TAILOR_SYSTEM.format(target=round(config.TARGET_SCORE), max_edits=max_edits,
                                  max_change=config.MAX_CHANGE_RATIO, max_skills=config.MAX_SKILL_ADDS)
    return llm.call_structured(system, user, TailorPlan, effort="high")


def refinement_feedback(edits: list[dict], skills: list[dict], score: dict, change: float) -> str:
    """Describe the previous attempt so the next round can close the remaining gaps."""
    kept = "\n".join(f"[{e['bullet_id']}] {e['new_text']}" for e in edits if e.get("accepted")) or "(none)"
    dropped = "\n".join(f"[{e['bullet_id']}] {e['dropped_reason']}" for e in edits if not e.get("accepted")) or "(none)"
    added = ", ".join(k for s in skills if s.get("accepted") for k in s["add_keywords"]) or "(none)"
    return f"""
<previous_attempt>
That attempt scored {score['overall']}/100 (breakdown: {score['breakdown']}); the target is {round(config.TARGET_SCORE)}.
It changed {change:.0%} of the resume's words (limit {config.MAX_CHANGE_RATIO:.0%}).
Accepted bullet edits:
{kept}
Edits dropped by the budget:
{dropped}
Skills added: {added}
Keywords STILL missing after that attempt: {", ".join(score['missing']) or "(none)"}
</previous_attempt>
Return a complete revised plan (it replaces the previous one, it is not added to it). Keep the edits that worked, cover the still-missing keywords, and make cheaper edits if the previous ones were dropped for exceeding the budget.
"""


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


def change_ratio(parsed: ParsedResume, bullet_texts: dict[str, str]) -> float:
    """Share of the resume's words that differ from the master, counting bullet rewrites only
    (skills additions have their own MAX_SKILL_ADDS cap)."""
    total = len(_words(parsed.plain_text())) or 1
    changed = 0
    for bid, new in bullet_texts.items():
        b = parsed.bullet(bid)
        if not b:
            continue
        sm = difflib.SequenceMatcher(a=_words(b.text), b=_words(new), autojunk=False)
        same = sum(block.size for block in sm.get_matching_blocks())
        changed += max(len(_words(b.text)), len(_words(new))) - same
    return changed / total


def enforce_budget(parsed: ParsedResume, plan: TailorPlan) -> tuple[list[dict], list[dict]]:
    """Validate the plan and trim it to the bullet-count, skill-count and word-change budgets.

    Bullets are accepted in the plan's order while their word change stays under budget;
    skills additions don't count toward it and are capped by MAX_SKILL_ADDS instead.
    Returns (edits, skills_edits) as plain dicts with `accepted`/`dropped_reason` fields.
    """
    skills: list[dict] = []
    budget_left = config.MAX_SKILL_ADDS
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
            if kept_texts and change_ratio(parsed, trial) > config.MAX_CHANGE_RATIO:
                item.update(accepted=False,
                            dropped_reason=f"Would exceed the {config.MAX_CHANGE_RATIO:.0%} change budget")
            else:
                kept_texts[e.bullet_id] = text
        edits.append(item)
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


# ---------- graduation date ----------

_EDU_RE = re.compile(r"\\section\*?\{[^}]*Education[^}]*\}", re.I)
_SECTION_RE = re.compile(r"\\section\*?\{")
# end of a date range: "Aug. 2024 -- May 2028", "2024 – Expected May 2028"
_RANGE_END_RE = re.compile(r"(--|–|—|\bto\b)(\s*)(?:Expected\s+)?(?:[A-Z][a-z]{2,8}\.?\s+)?(19|20)\d\d")


def detect_grad_date(tex: str) -> str | None:
    """The end date of the first Education entry, e.g. "May 2028"."""
    span = _education_span(tex)
    if not span:
        return None
    m = _RANGE_END_RE.search(tex, *span)
    return m.group(0)[len(m.group(1)) + len(m.group(2)):].strip() if m else None


def set_grad_date(tex: str, grad: str) -> str:
    """Replace the end date of the first Education entry's date range with `grad`."""
    span = _education_span(tex)
    if not grad or not span:
        return tex
    m = _RANGE_END_RE.search(tex, *span)
    if not m:
        return tex
    return tex[:m.start()] + m.group(1) + m.group(2) + escape_latex(grad) + tex[m.end():]


def _education_span(tex: str) -> tuple[int, int] | None:
    masked = mask_comments(tex)
    m = _EDU_RE.search(masked)
    if not m:
        return None
    nxt = _SECTION_RE.search(masked, m.end())
    return m.end(), nxt.start() if nxt else len(tex)
