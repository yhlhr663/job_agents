"""Pure logic for browser auto-fill: which ATS we attempt, the apply URL, and
the rules that map a form field's label/name to a value from profile.yaml.

No Playwright import here so it stays unit-testable. The actual browser driving
lives in apply_runner.py (a standalone subprocess).
"""
from __future__ import annotations

from urllib.parse import urlparse, urlsplit, urlunsplit

def policy_blocked(ats: str, blocklist) -> tuple[bool, str]:
    """Optional, user-configurable force-skip (settings.yaml `do_not_automate`).
    Empty by default — normally everything is judged live by assess_doability().
    Returns (blocked?, reason)."""
    if ats in set(blocklist or []):
        return True, f"{ats.title()} is on your do-not-automate list (settings.yaml) — apply manually."
    return False, ""


def assess_doability(requires_login: bool, num_fields: int,
                     has_captcha: bool, multistep: bool) -> tuple[str, str]:
    """Decide, from what was actually found on the page, whether auto-fill is viable.

    Returns (verdict, reason) where verdict is one of:
      "auto"    -> a standard form we can pre-fill (you still review & submit)
      "manual"  -> login / multi-step / no fillable form -> apply yourself
      "unknown" -> couldn't read a form (dynamic page, or "Apply" leads elsewhere)
    """
    if requires_login:
        return "manual", "The application requires login / account creation."
    if num_fields == 0:
        return "unknown", ("Couldn't find a fillable form on this page — it may load "
                           "dynamically or the real form is behind an 'Apply' button / account.")
    if multistep:
        return "manual", ("Looks like a multi-step application wizard — auto-fill might only "
                          "complete the first step, so it's safer to do this one yourself.")
    base = "Standard application form detected — I can pre-fill it"
    if has_captcha:
        return "auto", base + ", but it has a CAPTCHA, so you'll solve that and click submit."
    return "auto", base + "; you review and click submit."


def apply_url(url: str, ats: str) -> str:
    """Normalize to the page that actually shows the application form."""
    if ats == "lever" and "/apply" not in url:
        return url.rstrip("/") + "/apply"
    if ats == "ashby":
        # overview page -> /application sub-page; drop tracking query params.
        parts = urlsplit(url)
        path = parts.path.rstrip("/")
        if not path.endswith("/application"):
            path += "/application"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return url


def split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def build_field_rules(profile: dict) -> list[dict]:
    """Ordered rules. Each: {patterns, value, kind, exclude}.

    A form field is matched to the FIRST rule whose any `patterns` substring is in
    the field's combined label/name/placeholder key (and none of `exclude` is).
    Order matters: specific rules (first/last name) precede generic ones.
    """
    p = profile or {}
    loc = p.get("location") or {}
    links = p.get("links") or {}
    wa = p.get("work_authorization") or {}
    eeo = p.get("eeo") or {}
    pref = p.get("preferences") or {}

    first, last = split_name(p.get("full_name", ""))
    auth = "Yes" if wa.get("authorized_to_work_in_us") else "No"
    spons = "Yes" if wa.get("requires_sponsorship") else "No"
    city, state = loc.get("city", ""), loc.get("state", "")
    full_loc = ", ".join(x for x in (city, state) if x)

    raw = [
        (["first name", "firstname", "given name"], first, "text", []),
        (["last name", "lastname", "surname", "family name"], last, "text", []),
        (["full name", "your name", "fullname", "legal name", "name"], p.get("full_name", ""), "text",
         ["company", "organization", "employer", "reference", "username", "user name", "file"]),
        (["email"], p.get("email", ""), "text", []),
        (["phone", "mobile", "telephone"], p.get("phone", ""), "text", []),
        (["linkedin"], links.get("linkedin", ""), "text", []),
        (["github"], links.get("github", ""), "text", []),
        (["portfolio", "personal website", "website", "personal site"], links.get("portfolio", ""), "text", []),
        (["city"], city, "text", []),
        (["state", "province"], state, "text", []),
        (["location", "where are you", "current location"], full_loc, "text", []),
        (["legally authorized", "authorized to work", "work authorization",
          "eligible to work", "authorized to be employed"], auth, "yesno", []),
        (["require sponsorship", "need sponsorship", "visa sponsorship",
          "sponsorship now or in the future", "require visa"], spons, "yesno", []),
        (["gender"], eeo.get("gender", ""), "select", []),
        (["hispanic", "latino"], eeo.get("hispanic_latino", ""), "select", []),
        (["race", "ethnicity"], eeo.get("race_ethnicity", ""), "select", []),
        (["veteran"], eeo.get("veteran_status", ""), "select", []),
        (["disability"], eeo.get("disability_status", ""), "select", []),
        (["salary", "compensation expectation", "expected pay", "desired pay"],
         str(pref.get("desired_salary") or ""), "text", []),
        (["earliest start", "start date", "available to start", "availability"],
         str(pref.get("earliest_start_date") or ""), "text", []),
    ]
    return [
        {"patterns": pats, "value": val, "kind": kind, "exclude": exc}
        for pats, val, kind, exc in raw
        if val
    ]


