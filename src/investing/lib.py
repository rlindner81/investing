import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_sources(ticker: str) -> dict:
    path = REPO_ROOT / ticker / "SOURCES.yml"
    if not path.exists():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    # CIK may be loaded as an integer if unquoted; zero-pad to 10 digits.
    meta = data.get("_meta", {})
    if "cik" in meta:
        meta["cik"] = str(meta["cik"]).zfill(10)
    return data
