"""Tailor a base LaTeX resume to a specific job using Claude.

Hard rules baked into the prompt:
  * Never invent experience, employers, job titles, dates, degrees, or metrics.
  * Only reorder / re-emphasize / re-word what's already true, and surface
    real keywords from the JD where they genuinely apply.
  * Output must be a complete, compilable .tex document.
"""
from __future__ import annotations

from pathlib import Path

from . import config, prompts
from .latex_compiler import compile_tex, page_count


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # drop opening fence line and trailing fence
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _text(msg) -> str:
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _generate_and_compile(client, model, system, messages, out_dir,
                          max_fix_attempts=2) -> tuple[bool, str, str, str]:
    """Run the model, compile its LaTeX, and on failure feed the error back to it
    to self-correct (up to max_fix_attempts). Shared by tailor + revise."""
    msg = client.messages.create(model=model, system=system, max_tokens=8000, messages=messages)
    tex = _strip_fences(_text(msg))
    max_pages = config.settings().get("max_resume_pages", 1)
    for attempt in range(max_fix_attempts + 1):
        ok, pdf_path, log = compile_tex(tex, out_dir=out_dir, basename="tailored_resume")
        if ok:
            # Enforce the page limit by asking the model to trim, then recompile.
            pages = page_count(pdf_path)
            if pages <= max_pages or attempt == max_fix_attempts:
                return True, tex, pdf_path, ""
            nxt = prompts.load("tailor_shorten").format(pages=pages)
        else:
            if attempt == max_fix_attempts:
                break
            nxt = prompts.load("tailor_fix").format(log=log[:4000])
        messages += [{"role": "assistant", "content": tex},
                     {"role": "user", "content": nxt}]
        msg = client.messages.create(model=model, system=system, max_tokens=8000, messages=messages)
        tex = _strip_fences(_text(msg))
    return False, tex, "", "Tailored LaTeX would not compile after retries. See saved .tex/.log."


def tailor_resume(job, base_tex: str, client, model: str,
                  out_dir: Path, max_fix_attempts: int = 2) -> tuple[bool, str, str, str]:
    """Tailor the base resume to the job + compile. Returns (ok, tex, pdf_path, message)."""
    desc = (job.description or "")[:12000]
    messages = [{
        "role": "user",
        "content": prompts.load("tailor_user").format(
            base_tex=base_tex,
            title=job.title, company=job.company,
            location=job.location, description=desc,
        ),
    }]
    return _generate_and_compile(client, model, prompts.load("tailor_system"),
                                 messages, out_dir, max_fix_attempts)


def revise_resume(current_tex: str, feedback: str, client, model: str,
                  out_dir: Path, max_fix_attempts: int = 2) -> tuple[bool, str, str, str]:
    """Apply the user's feedback to the already-tailored resume + recompile."""
    messages = [{
        "role": "user",
        "content": prompts.load("tailor_revise").format(
            current_tex=current_tex, feedback=feedback,
        ),
    }]
    return _generate_and_compile(client, model, prompts.load("tailor_system"),
                                 messages, out_dir, max_fix_attempts)
