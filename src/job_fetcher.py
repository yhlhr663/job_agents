"""Fetch a job posting from a URL and extract structured info.

Strategy per URL:
  * Greenhouse / Lever  -> use their public JSON APIs (clean, reliable).
  * Everything else      -> fetch HTML, strip to text.
Then Claude turns the raw text into structured fields (title, company,
location, US?, login-required?).
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from . import prompts
from .models import Job, JobStatus

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 20


# --------------------------------------------------------------------------- #
# ATS detection
# --------------------------------------------------------------------------- #
def detect_ats(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "linkedin.com" in host:
        return "linkedin"
    if "indeed.com" in host:
        return "indeed"
    if "avature.net" in host:
        return "avature"
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host or "workday" in host:
        return "workday"
    return "other"


def looks_like_job_url(url: str, ats: str) -> bool:
    """Cheap pre-filter to reject obvious non-job links (feeds, notifications,
    profiles, search pages) before we spend a fetch + API call on them."""
    u = url.lower()
    if ats == "linkedin":
        return "/jobs/view/" in u or "currentjobid=" in u
    if ats == "indeed":
        return "viewjob" in u or "jk=" in u
    if ats == "greenhouse":
        return bool(re.search(r"/jobs/\d+", u)) or "gh_jid=" in u
    if ats == "lever":
        return bool(re.search(r"lever\.co/[^/]+/[0-9a-f-]{8,}", u))
    if ats == "ashby":
        return bool(re.search(r"ashbyhq\.com/[^/]+/[0-9a-f-]{8,}", u))
    if ats == "workday":
        return "/job/" in u
    if ats == "avature":
        return "pipelineid=" in u or "jobdetail" in u or "jobapplication" in u
    return True  # unknown company site: let the fetch + extraction decide


# --------------------------------------------------------------------------- #
# Raw-text extraction per source
# --------------------------------------------------------------------------- #
def _greenhouse_raw(url: str) -> dict:
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?[^#]*for=)?([^/?#]+)", url)
    board = m.group(1) if m else None
    jid = re.search(r"jobs/(\d+)", url) or re.search(r"token=(\d+)", url)
    if not (board and jid):
        return {}
    api = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid.group(1)}"
    r = requests.get(api, headers=HEADERS, timeout=TIMEOUT)
    if r.status_code != 200:
        return {}
    d = r.json()
    return {
        "title": d.get("title", ""),
        "company": board.replace("-", " ").title(),
        "location": (d.get("location") or {}).get("name", ""),
        "description": _html_to_text(d.get("content", "")),
        "requires_login": False,
    }


def _lever_raw(url: str) -> dict:
    m = re.search(r"lever\.co/([^/?#]+)/([0-9a-f-]+)", url)
    if not m:
        return {}
    company, jid = m.group(1), m.group(2)
    api = f"https://api.lever.co/v0/postings/{company}/{jid}"
    r = requests.get(api, headers=HEADERS, timeout=TIMEOUT)
    if r.status_code != 200:
        return {}
    d = r.json()
    cats = d.get("categories") or {}
    return {
        "title": d.get("text", ""),
        "company": company.replace("-", " ").title(),
        "location": cats.get("location", ""),
        "description": d.get("descriptionPlain") or _html_to_text(d.get("description", "")),
        "requires_login": False,
    }


def _resolve_embedded_greenhouse(url: str) -> tuple[str | None, str | None]:
    """Many company career sites (e.g. pinterestcareers.com) embed Greenhouse and
    carry a `gh_jid` param. Resolve (board_token, job_id) so we can use the clean
    Greenhouse-hosted job page/form instead of the JS-heavy wrapper."""
    jid_m = re.search(r"gh_jid=(\d+)", url)
    if not jid_m:
        return None, None
    jid = jid_m.group(1)
    try:
        html = requests.get(url, headers=HEADERS, timeout=TIMEOUT).text
    except requests.RequestException:
        html = ""
    for pat in (r"boards\.greenhouse\.io/embed/job_app\?[^\"']*for=([A-Za-z0-9_-]+)",
                r"greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)",
                r"job-boards\.greenhouse\.io/([A-Za-z0-9_-]+)",
                r"boards\.greenhouse\.io/([A-Za-z0-9_-]+)"):
        m = re.search(pat, html)
        if m and m.group(1) not in ("embed", "v1"):
            return m.group(1), jid
    # Fallback: guess board from the domain (pinterestcareers.com -> pinterest),
    # skipping common subdomain prefixes like "www"/"careers"/"jobs".
    host = (urlparse(url).hostname or "").lower()
    labels = [l for l in host.split(".") if l not in ("www", "careers", "jobs", "com", "net", "org", "io")]
    guess = (labels[0] if labels else "").replace("careers", "").replace("jobs", "").replace("-", "")
    return (guess or None), jid


def _ashby_raw(url: str) -> dict:
    m = re.search(r"ashbyhq\.com/([^/?#]+)/([0-9a-fA-F-]{36})", url)
    if not m:
        return {}
    org, jid = m.group(1), m.group(2)
    api = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
    r = requests.get(api, headers=HEADERS, timeout=TIMEOUT)
    if r.status_code != 200:
        return {}
    data = r.json()
    for job in data.get("jobs", []):
        if job.get("id") == jid:
            desc = job.get("descriptionPlain") or _html_to_text(job.get("descriptionHtml", ""))
            return {
                "title": job.get("title", ""),
                "company": org.replace("-", " ").title(),
                "location": job.get("location", "") or job.get("locationName", ""),
                "description": desc,
                "requires_login": False,
            }
    return {}


def _generic_raw(url: str) -> dict:
    """Fetch HTML and strip to readable text. Best effort for the long tail."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    text = _html_to_text(r.text)
    blocked = r.status_code in (401, 403) or _looks_like_login_wall(text)
    return {"description": text, "requires_login": blocked, "_status": r.status_code}


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _looks_like_login_wall(text: str) -> bool:
    t = text.lower()[:3000]
    signals = ["sign in to continue", "join linkedin", "please log in", "create an account to apply"]
    return any(s in t for s in signals)


