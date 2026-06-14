import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_sources(ticker: str) -> dict:
    path = REPO_ROOT / ticker / "sources.json"
    if path.exists():
        with path.open() as f:
            return json.load(f)
    return {}
