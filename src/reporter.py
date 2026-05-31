"""Persist per-job results in a human-readable layout.

    results/
      <company-slug>/                 (-> <company-slug>_2/ for another role there)
        resume_<company-slug>.pdf     (tailored resume)
        resume_<company-slug>.tex
        resume_<company-slug>.md      (the report: status, reason, links, JD)
        resume_<company-slug>_cover_letter.pdf   (optional)
      INDEX.md                        (run-level tracker across all jobs)
      .index.json                     (ledger behind INDEX.md)
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from .config import results_dir
from .models import Job, JobStatus


def _company_slug(company: str) -> str:
    """Lowercase, underscore-joined, filesystem-safe company slug. 'Saris Ai' -> 'saris_ai'."""
    s = (company or "company").lower()
    s = re.sub(r"[^\w\s-]", "", s)          # drop punctuation
    s = re.sub(r"[\s-]+", "_", s).strip("_")
    return s[:50] or "company"


def _unique_dir(slug: str) -> Path:
    """results/<slug>/, or <slug>_2, <slug>_3… so a second role at the same company
    gets its own folder instead of overwriting the first."""
    base = results_dir()
    d = base / slug
    n = 2
    while d.exists():
        d = base / f"{slug}_{n}"
        n += 1
    return d


def _ledger_path() -> Path:
    return results_dir() / ".index.json"


def _load_ledger() -> dict:
    p = _ledger_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def already_applied(company: str, title: str) -> str | None:
    """If a result for this exact company+role already exists (any session), return
    its .md path. Backed by the ledger so it works regardless of folder naming."""
    for rel, rec in _load_ledger().items():
        if rec.get("company") == company and rec.get("title") == title:
            return str(results_dir() / rel)
    return None


def save_result(job: Job, applied: bool, note: str = "") -> str:
    """Write the markdown report (+ copy resume artifacts). Returns the .md path.

    First save for a job picks a fresh <company-slug>[ _N ] folder; later saves
    (revise, cover letter, apply) reuse that same folder + basename and overwrite."""
    if job.result_path and Path(job.result_path).exists():
        out_dir = Path(job.result_path).parent
        name = Path(job.result_path).stem            # e.g. resume_speak
    else:
        slug = _company_slug(job.company)
        out_dir = _unique_dir(slug)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"resume_{slug}"
    md_path = out_dir / f"{name}.md"

    pdf_rel = tex_rel = cl_rel = ""
    if job.tailored_pdf_path and Path(job.tailored_pdf_path).exists():
        shutil.copy2(job.tailored_pdf_path, out_dir / f"{name}.pdf")
        pdf_rel = f"{name}.pdf"
    if job.tailored_tex:
        (out_dir / f"{name}.tex").write_text(job.tailored_tex, encoding="utf-8")
        tex_rel = f"{name}.tex"
    if job.cover_letter_path and Path(job.cover_letter_path).exists():
        shutil.copy2(job.cover_letter_path, out_dir / f"{name}_cover_letter.pdf")
        cl_rel = f"{name}_cover_letter.pdf"

    md_path.write_text(_render_md(job, applied, note, pdf_rel, tex_rel, cl_rel),
                       encoding="utf-8")
    job.result_path = str(md_path)
    _update_index(job, applied, pdf_rel)
    return str(md_path)


# --------------------------------------------------------------------------- #
# Run-level tracker: results/INDEX.md (+ JSON ledger)
# --------------------------------------------------------------------------- #
def _update_index(job: Job, applied: bool, pdf_rel: str):
    base = results_dir()
    ledger = _load_ledger()
    rel_dir = Path(job.result_path).parent.relative_to(base)
    rel_md = str(Path(job.result_path).relative_to(base))
    ledger[rel_md] = {
        "company": job.company, "title": job.title, "location": job.location,
        "status": "APPLIED" if applied else job.status.value, "url": job.url,
        "resume": f"{rel_dir}/{pdf_rel}" if pdf_rel else "",
        "cover_letter": bool(job.cover_letter_path),
        "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _ledger_path().write_text(json.dumps(ledger, indent=2))

    rows = sorted(ledger.values(), key=lambda r: (r["company"].lower(), r["title"].lower()))
    lines = ["# Application tracker", "",
             f"_{len(rows)} job(s) · updated {datetime.now():%Y-%m-%d %H:%M}_", "",
             "| Company | Role | Status | Resume | Cover letter | When | Posting |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        resume = f"[PDF]({r['resume']})" if r.get("resume") else "—"
        cl = "✅" if r.get("cover_letter") else "—"
        lines.append(f"| {r['company']} | {r['title']} | {r['status']} | {resume} | {cl} | "
                     f"{r['when']} | [link]({r['url']}) |")
    (base / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render_md(job: Job, applied: bool, note: str, pdf_rel: str, tex_rel: str,
               cl_rel: str = "") -> str:
    if applied:
        status_line = "✅ **APPLIED** (resume tailored & saved)"
    elif job.status == JobStatus.SKIPPED:
        status_line = "⏭️ **SKIPPED**"
    else:
        status_line = f"⚠️ **{job.status.value.upper()}**"

    lines = [
        f"# {job.title} — {job.company}",
        "",
        f"- **Status:** {status_line}",
        f"- **When:** {datetime.now():%Y-%m-%d %H:%M}",
        f"- **Location:** {job.location or 'n/a'}",
        f"- **Source ({job.ats}):** {job.url}",
    ]
    if job.status_reason:
        lines.append(f"- **Reason:** {job.status_reason}")
    if note:
        lines.append(f"- **Note:** {note}")
    if pdf_rel:
        lines.append(f"- **Tailored resume (PDF):** [{pdf_rel}]({pdf_rel})")
    if tex_rel:
        lines.append(f"- **Tailored resume (LaTeX):** [{tex_rel}]({tex_rel})")
    if cl_rel:
        lines.append(f"- **Cover letter (PDF):** [{cl_rel}]({cl_rel})")

    lines += ["", "---", "", "## Job description (captured)", "", job.description or "_not captured_"]
    return "\n".join(lines)