# --------------------------------------------------------------------------- #
# Claude structuring
# --------------------------------------------------------------------------- #
def _structure_with_claude(client, model: str, raw_text: str, max_chars: int) -> dict:
    raw = raw_text[:max_chars]
    msg = client.messages.create(
        model=model,
        max_tokens=3000,
        messages=[{"role": "user", "content": prompts.load("extract_job").format(raw=raw)}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return _parse_json(text)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(m.group(0)) if m else {}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def fetch_job(url: str, client, model: str, max_chars: int = 12000) -> Job:
    url = url.strip()
    ats = detect_ats(url)

    # Company career sites that embed Greenhouse (carry gh_jid) -> use the clean
    # Greenhouse-hosted page so fetch/inspect/apply all work on a real form.
    if ats == "other" and "gh_jid=" in url:
        board, jid = _resolve_embedded_greenhouse(url)
        if board and jid:
            url = f"https://job-boards.greenhouse.io/{board}/jobs/{jid}"
            ats = "greenhouse"

    job = Job(url=url, ats=ats)

    try:
        if ats == "greenhouse":
            raw = _greenhouse_raw(url) or _generic_raw(url)
        elif ats == "lever":
            raw = _lever_raw(url) or _generic_raw(url)
        elif ats == "ashby":
            raw = _ashby_raw(url) or _generic_raw(url)
        else:
            raw = _generic_raw(url)
    except requests.RequestException as e:
        job.status = JobStatus.FETCH_FAILED
        job.status_reason = f"Could not fetch page: {e}"
        return job

    if not raw.get("description"):
        job.status = JobStatus.FETCH_FAILED
        job.status_reason = "No job-description text could be extracted (page may require login)."
        if ats == "linkedin":
            job.status_reason += " LinkedIn often blocks scraping; paste a direct ATS link if possible."
        return job

    # Let Claude clean up + classify (fills title/company/location/US/login).
    try:
        structured = _structure_with_claude(client, model, raw["description"], max_chars)
    except Exception as e:  # noqa: BLE001 — surface any API/parse error as a fetch issue
        structured = {}
        job.status_reason = f"(structuring fell back to raw text: {e}) "

    job.title = raw.get("title") or structured.get("title") or job.title
    job.company = raw.get("company") or structured.get("company") or job.company
    job.location = raw.get("location") or structured.get("location") or ""
    job.description = structured.get("description") or raw["description"]
    job.is_us = structured.get("is_us")
    job.requires_login = bool(raw.get("requires_login") or structured.get("requires_login"))
    job.status = JobStatus.FETCHED
    return job
