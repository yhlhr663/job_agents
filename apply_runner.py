"""Standalone Playwright subprocess that opens an application page, pre-fills it
from your profile, attaches the tailored resume, then hands control to you to
REVIEW and SUBMIT in the visible browser. When you submit (or close the window),
it saves the result report.

Run by app.py via:  python apply_runner.py <payload.json>

Payload JSON:
{
  "job":     {...Job fields...},
  "profile": {...profile.yaml...},
  "status_out": "/path/to/status.json"
}

It is a separate process on purpose: Playwright's sync API doesn't play well
inside Streamlit's event loop, and a subprocess keeps the browser independent.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.applicator import apply_url, build_field_rules, match_rule, policy_blocked
from src.config import do_not_automate
from src.models import Job, JobStatus
from src.pw_forms import detect_login_wall, field_key, fill_select, fill_text
from src.reporter import save_result

CONFIRM_SIGNALS = ["thank you for applying", "application submitted", "thanks for applying",
                   "we received your application", "successfully submitted"]
CONFIRM_URL_BITS = ["thanks", "thank-you", "confirmation", "submitted"]
MAX_WAIT_SECONDS = 600   # generous: you may take a while to review/submit


def log(msg: str):
    print(f"[apply_runner] {msg}", flush=True)


def fill_form(page, rules, resume_pdf: str) -> dict:
    filled, attached = [], False
    elements = page.query_selector_all("input, textarea, select")
    log(f"found {len(elements)} form fields")

    # 1) Resume upload — first file input gets the tailored PDF.
    for el in elements:
        try:
            if el.get_attribute("type") == "file" and not attached:
                el.set_input_files(resume_pdf)
                attached = True
                log("attached tailored resume")
                break
        except Exception:
            continue

    # 2) Text / select / yes-no fields via label matching.
    for el in elements:
        try:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            etype = (el.get_attribute("type") or "").lower()
            if etype in ("file", "hidden", "submit", "button", "checkbox", "radio"):
                continue
            key = field_key(el)
            rule = match_rule(key, rules)
            if not rule:
                continue
            ok = fill_select(el, rule["value"]) if tag == "select" else fill_text(el, rule["value"])
            if ok:
                filled.append(rule["patterns"][0])
        except Exception:
            continue

    return {"filled": filled, "resume_attached": attached}


def wait_for_submit(page, context) -> bool:
    """Block until a confirmation page appears or the user closes the browser."""
    start = time.time()
    while time.time() - start < MAX_WAIT_SECONDS:
        if not context.pages:           # user closed the window
            return False
        pg = context.pages[-1]
        try:
            url = (pg.url or "").lower()
            body = pg.inner_text("body").lower()[:6000]
        except Exception:
            time.sleep(1.0)
            continue
        if any(s in body for s in CONFIRM_SIGNALS) or any(b in url for b in CONFIRM_URL_BITS):
            time.sleep(2)
            return True
        time.sleep(1.5)
    return False


def job_from_dict(d: dict) -> Job:
    j = Job(url=d.get("url", ""))
    for k, v in d.items():
        if hasattr(j, k) and k != "status":
            setattr(j, k, v)
    return j


def main():
    payload = json.loads(Path(sys.argv[1]).read_text())
    job = job_from_dict(payload["job"])
    profile = payload.get("profile") or {}
    status_out = payload.get("status_out")

    result = {"submitted": False, "saved_path": "", "message": "", "fill": {}}

    blocked, reason = policy_blocked(job.ats, do_not_automate())
    if blocked:
        job.status = JobStatus.SKIPPED
        job.status_reason = reason
        result["saved_path"] = save_result(job, applied=False, note="Auto-apply skipped (policy).")
        result["message"] = reason
        _write(status_out, result)
        log(reason)
        return

    target = apply_url(job.url, job.ats)
    rules = build_field_rules(profile)
    log(f"opening {target}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            try:  # React/SPA forms (e.g. Ashby) need time to render fields
                page.wait_for_selector("input, textarea, select", timeout=15000)
            except Exception:
                pass
            time.sleep(1.5)
            # Live check: if the form is behind a login wall, don't waste your time.
            if detect_login_wall(page):
                result["message"] = ("This application requires login/account — "
                                     "can't auto-fill. Apply manually with the tailored resume.")
                log(result["message"])
            else:
                result["fill"] = fill_form(page, rules, job.tailored_pdf_path)
                log("PRE-FILLED. Review the form in the browser, then click Submit. "
                    "(Close the window when done.)")
                result["submitted"] = wait_for_submit(page, context)
        except Exception as e:  # noqa: BLE001
            result["message"] = f"automation error: {e}"
            log(result["message"])
        finally:
            try:
                browser.close()
            except Exception:
                pass

    if result["submitted"]:
        job.status = JobStatus.APPLIED
        note = "Auto-filled in browser; submission confirmed."
    else:
        job.status = JobStatus.TAILORED
        note = ("Auto-filled in browser; submission NOT confirmed "
                "(you may have closed before submitting). Tailored resume saved.")
    if result["fill"]:
        note += f" Filled: {', '.join(result['fill'].get('filled', [])) or 'none'}."
    result["saved_path"] = save_result(job, applied=result["submitted"], note=note)
    result["message"] = note
    _write(status_out, result)
    log(f"done -> {result['saved_path']}")


def _write(path, obj):
    if path:
        Path(path).write_text(json.dumps(obj, indent=2))


if __name__ == "__main__":
    main()
