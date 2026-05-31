"""Launch the headless inspect_runner subprocess and return its parsed result.

Kept separate from agent.py so the Playwright work runs in its own process
(Playwright's sync API doesn't mix with Streamlit's event loop).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def inspect_job(job) -> dict:
    """Return what the application form requires (documents, fields, login)."""
    tmp = Path(tempfile.mkdtemp(prefix="inspect_"))
    out = tmp / "result.json"
    payload = tmp / "payload.json"
    payload.write_text(json.dumps({"job": job.to_dict(), "out": str(out)}))

    proc = subprocess.run(
        [sys.executable, str(ROOT / "inspect_runner.py"), str(payload)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=90,
    )
    if out.exists():
        return json.loads(out.read_text())
    return {"ok": False, "requires_login": False, "documents": [],
            "profile_fields": [], "custom_questions": [],
            "message": f"Inspector failed to run. {proc.stderr[-300:]}"}
