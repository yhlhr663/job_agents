"""Loads settings.yaml + profile.yaml + .env and exposes them."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

load_dotenv(ROOT / ".env")


@lru_cache(maxsize=1)
def settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml") as f:
        return yaml.safe_load(f)


def load_profile() -> dict:
    """Re-read each call so edits to profile.yaml take effect without restart.
    On a fresh clone (profile.yaml gitignored), seed it from the example."""
    profile = CONFIG_DIR / "profile.yaml"
    if not profile.exists():
        import shutil
        shutil.copy2(CONFIG_DIR / "profile.example.yaml", profile)
    with open(profile) as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, updates: dict) -> dict:
    """Recursively merge `updates` into `base` (updates win). Returns base."""
    for k, v in (updates or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def update_profile(updates: dict) -> dict:
    """Deep-merge `updates` into profile.yaml and write it back. Returns merged dict."""
    merged = _deep_merge(load_profile(), updates)
    with open(CONFIG_DIR / "profile.yaml", "w") as f:
        yaml.safe_dump(merged, f, sort_keys=False, default_flow_style=False,
                       allow_unicode=True)
    return merged


def anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def do_not_automate() -> list:
    """Optional user list of ATS keys to force-skip (settings.yaml). Empty by default."""
    return settings().get("do_not_automate") or []


def base_resumes_dir() -> Path:
    return ROOT / settings().get("base_resumes_dir", "base_resumes")


def results_dir() -> Path:
    d = ROOT / settings().get("results_dir", "results")
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_base_resumes() -> list[str]:
    """Names (without path) of every .tex file the user dropped in base_resumes/."""
    d = base_resumes_dir()
    if not d.exists():
        return []
    return sorted(p.name for p in d.glob("*.tex"))


def read_base_resume(name: str) -> str:
    return (base_resumes_dir() / name).read_text(encoding="utf-8")
