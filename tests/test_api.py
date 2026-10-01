"""End-to-end API flow with Claude and Tectonic mocked out."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import compiler, config, llm, tailor
from app.compiler import CompileResult

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
    monkeypatch.setattr(compiler, "compile_tex", lambda tex, assets=None: CompileResult(True, b"%PDF-fake", "ok"))
    from app.main import app
    return TestClient(app)


def test_full_flow(client, tmp_path):
    r = client.put("/api/master", json={"tex": SAMPLE})
    assert r.status_code == 200 and len(r.json()["bullets"]) == 20

    run = client.post("/api/tailor", json={"jd": JD}).json()
    assert run["filename"] == "Ryan_Jake_AcmeRobotics_Spring2028.pdf"
    assert run["scores"]["tailored"]["overall"] > run["scores"]["master"]["overall"]
    assert run["comparison"]["winner"] == "tailored"
    assert run["edits"][0]["metric_estimated"] is True
    assert 0 < run["change_ratio"] <= config.MAX_CHANGE_RATIO

    rid = run["run_id"]
    dl = client.get(f"/api/runs/{rid}/download")
    assert dl.status_code == 200 and "Ryan_Jake_AcmeRobotics_Spring2028.pdf" in dl.headers["content-disposition"]

    # reject the change -> tailored tex equals master again, scores equal
    run = client.post(f"/api/runs/{rid}/apply", json={"edits": [{"bullet_id": "b1", "accepted": False}],
                                                       "company": "Acme"}).json()
    assert run["scores"]["tailored"] == run["scores"]["master"]
    assert run["filename"] == "Ryan_Jake_Acme_Spring2028.pdf"
    assert (tmp_path / "runs" / rid / "tailored.tex").read_text(encoding="utf-8") == SAMPLE

    # accept again with the user's own number
    run = client.post(f"/api/runs/{rid}/apply", json={"edits": [{"bullet_id": "b1", "accepted": True,
        "new_text": "Built a FastAPI REST API on Kubernetes serving 12K requests/day"}]}).json()
    assert run["edits"][0]["user_edited"] is True
    assert "12K" in (tmp_path / "runs" / rid / "tailored.tex").read_text(encoding="utf-8")
    assert run["explanation_stale"] is True


def test_master_backup_and_profile(client, tmp_path):
    client.put("/api/master", json={"tex": SAMPLE})
    client.put("/api/master", json={"tex": SAMPLE.replace("Jake Ryan", "Jake Q Ryan")})
    assert len(list((tmp_path / "backups").glob("*.tex"))) == 1
    m = client.put("/api/profile", json={"first_name": "Jacob", "last_name": ""}).json()
    assert m["name"] == {"first": "Jacob", "last": "Ryan"}


def test_rejects_non_latex(client):
    assert client.put("/api/master", json={"tex": "hello"}).status_code == 400
