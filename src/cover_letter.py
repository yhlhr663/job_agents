"""Optional: generate a tailored cover letter and compile it to a clean PDF.

Only runs when the user explicitly asks for one. Uses facts from the tailored
resume + JD; never fabricates.
"""
from __future__ import annotations

from pathlib import Path

from . import prompts
from .latex_compiler import compile_tex

# Map each LaTeX-special char to its escaped form (applied char-by-char on the
# ORIGINAL text, so the replacement strings are never themselves re-escaped).
_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _escape(text: str) -> str:
    return "".join(_LATEX_SPECIALS.get(ch, ch) for ch in text)


def _wrap(body: str, profile: dict) -> str:
    name = _escape(profile.get("full_name", "") or "")
    loc = profile.get("location") or {}
    contact_bits = [profile.get("email", ""), profile.get("phone", ""),
                    ", ".join(x for x in (loc.get("city", ""), loc.get("state", "")) if x)]
    contact = _escape(" $\\cdot$ ".join(b for b in contact_bits if b)) if any(contact_bits) else ""
    body_tex = _escape(body)
    return (
        "\\documentclass[11pt]{article}\n"
        "\\usepackage[margin=1in]{geometry}\n"
        "\\usepackage{parskip}\n"
        "\\usepackage[hidelinks]{hyperref}\n"
        "\\pagestyle{empty}\n"
        "\\begin{document}\n"
        f"{{\\Large \\textbf{{{name}}}}}\\\\\n"
        f"{contact}\n\n"
        "\\vspace{1em}\n\n"
        f"{body_tex}\n"
        "\\end{document}\n"
    )


def _text(msg) -> str:
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def generate_cover_letter(job, profile: dict, resume_tex: str, client, model: str,
                          out_dir: Path) -> tuple[bool, str, str, str]:
    """Returns (ok, letter_text, pdf_path, message)."""
    user = prompts.load("cover_letter").format(
        full_name=profile.get("full_name", "the candidate"),
        title=job.title, company=job.company,
        description=(job.description or "")[:8000],
        resume_tex=(resume_tex or "")[:8000],
    )
    msg = client.messages.create(model=model, max_tokens=1500,
                                 messages=[{"role": "user", "content": user}])
    letter = _text(msg).strip()
    if not letter:
        return False, "", "", "Model returned no cover-letter text."

    ok, pdf_path, log = compile_tex(_wrap(letter, profile), out_dir=out_dir,
                                    basename="cover_letter")
    if ok:
        return True, letter, pdf_path, ""
    return False, letter, "", f"Cover letter compiled with errors: {log[:300]}"
