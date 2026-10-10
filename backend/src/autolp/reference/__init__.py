"""Curated reference data shipped with the code (versioned in git)."""

import json
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache
def infrastructure_wallets() -> dict[str, str]:
    """address -> label for curated infrastructure wallets."""
    data = json.loads((_DIR / "infrastructure_wallets.json").read_text())
    out: dict[str, str] = {}
    for w in data["wallets"]:
        if not (w.get("address") and w.get("label") and w.get("source")):
            raise ValueError(f"infrastructure wallet entry needs address, label, source: {w}")
        out[w["address"]] = w["label"]
    return out