def match_rule(field_key: str, rules: list[dict]) -> dict | None:
    """Return the first rule matching the normalized field key, else None."""
    key = (field_key or "").lower()
    if not key:
        return None
    for rule in rules:
        if any(x in key for x in rule["exclude"]):
            continue
        if any(pat in key for pat in rule["patterns"]):
            return rule
    return None


# --------------------------------------------------------------------------- #
# Field categorization — used by the inspector to describe what a form asks for,
# independent of whether the user's profile happens to have a value.
# Each: (patterns, category, profile_covered, exclude)
# --------------------------------------------------------------------------- #
FIELD_CATEGORIES = [
    (["first name", "given name"], "First name", True, []),
    (["last name", "surname", "family name"], "Last name", True, []),
    (["full name", "your name", "legal name", "name"], "Full name", True,
     ["company", "organization", "employer", "reference", "username", "user name", "file"]),
    (["email"], "Email", True, []),
    (["phone", "mobile", "telephone"], "Phone", True, []),
    (["linkedin"], "LinkedIn URL", True, []),
    (["github"], "GitHub URL", True, []),
    (["portfolio", "personal website", "website", "personal site"], "Portfolio/website", True, []),
    (["city"], "City", True, []),
    (["state", "province"], "State", True, []),
    (["location", "current location"], "Location", True, []),
    (["legally authorized", "authorized to work", "work authorization",
      "eligible to work", "authorized to be employed"], "Work authorization", True, []),
    (["require sponsorship", "need sponsorship", "visa sponsorship", "require visa"],
     "Visa sponsorship", True, []),
    (["gender"], "Gender (EEO)", True, []),
    (["hispanic", "latino"], "Hispanic/Latino (EEO)", True, []),
    (["race", "ethnicity"], "Race/Ethnicity (EEO)", True, []),
    (["veteran"], "Veteran status (EEO)", True, []),
    (["disability"], "Disability status (EEO)", True, []),
    (["salary", "compensation", "expected pay", "desired pay"], "Salary expectation", True, []),
    (["earliest start", "start date", "available to start", "availability"], "Start date", True, []),
    (["resume", "cv", "curriculum vitae"], "Resume", True, []),
    (["cover letter"], "Cover letter", False, []),
    (["how did you hear", "referral source", "source"], "How did you hear about us", False, []),
    (["pronoun"], "Pronouns", False, []),
    (["reference"], "References", False, []),
]


def categorize(field_key: str) -> tuple[str | None, bool]:
    """Map a field label to (category, profile_covered). (None, False) if unknown."""
    key = (field_key or "").lower()
    if not key:
        return None, False
    for pats, category, covered, exclude in FIELD_CATEGORIES:
        if any(x in key for x in exclude):
            continue
        if any(p in key for p in pats):
            return category, covered
    return None, False
