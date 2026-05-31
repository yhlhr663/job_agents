"""Shared Playwright form helpers used by the runner subprocesses
(apply_runner.py = fill + submit, inspect_runner.py = read-only enumerate).

Imports Playwright, so only the subprocess runners import this — the pure logic
in applicator.py stays Playwright-free and unit-testable.
"""
from __future__ import annotations

import re

SKIP_INPUT_TYPES = {"file", "hidden", "submit", "button", "image", "reset"}
# Labels that aren't real questions (bot checks, honeypots).
NOISE_LABELS = ("recaptcha", "captcha", "hcaptcha", "honeypot")


def field_key(el) -> str:
    """Normalized label string for a form element (name/id/placeholder/aria + <label>)."""
    bits = []
    for attr in ("name", "id", "placeholder", "aria-label"):
        v = el.get_attribute(attr)
        if v:
            bits.append(v)
    eid = el.get_attribute("id")
    if eid:
        lab = el.evaluate(
            """(_, id) => { const l = document.querySelector(`label[for="${id}"]`); return l ? l.innerText : ""; }""",
            eid,
        )
        if lab:
            bits.append(lab)
    return " ".join(bits).strip()


def is_required(el) -> bool:
    try:
        if el.get_attribute("required") is not None:
            return True
        if (el.get_attribute("aria-required") or "").lower() == "true":
            return True
    except Exception:
        pass
    # Many ATS mark required fields with an asterisk in the visible label.
    return "*" in field_key(el)


def enumerate_fields(page) -> list[dict]:
    """Read-only: list every meaningful form field on the page."""
    out = []
    for el in page.query_selector_all("input, textarea, select"):
        try:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            etype = (el.get_attribute("type") or "").lower()
            if tag == "input" and etype in SKIP_INPUT_TYPES:
                continue
            label = field_key(el)
            if not label or any(n in label.lower() for n in NOISE_LABELS):
                continue
            kind = "select" if tag == "select" else ("textarea" if tag == "textarea" else (etype or "text"))
            out.append({"label": label, "kind": kind, "required": is_required(el)})
        except Exception:
            continue
    return out


def list_file_inputs(page) -> list[str]:
    """Labels of file-upload inputs (resume / cover letter / etc.)."""
    labels = []
    for el in page.query_selector_all("input[type=file]"):
        labels.append(field_key(el) or "file upload")
    return labels


def has_captcha(page) -> bool:
    """True if the form has a CAPTCHA (so submission must be done by the human)."""
    try:
        sel = ("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], [class*='g-recaptcha'], "
               "[data-sitekey], textarea#g-recaptcha-response, [name='g-recaptcha-response']")
        return page.query_selector(sel) is not None
    except Exception:
        return False


def detect_multistep(page) -> bool:
    """Heuristic: does this look like a paginated multi-step application wizard?"""
    try:
        body = page.inner_text("body")[:8000].lower()
    except Exception:
        return False
    if re.search(r"\bstep\s*\d+\s*(of|/)\s*\d+\b", body):
        return True
    if re.search(r"\b\d\s*/\s*\d\b", body) and ("next" in body or "continue" in body):
        return True
    return False


def detect_login_wall(page) -> bool:
    try:
        if page.query_selector("input[type=password]"):
            return True
        body = page.inner_text("body").lower()[:4000]
    except Exception:
        return False
    signals = ["sign in to continue", "log in to apply", "create an account to apply",
               "please sign in", "you must be logged in"]
    return any(s in body for s in signals)


# ---- fill helpers (used by apply_runner) ---------------------------------- #
def fill_text(el, value: str) -> bool:
    try:
        if (el.input_value() or "").strip():
            return False  # don't clobber prefilled values
    except Exception:
        pass
    try:
        el.fill(value)
        return True
    except Exception:
        return False


def fill_select(el, value: str) -> bool:
    try:
        options = el.evaluate("(s) => Array.from(s.options).map(o => ({v:o.value, t:o.innerText}))")
    except Exception:
        return False
    target = (value or "").lower()
    for o in options:
        if target and target in (o["t"] or "").lower():
            try:
                el.select_option(value=o["v"])
                return True
            except Exception:
                return False
    return False
