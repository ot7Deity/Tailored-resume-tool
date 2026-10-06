"""End-to-end API flow with Claude and Tectonic mocked out."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, llm, one_page, tailor

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_resume.tex").read_text(encoding="utf-8")
JD = "Acme Robotics is hiring a Software Engineering Intern. Required: Python, Kubernetes, REST APIs. " * 3


def fake_llm(system, user, output, effort="high"):
    if output is tailor.JDAnalysis:
        return tailor.JDAnalysis(company="Acme Robotics", role="Software Engineering Intern", summary="Build robots.",
                                 keywords=[tailor.Keyword(term="Python", category="hard_skill", importance=3, variants=[]),
                                           tailor.Keyword(term="Kubernetes", category="tool", importance=3, variants=[])])
    if output is tailor.TailorPlan:
        assert "Kubernetes" in user  # missing keywords are passed to the tailoring step
        return tailor.TailorPlan(edits=[tailor.BulletEdit(
            bullet_id="b1", new_text="Built a FastAPI REST API on Kubernetes serving 5K requests/day by containerizing services",
            xyz=tailor.XYZ(x_accomplished="Built API", y_measured_by="5K req/day", z_by_doing="containerizing"),
            keywords_added=["Kubernetes"], metric_estimated=True, estimated_metrics=["5K requests/day"], reason="Adds k8s")],
            skills_edits=[])
    if output is tailor.Explanation:
        return tailor.Explanation(verdict="tailored", headline="Tailored wins", reasons=["Adds Kubernetes"], advice=[])
    raise AssertionError(output)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(llm, "call_structured", fake_llm)
    monkeypatch.setattr(one_page, "fit_to_one_page",
                        lambda tex, assets=None: one_page.OnePageResult(True, b"%PDF-fake", "ok", tex, 1))
    from app.main import app
    return TestClient(app)


def test_full_flow(client, tmp_path):
    r = client.put("/api/master", json={"tex": SAMPLE})
    assert r.status_code == 200 and len(r.json()["bullets"]) == 20
    assert client.get("/api/master").json()["saved_at"]  # shown on the tailor page

    run = client.post("/api/tailor", json={"jd": JD}).json()
    assert run["filename"] == "Jake_Ryan_AcmeRobotics_Spring2028.pdf"
    assert run["scores"]["tailored"]["overall"] > run["scores"]["master"]["overall"]
    assert run["comparison"]["winner"] == "tailored"
    assert run["edits"][0]["metric_estimated"] is True
    assert 0 < run["change_ratio"] <= config.MAX_CHANGE_RATIO
    assert run["target_score"] == config.TARGET_SCORE
    assert run["rounds_used"] == 1  # both keywords covered on the first pass -> target met

    rid = run["run_id"]
    dl = client.get(f"/api/runs/{rid}/download")
    assert dl.status_code == 200 and "Jake_Ryan_AcmeRobotics_Spring2028.pdf" in dl.headers["content-disposition"]

    # reject the change -> tailored tex equals master again, scores equal
    run = client.post(f"/api/runs/{rid}/apply", json={"edits": [{"bullet_id": "b1", "accepted": False}],
                                                       "company": "Acme"}).json()
    assert run["scores"]["tailored"] == run["scores"]["master"]
    assert run["filename"] == "Jake_Ryan_Acme_Spring2028.pdf"
    assert (tmp_path / "runs" / rid / "tailored.tex").read_text(encoding="utf-8") == SAMPLE

    # accept again with the user's own number
    run = client.post(f"/api/runs/{rid}/apply", json={"edits": [{"bullet_id": "b1", "accepted": True,
        "new_text": "Built a FastAPI REST API on Kubernetes serving 12K requests/day"}]}).json()
    assert run["edits"][0]["user_edited"] is True
    assert "12K" in (tmp_path / "runs" / rid / "tailored.tex").read_text(encoding="utf-8")
    assert run["explanation_stale"] is True


def test_grad_date_choice(client, tmp_path):
    client.put("/api/master", json={"tex": SAMPLE})
    m = client.get("/api/master").json()
    assert m["grad_dates"] == ["May 2028", "May 2029"] and m["detected_grad_date"] == "May 2028"

    run = client.post("/api/tailor", json={"jd": JD, "grad_date": "May 2029"}).json()
    rid = run["run_id"]
    assert run["grad_date"] == "May 2029" and run["filename"] == "Jake_Ryan_AcmeRobotics_Spring2029.pdf"
    assert "-- May 2029}" in (tmp_path / "runs" / rid / "tailored.tex").read_text(encoding="utf-8")

    run = client.post(f"/api/runs/{rid}/apply", json={"grad_date": "May 2028"}).json()
    assert run["filename"] == "Jake_Ryan_AcmeRobotics_Spring2028.pdf"
    assert "-- May 2028}" in (tmp_path / "runs" / rid / "tailored.tex").read_text(encoding="utf-8")

    assert client.post("/api/tailor", json={"jd": JD, "grad_date": "June 2031"}).status_code == 400


def test_jd_analysis_is_cached_across_runs(client, monkeypatch):
    """A second run on the same JD reuses the stored analysis instead of calling Claude again."""
    client.put("/api/master", json={"tex": SAMPLE})
    calls = []
    real = tailor.analyze_jd
    monkeypatch.setattr(tailor, "analyze_jd", lambda jd: (calls.append(jd), real(jd))[1])

    jd = "We need a Python engineer with Kubernetes experience. " * 5
    first = client.post("/api/tailor", json={"jd": jd}).json()
    second = client.post("/api/tailor", json={"jd": jd.replace(" ", "  ")}).json()  # whitespace differs

    assert len(calls) == 1, "analyze_jd should only run for the first request"
    assert first["jd_cached"] is False and second["jd_cached"] is True
    # identical keyword set -> master scores match exactly, so runs are comparable
    assert first["scores"]["master"] == second["scores"]["master"]


def test_explanation_is_deferred_by_default(client):
    client.put("/api/master", json={"tex": SAMPLE})
    run = client.post("/api/tailor", json={"jd": "Python and Kubernetes engineer wanted. " * 5}).json()
    assert run["explanation"] is None and run["explanation_error"] is None
    # the deterministic half is still there
    assert run["scores"]["tailored"]["overall"] > 0 and run["comparison"]["reasons"]


def test_retries_until_target(client, monkeypatch):
    monkeypatch.setattr(config, "TARGET_SCORE", 101)  # unreachable -> every round runs
    prompts = []
    real = llm.call_structured
    monkeypatch.setattr(llm, "call_structured",
                        lambda system, user, output, effort="high": (prompts.append(user), real(system, user, output, effort))[1])
    client.put("/api/master", json={"tex": SAMPLE})
    run = client.post("/api/tailor", json={"jd": JD}).json()
    assert run["rounds_used"] == config.TAILOR_ROUNDS
    tailor_prompts = [p for p in prompts if "<resume_bullets>" in p]
    assert len(tailor_prompts) == config.TAILOR_ROUNDS
    assert "<previous_attempt>" not in tailor_prompts[0] and "<previous_attempt>" in tailor_prompts[1]


def test_master_backup_and_profile(client, tmp_path):
    client.put("/api/master", json={"tex": SAMPLE})
    client.put("/api/master", json={"tex": SAMPLE.replace("Jake Ryan", "Jake Q Ryan")})
    assert len(list((tmp_path / "backups").glob("*.tex"))) == 1
    m = client.put("/api/profile", json={"first_name": "Jacob", "last_name": ""}).json()
    assert m["name"] == {"first": "Jacob", "last": "Ryan"}


def test_rejects_non_latex(client):
    assert client.put("/api/master", json={"tex": "hello"}).status_code == 400
