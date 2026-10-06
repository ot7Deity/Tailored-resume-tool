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
# Write the AI score explanation during tailoring (0 = on demand, saves one Claude call per run)
AUTO_EXPLAIN = os.getenv("AUTO_EXPLAIN", "0") == "1"
TECTONIC_PATH = os.getenv("TECTONIC_PATH", "").strip()
TERM_LABEL = os.getenv("TERM_LABEL", "Spring2028")
# Graduation dates offered on the tailor page; the choice sets the Education end date and the filename term
GRAD_DATES = [d.strip() for d in os.getenv("GRAD_DATES", "May 2028,May 2029").split(",") if d.strip()]
MAX_CHANGE_RATIO = _float("MAX_CHANGE_RATIO", 0.20)
MAX_BULLET_RATIO = _float("MAX_BULLET_RATIO", 0.50)
TARGET_SCORE = _float("TARGET_SCORE", 85)
TAILOR_ROUNDS = max(1, int(_float("TAILOR_ROUNDS", 3)))
MAX_SKILL_ADDS = max(0, int(_float("MAX_SKILL_ADDS", 15)))
