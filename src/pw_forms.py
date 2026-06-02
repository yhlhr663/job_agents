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
    """Read-only: list every meaningful text/textarea/select field on the page.

    Radio/checkbox groups are reported separately by enumerate_radio_groups()
    so a Yes/No question appears once (with its options) rather than as N rows.
    """
    out = []
    for el in page.query_selector_all("input, textarea, select"):
        try:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            etype = (el.get_attribute("type") or "").lower()
            if tag == "input" and (etype in SKIP_INPUT_TYPES or etype in ("radio", "checkbox")):
                continue
            label = field_key(el)
            if not label or any(n in label.lower() for n in NOISE_LABELS):
                continue
            kind = "select" if tag == "select" else ("textarea" if tag == "textarea" else (etype or "text"))
            entry = {"label": label, "kind": kind, "required": is_required(el)}
            if tag == "select":
                entry["options"] = _select_option_texts(el)
            out.append(entry)
        except Exception:
            continue
    return out


# JS helpers reused for reading and (later) filling single-choice questions. ---
_OPTION_LABEL_JS = """(el) => {
  if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
               if (l && l.innerText.trim()) return l.innerText.trim(); }
  const p = el.closest('label'); if (p && p.innerText.trim()) return p.innerText.trim();
  const al = el.getAttribute('aria-label'); if (al) return al.trim();
  return el.getAttribute('value') || '';
}"""

_GROUP_QUESTION_JS = """(el) => {
  const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
  const fs = el.closest('fieldset');
  if (fs) { const lg = fs.querySelector('legend'); if (lg && clean(lg.innerText)) return clean(lg.innerText); }
  const grp = el.closest('[role=radiogroup],[role=group]');
  if (grp) {
    const lb = grp.getAttribute('aria-labelledby');
    if (lb) { const n = document.getElementById(lb); if (n && clean(n.innerText)) return clean(n.innerText); }
    const al = grp.getAttribute('aria-label'); if (al) return clean(al);
  }
  let node = el;
  for (let i = 0; i < 6 && node; i++) {
    node = node.parentElement; if (!node) break;
    const cand = node.querySelector(
      'label, legend, h1,h2,h3,h4,h5,h6, [class*=label], [class*=Label], [class*=question], [class*=Question], [class*=title], [class*=Title]');
    if (cand) { const t = clean(cand.innerText);
      if (t && t.length > 3 && t.length < 300 && !/^(yes|no)$/i.test(t)) return t; }
  }
  return el.getAttribute('name') || '';
}"""


def _select_option_texts(el) -> list[str]:
    try:
        return el.evaluate(
            "(s) => Array.from(s.options).map(o => o.innerText.trim()).filter(Boolean)")
    except Exception:
        return []


def enumerate_radio_groups(page) -> list[dict]:
    """Group radio buttons by `name` into single-choice questions.

    Returns [{name, question, required, options:[{label, value}]}] — the
    question text is recovered from a fieldset legend / aria-label / nearby
    label, falling back to the input's `name`.
    """
    groups: dict[str, dict] = {}
    for el in page.query_selector_all("input[type=radio]"):
        try:
            name = el.get_attribute("name") or ""
            if not name:
                continue
            if name not in groups:
                question = ""
                try:
                    question = el.evaluate(_GROUP_QUESTION_JS)
                except Exception:
                    pass
                groups[name] = {"name": name, "question": question,
                                "required": False, "options": []}
            try:
                label = el.evaluate(_OPTION_LABEL_JS)
            except Exception:
                label = el.get_attribute("value") or ""
            groups[name]["options"].append(
                {"label": label, "value": el.get_attribute("value") or label})
            if is_required(el):
                groups[name]["required"] = True
        except Exception:
            continue
    return [g for g in groups.values() if g["options"]]


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
