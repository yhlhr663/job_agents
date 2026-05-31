"""Load prompt templates from the prompts/ directory.

Prompts live in plain .md files (not in code) so they're easy to read and edit —
and edits take effect on the next turn without a restart (no caching).
"""
from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load(name: str) -> str:
    """Return the text of prompts/<name>.md."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
