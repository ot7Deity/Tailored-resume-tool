from pathlib import Path

import pytest

from app import config, tailor
from app.compiler import build_filename, term_for, xetex_compat
from app.latex_utils import escape_latex, latex_to_text
from app.scorer import bullet_quality, quality_gaps, score_resume
from app.tex_parser import parse_resume

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_resume.tex").read_text(encoding="utf-8")

KEYWORDS = [
    {"term": "Python", "category": "hard_skill", "importance": 3, "variants": []},
    {"term": "Kubernetes", "category": "tool", "importance": 3, "variants": ["k8s"]},
    {"term": "REST API", "category": "hard_skill", "importance": 2, "variants": ["RESTful"]},
    {"term": "CI/CD", "category": "tool", "importance": 2, "variants": ["continuous delivery"]},
    {"term": "Java", "category": "hard_skill", "importance": 1, "variants": []},
]


@pytest.fixture
def parsed():
    return parse_resume(SAMPLE)


def make_plan(n_edits: int) -> tailor.TailorPlan:
    edits = [
        tailor.BulletEdit(
            bullet_id=f"b{i + 1}",
            new_text=f"Deployed service {i} on Kubernetes, cutting latency 30% by caching hot paths",
            xyz=tailor.XYZ(x_accomplished="x", y_measured_by="y", z_by_doing="z"),
            keywords_added=["Kubernetes"], metric_estimated=True, estimated_metrics=["30%"], reason="r",
        )
        for i in range(n_edits)
    ]
    return tailor.TailorPlan(edits=edits, skills_edits=[
        tailor.SkillsEdit(line_id="s3", add_keywords=["Kubernetes", "Git"], reason="r")])


def test_parser_finds_name_bullets_skills(parsed):
    assert (parsed.first_name, parsed.last_name) == ("Jake", "Ryan")
    assert len(parsed.bullets) == 20
    assert [s.label for s in parsed.skills] == ["Languages", "Frameworks", "Developer Tools", "Libraries"]
    # nested braces (\emph{...}) inside a bullet
    zelda = next(b for b in parsed.bullets if "Zelda" in b.text)
    assert zelda.text.endswith("The Legend of Zelda")
    assert SAMPLE[zelda.start:zelda.end].count("{") == SAMPLE[zelda.start:zelda.end].count("}")
    # preamble \newcommand definitions are not bullets
    assert all(b.start > parsed.body_start for b in parsed.bullets)


def test_splice_only_touches_edited_spans(parsed):
    assert tailor.build_tex(parsed, [], []) == SAMPLE
    b = parsed.bullets[2]
    edits = [{"bullet_id": b.id, "new_text": "Built 50% faster R&D pipeline in C#", "accepted": True}]
    out = tailor.build_tex(parsed, edits, [])
    new = r"Built 50\% faster R\&D pipeline in C\#"
    assert out == SAMPLE[:b.start] + new + SAMPLE[b.end:]
    assert parse_resume(out).bullets[2].text == "Built 50% faster R&D pipeline in C#"


def test_skills_addition_appends(parsed):
    skills = [{"line_id": "s3", "add_keywords": ["Kubernetes"], "accepted": True}]
    out = tailor.build_tex(parsed, [], skills)
    assert "Eclipse, Kubernetes}" in out
    assert len(out) == len(SAMPLE) + len(", Kubernetes")


def test_escape_latex():
    assert escape_latex("C# & 50% of $5_000 {x}") == r"C\# \& 50\% of \$5\_000 \{x\}"
    assert escape_latex("Cut cost **40%** fast") == r"Cut cost \textbf{40\%} fast"
    assert latex_to_text(escape_latex("R&D ~ 100%")) == "R&D ~ 100%"


def test_edit_that_weakens_structure_is_rejected(parsed):
    """Tailoring may never lower a bullet's metric / action verb / XYZ score."""
    strong = next(b for b in parsed.bullets if len(quality_gaps(b.text)) < 3)
    plan = make_plan(1)
    plan.edits[0] = plan.edits[0].model_copy(update={"bullet_id": strong.id, "new_text": "Stuff about things"})
    edits, _ = tailor.enforce_budget(parsed, plan)
    assert edits[0]["accepted"] is False
    assert "drop the bullet's" in edits[0]["dropped_reason"]


def test_quality_gaps_matches_scoring():
    assert quality_gaps("Reduced latency 30% by caching") == []
    assert bullet_quality("Reduced latency 30% by caching") == pytest.approx(1.0)
    assert quality_gaps("Responsible for the website") == ["metric", "action verb", "XYZ connector"]
    assert bullet_quality("Responsible for the website") == 0.0
    assert quality_gaps("Built a parser using recursion") == ["metric"]


