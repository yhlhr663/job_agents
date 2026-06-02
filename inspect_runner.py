"""Standalone headless Playwright subprocess that READS an application form and
reports what it asks for — no filling, no submitting.

Run by src/inspector.py via:  python inspect_runner.py <payload.json>

Payload: {"job": {...Job fields...}, "out": "/path/to/result.json"}

Output JSON:
{
  "ok": bool,
  "requires_login": bool,
  "documents": [labels of file uploads],
  "profile_fields": [{label, kind, required, category}],   # we can auto-fill
  "custom_questions": [{label, kind, required}],            # need the user's input
  "message": str
}
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.applicator import apply_url, assess_doability, categorize, policy_blocked
from src.config import do_not_automate
from src.models import Job
from src.pw_forms import (detect_login_wall, detect_multistep, enumerate_fields,
                          enumerate_radio_groups, has_captcha, list_file_inputs)


def job_from_dict(d: dict) -> Job:
    j = Job(url=d.get("url", ""))
    for k, v in d.items():
        if hasattr(j, k) and k != "status":
            setattr(j, k, v)
    return j


def main():
    payload = json.loads(Path(sys.argv[1]).read_text())
    job = job_from_dict(payload["job"])
    out = payload.get("out")
    result = {"ok": False, "verdict": "unknown", "doable_reason": "",
              "requires_login": False, "has_captcha": False, "multistep": False,
              "documents": [], "profile_fields": [], "custom_questions": [],
              "message": ""}

    # The only non-live gate: an optional user-configured do-not-automate list.
    blocked, reason = policy_blocked(job.ats, do_not_automate())
    if blocked:
        result.update(verdict="manual", doable_reason=reason, message=reason)
        _write(out, result)
        return

    target = apply_url(job.url, job.ats)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_context().new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            try:  # React/SPA forms (e.g. Ashby) need time to render fields
                page.wait_for_selector("input, textarea, select", timeout=15000)
            except Exception:
                pass
            time.sleep(1.5)

            requires_login = detect_login_wall(page)
            result["requires_login"] = requires_login
            result["has_captcha"] = has_captcha(page)
            result["multistep"] = detect_multistep(page)
            result["documents"] = list_file_inputs(page)
            for f in enumerate_fields(page):
                category, covered = categorize(f["label"])
                if category and covered:
                    result["profile_fields"].append({**f, "category": category})
                elif category:           # known but not in profile (e.g. cover letter)
                    result["custom_questions"].append({**f, "category": category})
                else:                    # truly custom question
                    result["custom_questions"].append(f)
            # Single-choice (radio) questions, grouped so Yes/No shows once.
            for g in enumerate_radio_groups(page):
                label = g["question"] or g["name"]
                entry = {"label": label, "kind": "radio", "required": g["required"],
                         "options": [o["label"] for o in g["options"] if o["label"]]}
                category, covered = categorize(label)
                if category and covered:
                    result["profile_fields"].append({**entry, "category": category})
                else:
                    result["custom_questions"].append({**entry, "category": category})
            browser.close()

        num_fields = len(result["profile_fields"]) + len(result["custom_questions"])
        verdict, why = assess_doability(requires_login, num_fields,
                                        result["has_captcha"], result["multistep"])
        result.update(ok=True, verdict=verdict, doable_reason=why,
                      message="Read the application form.")
    except Exception as e:  # noqa: BLE001
        result.update(verdict="unknown",
                      doable_reason="Could not load/read the form.",
                      message=f"Could not read the form automatically: {e}")

    _write(out, result)


def _write(path, obj):
    if path:
        Path(path).write_text(json.dumps(obj, indent=2))
    print(json.dumps(obj))


if __name__ == "__main__":
    main()
