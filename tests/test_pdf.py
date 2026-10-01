"""PDF inputs: resume PDF -> LaTeX master (Claude mocked) and JD text extraction."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, llm, pdf_import

FIX = Path(__file__).parent / "fixtures"
PDF = (FIX / "sample_resume.pdf").read_bytes()
SAMPLE = (FIX / "sample_resume.tex").read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    from app.main import app
    return TestClient(app)


def test_extract_text_from_pdf():
    text = pdf_import.extract_text(PDF)
    assert "Jake Ryan" in text and "Technical Skills" in text


def test_rejects_non_pdf():
    with pytest.raises(pdf_import.PDFError):
        pdf_import.extract_text(b"not a pdf")


def test_jd_extract_endpoint(client):
    r = client.post("/api/jd/extract", files={"file": ("jd.pdf", PDF, "application/pdf")})
    assert r.status_code == 200 and "Southwestern University" in r.json()["text"]
    r = client.post("/api/jd/extract", files={"file": ("jd.txt", b"We are hiring a Python intern", "text/plain")})
    assert r.json()["text"] == "We are hiring a Python intern"


def test_master_pdf_upload_converts_without_saving(client, tmp_path, monkeypatch):
    calls = []

    def fake_text(system, content, effort="medium", max_tokens=64000):
        calls.append(content)
        return "```latex\n" + SAMPLE + "\n```"

    monkeypatch.setattr(llm, "call_text", fake_text)
    r = client.post("/api/master/upload", files={"file": ("resume.pdf", PDF, "application/pdf")})
    body = r.json()
    assert r.status_code == 200 and body["unsaved"] and body["converted_from_pdf"]
    assert body["tex"].startswith("%---") and "```" not in body["tex"]
    assert len(body["bullets"]) == 20
    assert calls[0][0]["type"] == "document"  # the PDF is sent to Claude as a document block
    assert not (tmp_path / "master.tex").exists()  # user must review + save first