def test_budget_caps_edits(parsed):
    edits, skills = tailor.enforce_budget(parsed, make_plan(20))
    accepted = [e for e in edits if e["accepted"]]
    assert 0 < len(accepted) <= tailor.max_edits_for(parsed) < 20
    assert any(not e["accepted"] for e in edits)
    assert all(e["dropped_reason"] for e in edits if not e["accepted"])
    # "Git" already listed on the line -> not re-added
    assert skills[0]["add_keywords"] == ["Kubernetes"]
    ratio = tailor.change_ratio(parsed, {e["bullet_id"]: e["new_text"] for e in accepted})
    assert ratio <= config.MAX_CHANGE_RATIO


def test_skill_adds_dont_use_word_budget(parsed, monkeypatch):
    monkeypatch.setattr(config, "MAX_CHANGE_RATIO", 0.0)  # no word budget at all
    _, skills = tailor.enforce_budget(parsed, make_plan(0))
    assert skills[0]["add_keywords"] == ["Kubernetes"]
    assert tailor.change_ratio(parsed, {}) == 0


def test_skill_adds_capped(parsed, monkeypatch):
    monkeypatch.setattr(config, "MAX_SKILL_ADDS", 2)
    plan = tailor.TailorPlan(edits=[], skills_edits=[
        tailor.SkillsEdit(line_id="s3", add_keywords=["Kubernetes", "Docker Swarm", "Terraform"], reason="r")])
    _, skills = tailor.enforce_budget(parsed, plan)
    assert skills[0]["add_keywords"] == ["Kubernetes", "Docker Swarm"]


def test_budget_ignores_unknown_and_duplicate_ids(parsed):
    plan = make_plan(1)
    plan.edits.append(plan.edits[0])
    plan.edits.append(plan.edits[0].model_copy(update={"bullet_id": "b999"}))
    edits, _ = tailor.enforce_budget(parsed, plan)
    assert [e["bullet_id"] for e in edits] == ["b1"]


def test_filename():
    assert build_filename("Jake", "Ryan", "Goldman Sachs") == "Jake_Ryan_GoldmanSachs_Spring2028.pdf"
    assert build_filename("Mary-Ann", "O'Neil", "") == "MaryAnn_ONeil_Company_Spring2028.pdf"
    assert build_filename("Christopher", "Omubo", "apple") == "Christopher_Omubo_Apple_Spring2028.pdf"
    assert build_filename("Jake", "Ryan", "eBay") == "Jake_Ryan_eBay_Spring2028.pdf"


def test_scorer_deterministic_and_tailored_improves(parsed):
    master = score_resume(parsed.plain_text(), [b.text for b in parsed.bullets], KEYWORDS)
    assert master == score_resume(parsed.plain_text(), [b.text for b in parsed.bullets], KEYWORDS)
    assert "Kubernetes" in master["missing"] and "Python" in master["matched"]
    assert "CI/CD" in master["matched"]  # via the "continuous delivery" variant

    edits, skills = tailor.enforce_budget(parsed, make_plan(2))
    tex = tailor.build_tex(parsed, edits, skills)
    tailored = score_resume(latex_to_text(tex[parsed.body_start:]), tailor.tailored_bullets(parsed, edits), KEYWORDS)
    assert tailored["overall"] > master["overall"]
    assert "Kubernetes" in tailored["matched"]


def test_java_does_not_match_javascript():
    s = score_resume("Skilled in JavaScript", [], [{"term": "Java", "category": "hard_skill", "importance": 3, "variants": []}])
    assert s["missing"] == ["Java"]


def test_xetex_compat_guards_pdftex_primitives():
    out = xetex_compat(SAMPLE)
    assert r"\ifdefined\pdfglyphtounicode\input{glyphtounicode}\fi" in out
    assert r"\ifdefined\pdfgentounicode\pdfgentounicode=1\fi" in out


def test_grad_date_swaps_only_education_end_date():
    assert tailor.detect_grad_date(SAMPLE) == "May 2028"
    out = tailor.set_grad_date(SAMPLE, "May 2029")
    assert out == SAMPLE.replace("Aug. 2024 -- May 2028", "Aug. 2024 -- May 2029", 1)
    assert tailor.detect_grad_date(out) == "May 2029"
    assert tailor.set_grad_date(SAMPLE, "") == SAMPLE


def test_term_for():
    assert term_for("May 2029") == "Spring2029"
    assert term_for("Dec. 2028") == "Fall2028"
    assert term_for("") == config.TERM_LABEL
