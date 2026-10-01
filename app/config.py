import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
STATIC_DIR = ROOT / "static"
TOOLS_DIR = ROOT / "tools"

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")
CLAUDE_FALLBACKS = os.getenv("CLAUDE_FALLBACKS", "1") == "1"
TECTONIC_PATH = os.getenv("TECTONIC_PATH", "").strip()
TERM_LABEL = os.getenv("TERM_LABEL", "Spring2028")
MAX_CHANGE_RATIO = _float("MAX_CHANGE_RATIO", 0.10)
MAX_BULLET_RATIO = _float("MAX_BULLET_RATIO", 0.10)
