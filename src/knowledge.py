"""The agent's growing memory of how different ATS platforms behave.

Each live inspection can teach the agent something ("ashby = auto-fillable",
"workday = needs an account"). We persist that to data/learned_ats.yaml and only
write when it's genuinely NEW or CHANGED — so the file stays signal, not noise.

This is a knowledge/memory store, not a skill: the agent fills it in itself from
real observations and consults it to answer faster next time.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .config import ROOT

KNOWLEDGE_PATH = ROOT / "data" / "learned_ats.yaml"


def _load() -> dict:
    if KNOWLEDGE_PATH.exists():
        return yaml.safe_load(KNOWLEDGE_PATH.read_text()) or {}
    return {}


def _save(store: dict):
    KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_PATH.write_text(
        yaml.safe_dump(store, sort_keys=True, default_flow_style=False, allow_unicode=True)
    )


def recall(ats: str) -> dict | None:
    """What we already know about this ATS, or None."""
    return _load().get(ats)


def record(ats: str, verdict: str, reason: str, url: str = "", notes: str = "") -> bool:
    """Record a finding — but ONLY if it's new or the verdict changed.

    Returns True if the knowledge file was actually updated, else False.
    """
    if not ats or ats == "unknown":
        return False
    store = _load()
    existing = store.get(ats)

    # Only worth recording when it's genuinely NEW: a never-seen ATS, or a verdict
    # that changed from what we already knew. Repeat sightings are noise — skip.
    if existing and existing.get("verdict") == verdict:
        return False

    entry = existing or {"examples": []}
    entry.update(verdict=verdict, reason=reason, last_seen=str(date.today()))
    if notes:
        entry["notes"] = notes
    host = urlparse(url).hostname or ""
    if host and host not in entry["examples"] and len(entry["examples"]) < 8:
        entry["examples"].append(host)
    store[ats] = entry
    _save(store)
    return True


def summary() -> dict:
    """Everything the agent has learned so far (for 'what do you know?' queries)."""
    return _load()
