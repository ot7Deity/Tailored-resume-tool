"""Local file storage: master resume (+ backups), name override, and tailoring runs."""
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from . import config


def _data() -> Path:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR


def master_path() -> Path:
    return _data() / "master.tex"


def assets_dir() -> Path:
    """Extra files the master needs to compile (.cls, .sty, images). Copied next to the .tex."""
    d = _data() / "assets"
    d.mkdir(exist_ok=True)
    return d


def read_master() -> str | None:
    p = master_path()
    return p.read_text(encoding="utf-8") if p.exists() else None


def save_master(tex: str) -> None:
    p = master_path()
    if p.exists():
        backups = _data() / "backups"
        backups.mkdir(exist_ok=True)
        shutil.copy2(p, backups / f"master_{datetime.now():%Y%m%d_%H%M%S}.tex")
    p.write_text(tex, encoding="utf-8", newline="")


def list_backups() -> list[str]:
    d = _data() / "backups"
    return sorted((f.name for f in d.glob("master_*.tex")), reverse=True) if d.exists() else []


def read_backup(name: str) -> str | None:
    if not re.fullmatch(r"master_\d{8}_\d{6}\.tex", name):
        return None
    p = _data() / "backups" / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def read_profile() -> dict:
    p = _data() / "profile.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_profile(profile: dict) -> None:
    (_data() / "profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")


# ---------- runs ----------

def _runs() -> Path:
    d = _data() / "runs"
    d.mkdir(exist_ok=True)
    return d


def run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", run_id):
        raise FileNotFoundError(run_id)
    return _runs() / run_id


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def save_run(run: dict, master_tex: str | None = None, tailored_tex: str | None = None,
             pdf: bytes | None = None) -> None:
    d = run_dir(run["run_id"])
    d.mkdir(exist_ok=True)
    if master_tex is not None:
        (d / "master.tex").write_text(master_tex, encoding="utf-8", newline="")
    if tailored_tex is not None:
        (d / "tailored.tex").write_text(tailored_tex, encoding="utf-8", newline="")
    if pdf is not None:
        (d / "tailored.pdf").write_bytes(pdf)
    elif tailored_tex is not None and (d / "tailored.pdf").exists():
        (d / "tailored.pdf").unlink()  # stale
    (d / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")


def load_run(run_id: str) -> dict:
    p = run_dir(run_id) / "run.json"
    if not p.exists():
        raise FileNotFoundError(run_id)
    return json.loads(p.read_text(encoding="utf-8"))


def run_file(run_id: str, name: str) -> Path:
    return run_dir(run_id) / name


def list_runs(limit: int = 20) -> list[dict]:
    out = []
    for p in _runs().glob("*/run.json"):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({k: r.get(k) for k in ("run_id", "created", "company", "role", "filename")}
                   | {"score_master": r["scores"]["master"]["overall"],
                      "score_tailored": r["scores"]["tailored"]["overall"]})
    out.sort(key=lambda r: r["created"] or "", reverse=True)
    return out[:limit]
