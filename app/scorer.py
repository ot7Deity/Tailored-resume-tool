"""Deterministic ATS-style scoring of a resume against JD keywords.

The same function scores the master and the tailored resume, so the two numbers
are directly comparable. Weights: keyword coverage 60%, must-have hard skills 20%,
bullet quality (metrics / action verbs / XYZ structure) 20%.
"""
import re

WEIGHTS = {"keyword_coverage": 0.6, "hard_skill_match": 0.2, "bullet_quality": 0.2}
HARD_CATEGORIES = {"hard_skill", "tool", "certification"}

ACTION_VERBS = set("""
accelerated accomplished achieved acquired administered advanced analyzed architected
assembled automated boosted built captured championed coached collaborated compiled
conceived conducted consolidated constructed converted coordinated created cut debugged
decreased defined delivered deployed designed developed devised diagnosed digitized directed
discovered doubled drove eliminated enabled engineered enhanced established evaluated
executed expanded expedited facilitated forecasted formulated founded generated grew guided
halved headed identified implemented improved increased initiated innovated integrated
introduced invented launched led leveraged maintained managed maximized mentored migrated
minimized modeled modernized monitored negotiated optimized orchestrated organized
outperformed overhauled owned partnered piloted pioneered planned presented prioritized
produced programmed prototyped published raised rebuilt redesigned reduced refactored
regained reengineered replaced researched resolved restructured revamped scaled secured
simplified slashed solved spearheaded standardized streamlined strengthened supervised
surpassed synthesized taught tested trained transformed tripled troubleshot tuned unified
upgraded validated visualized won wrote
""".split())

_METRIC_RE = re.compile(
    r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|dozens?|hundreds?|thousands?|millions?)\b",
    re.I,
)
_XYZ_RE = re.compile(
    r"\b(?:by|through|via|using|resulting in|leading to|as measured by|achieving|which)\b", re.I
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _forms(kw: dict) -> list[str]:
    forms = [kw.get("term", "")] + list(kw.get("variants") or [])
    return [f.strip().lower() for f in forms if f and f.strip()]


def keyword_found(text_norm: str, kw: dict) -> bool:
    for form in _forms(kw):
        pat = r"(?<![a-z0-9])" + re.escape(form) + r"(?:s|es|'s)?(?![a-z0-9+#])"
        if re.search(pat, text_norm):
            return True
        # tolerate punctuation differences, e.g. "React.js" vs "ReactJS", "CI/CD" vs "CI CD"
        # (only for forms that contain punctuation/spaces, so "java" never matches "javascript")
        squashed = re.sub(r"[^a-z0-9+#]", "", form)
        if squashed != form and len(squashed) >= 4 and squashed in re.sub(r"[^a-z0-9+#]", "", text_norm):
            return True
    return False


def _has_metric(text: str) -> bool:
    return bool(_METRIC_RE.search(text))


def _has_action_verb(text: str) -> bool:
    words = text.split()
    if not words:
        return False
    first = re.sub(r"[^a-z]", "", words[0].lower())
    return first in ACTION_VERBS or (first.endswith("ed") and len(first) > 4)


def _has_xyz_connector(text: str) -> bool:
    return bool(_XYZ_RE.search(text))


QUALITY_PARTS = (
    ("metric", 0.4, _has_metric),
    ("action verb", 0.3, _has_action_verb),
    ("XYZ connector", 0.3, _has_xyz_connector),
)


def bullet_quality(bullet_text: str) -> float:
    if not bullet_text.split():
        return 0.0
    return sum(weight for _, weight, ok in QUALITY_PARTS if ok(bullet_text))


def quality_gaps(bullet_text: str) -> list[str]:
    """Which quality components a bullet lacks, so tailoring can target them."""
    if not bullet_text.split():
        return [name for name, _, _ in QUALITY_PARTS]
    return [name for name, _, ok in QUALITY_PARTS if not ok(bullet_text)]


def score_resume(text: str, bullets: list[str], keywords: list[dict]) -> dict:
    text_norm = _norm(text)
    matched, missing = [], []
    total_w = found_w = 0.0
    hard_total = hard_found = 0.0
    for kw in keywords:
        w = float(max(1, min(3, int(kw.get("importance", 1)))))
        total_w += w
        hit = keyword_found(text_norm, kw)
        (matched if hit else missing).append(kw["term"])
        if hit:
            found_w += w
        if kw.get("category") in HARD_CATEGORIES and int(kw.get("importance", 1)) >= 3:
            hard_total += 1
            hard_found += 1 if hit else 0

    if hard_total == 0:  # no must-haves flagged: use all hard skills
        hard = [k for k in keywords if k.get("category") in HARD_CATEGORIES]
        hard_total = len(hard)
        hard_found = sum(1 for k in hard if k["term"] in matched)

    kw_cov = 100 * found_w / total_w if total_w else 0.0
    hard_match = 100 * hard_found / hard_total if hard_total else kw_cov
    quality = 100 * sum(bullet_quality(b) for b in bullets) / len(bullets) if bullets else 0.0

    breakdown = {
        "keyword_coverage": round(kw_cov, 1),
        "hard_skill_match": round(hard_match, 1),
        "bullet_quality": round(quality, 1),
    }
    overall = sum(breakdown[k] * w for k, w in WEIGHTS.items())
    return {
        "overall": round(overall, 1),
        "breakdown": breakdown,
        "matched": matched,
        "missing": missing,
    }


LABELS = {
    "keyword_coverage": "Keyword coverage",
    "hard_skill_match": "Must-have hard skills",
    "bullet_quality": "Bullet quality (metrics, action verbs, XYZ)",
}


def compare(master: dict, tailored: dict, keywords: list[dict]) -> dict:
    """Deterministic reasons for the score gap."""
    importance = {k["term"]: int(k.get("importance", 1)) for k in keywords}
    gained = [t for t in tailored["matched"] if t not in master["matched"]]
    lost = [t for t in master["matched"] if t not in tailored["matched"]]
    still_missing = sorted(tailored["missing"], key=lambda t: -importance.get(t, 1))

    reasons = []
    if gained:
        reasons.append(f"The tailored version now includes {len(gained)} JD keyword(s) the master lacks: "
                       + ", ".join(gained) + ".")
    if lost:
        reasons.append("Keywords present in the master but dropped in the tailored version: "
                       + ", ".join(lost) + ".")
    for key, label in LABELS.items():
        d = round(tailored["breakdown"][key] - master["breakdown"][key], 1)
        if abs(d) >= 0.5:
            reasons.append(f"{label}: {master['breakdown'][key]} → {tailored['breakdown'][key]} "
                           f"({'+' if d > 0 else ''}{d}).")
    must_missing = [t for t in still_missing if importance.get(t, 1) >= 3]
    if must_missing:
        reasons.append("High-importance keywords still missing from both: " + ", ".join(must_missing) + ".")
    if not reasons:
        reasons.append("Both versions match the job description about equally.")

    delta = round(tailored["overall"] - master["overall"], 1)
    if delta > 0.5:
        winner = "tailored"
    elif delta < -0.5:
        winner = "master"
    else:
        winner = "tie"
    return {
        "delta": delta,
        "winner": winner,
        "gained": gained,
        "lost": lost,
        "still_missing": still_missing,
        "reasons": reasons,
    }
