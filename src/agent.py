"""The conversational brain.

A Claude tool-use loop with these tools:
  * list_base_resumes()       -> names of .tex resumes the user can pick
  * select_base_resume(name)  -> choose which base resume to tailor from
  * add_jobs(urls)            -> fetch + parse postings, store them, return summaries
  * tailor_jobs(indices)      -> tailor the chosen resume to jobs, compile PDFs

The agent CHATS, FETCHES, and TAILORS. It never submits applications — that's a
human click in the UI (review-then-submit), which calls reporter.save_result.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import config, cover_letter, inspector, job_fetcher, knowledge, prompts, reporter
from .config import settings
from .models import Job, JobStatus
from .resume_tailor import revise_resume, tailor_resume

TOOLS = [
    {
        "name": "list_base_resumes",
        "description": "List the base resume files (.tex) the user has available to "
                       "tailor from. Call this when the user asks what resumes exist, "
                       "or before tailoring if none has been selected yet.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "select_base_resume",
        "description": "Choose which base resume to use for tailoring. Accepts the "
                       "file name or a partial/fuzzy name (e.g. 'backend' matches "
                       "'swe_backend.tex'). Must be called before tailoring.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Resume file name or partial match."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "add_jobs",
        "description": "Fetch and parse one or more job postings from their URLs. "
                       "Call this whenever the user provides job links. Returns a "
                       "summary of each job (title, company, location, US?, whether "
                       "login is required, and a status).",
        "input_schema": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"},
                         "description": "Job posting URLs."},
            },
            "required": ["urls"],
        },
    },
    {
        "name": "inspect_requirements",
        "description": "Open a job's application form and report what it requires: "
                       "documents (resume, cover letter, portfolio), the questions it "
                       "asks, which fields can be auto-filled from the user's profile vs "
                       "which need the user's own input, and whether login is required. "
                       "Call this when the user asks what's needed to apply to a job. "
                       "Pass the 1-based job numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {"type": "array", "items": {"type": "integer"},
                            "description": "1-based job numbers to inspect."},
            },
            "required": ["indices"],
        },
    },
    {
        "name": "answer_questions",
        "description": "Save the user-confirmed answers to a job's free-text/essay "
                       "application questions — the ones inspect_requirements returned "
                       "under 'essay_questions_to_draft'. Call this ONLY after you've "
                       "drafted answers, shown them to the user, and they confirmed or "
                       "edited them. These answers get auto-filled when the user clicks "
                       "Auto-fill. Do NOT use this for single-choice questions — the user "
                       "answers those themselves in the browser. Pass the 1-based job "
                       "number and a list of {question, answer}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "1-based job number."},
                "answers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string",
                                         "description": "The question text, matching the form label."},
                            "answer": {"type": "string",
                                       "description": "The confirmed answer (an exact option for "
                                                      "single-choice questions)."},
                        },
                        "required": ["question", "answer"],
                    },
                },
            },
            "required": ["index", "answers"],
        },
    },
    {
        "name": "recall_knowledge",
        "description": "Recall what the agent has learned over time about how different "
                       "ATS platforms behave (doable/not and why). Use when the user asks "
                       "'what do you know about X' or 'is X usually doable'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_jobs",
        "description": "List every job in the current session with its number, title, "
                       "company, status, and whether it's already saved. Use when the user "
                       "asks what's in the session / for a status overview.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "generate_cover_letter",
        "description": "OPTIONAL: generate a tailored cover letter (PDF) for jobs the user "
                       "chooses. Only call when the user explicitly asks for a cover letter. "
                       "The job must already be tailored. Pass the 1-based job numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {"type": "array", "items": {"type": "integer"},
                            "description": "1-based job numbers to write a cover letter for."},
            },
            "required": ["indices"],
        },
    },
    {
        "name": "revise_resume",
        "description": "Revise an already-tailored resume using the user's feedback "
                       "(e.g. 'emphasize Python', 'shorten the summary', 'move education up'), "
                       "then recompile. Pass the 1-based job number and the feedback text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "1-based job number."},
                "feedback": {"type": "string", "description": "What to change."},
            },
            "required": ["index", "feedback"],
        },
    },
    {
        "name": "tailor_jobs",
        "description": "Tailor the user's chosen base resume to specific jobs and "
                       "compile each to a PDF. Pass the 1-based numbers shown in the "
                       "job list. Call this only after the user says which jobs to "
                       "apply to. After it returns, tell the user to review the PDF "
                       "preview(s) and click Apply.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {"type": "array", "items": {"type": "integer"},
                            "description": "1-based job numbers to tailor."},
            },
            "required": ["indices"],
        },
    },
    {
        "name": "list_models",
        "description": "Show the available Claude models and which ones are currently "
                       "selected for chat and for resume tailoring.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_model",
        "description": "Switch the Claude model used for chat and/or resume tailoring. "
                       "Accepts a short alias (opus, sonnet, haiku) or a full model id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": ["chat", "tailor", "both"],
                         "description": "Which job to set the model for."},
                "model": {"type": "string",
                          "description": "Alias (opus|sonnet|haiku) or full model id."},
            },
            "required": ["role", "model"],
        },
    },
    {
        "name": "update_profile",
        "description": "Save the user's personal info to config/profile.yaml (used to "
                       "tailor resumes and auto-fill applications). Call this when the "
                       "user shares any details: name, email, phone, location, links "
                       "(linkedin/github/portfolio), work authorization, sponsorship "
                       "needs, EEO self-identification, or preferences (salary, start "
                       "date, relocation). Only include the fields the user actually "
                       "provided; existing values are preserved (deep-merged).",
        "input_schema": {
            "type": "object",
            "properties": {
                "full_name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "location": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"}, "state": {"type": "string"},
                        "country": {"type": "string"},
                    },
                },
                "links": {
                    "type": "object",
                    "properties": {
                        "linkedin": {"type": "string"}, "github": {"type": "string"},
                        "portfolio": {"type": "string"}, "other": {"type": "string"},
                    },
                },
                "work_authorization": {
                    "type": "object",
                    "properties": {
                        "authorized_to_work_in_us": {"type": "boolean"},
                        "requires_sponsorship": {"type": "boolean"},
                        "status_note": {"type": "string"},
                    },
                },
                "eeo": {
                    "type": "object",
                    "properties": {
                        "gender": {"type": "string"}, "race_ethnicity": {"type": "string"},
                        "veteran_status": {"type": "string"},
                        "disability_status": {"type": "string"},
                        "hispanic_latino": {"type": "string"},
                    },
                },
                "preferences": {
                    "type": "object",
                    "properties": {
                        "earliest_start_date": {"type": "string"},
                        "desired_salary": {"type": "string"},
                        "willing_to_relocate": {"type": "boolean"},
                        "notes": {"type": "string"},
                    },
                },
            },
        },
    },
]

# Fallback aliases if settings.yaml doesn't define model_aliases.
DEFAULT_MODEL_ALIASES = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}

def _system() -> str:
    """Loaded fresh each turn so edits to prompts/agent_system.md take effect live."""
    return prompts.load("agent_system")


class AgentContext:
    """Holds everything the tools need + the list of jobs for this session."""

    def __init__(self, client):
        self.client = client
        self.cfg = settings()
        # Selected via chat tools; empty until the user picks one.
        self.base_resume_name = ""
        self.base_tex = ""
        # Mutable per-session model choices (start from settings.yaml defaults).
        self.models = dict(self.cfg["models"])
        # Switchable models, editable in settings.yaml (alias -> full model id).
        self.aliases = self.cfg.get("model_aliases") or DEFAULT_MODEL_ALIASES
        self.jobs: list[Job] = []
        self.work_dir = Path(tempfile.mkdtemp(prefix="job_agents_"))

    # ---- tool implementations --------------------------------------------- #
    def _list_resumes(self) -> str:
        names = config.list_base_resumes()
        return json.dumps({
            "available": names,
            "selected": self.base_resume_name or None,
            "hint": "" if names else "No .tex files in base_resumes/ — add one and refresh.",
        }, indent=2)

    def _select_resume(self, name: str) -> str:
        names = config.list_base_resumes()
        if not names:
            return "No resumes found in base_resumes/. Add a .tex file there first."
        q = (name or "").lower().strip().removesuffix(".tex")
        exact = [n for n in names if n.lower().removesuffix(".tex") == q]
        partial = [n for n in names if q and q in n.lower()]
        match = exact[0] if exact else (partial[0] if len(partial) == 1 else None)
        if not match:
            if len(partial) > 1:
                return f"'{name}' is ambiguous. Matches: {partial}. Which one?"
            return f"No resume matches '{name}'. Available: {names}"
        self.base_resume_name = match
        self.base_tex = config.read_base_resume(match)
        return f"Selected base resume: {match}"

    def _list_models(self) -> str:
        return json.dumps({
            "available": self.aliases,
            "current": {"chat": self.models["chat"], "tailor": self.models["tailor"]},
        }, indent=2)

    def _set_model(self, role: str, model: str) -> str:
        resolved = self.aliases.get((model or "").lower().strip(), model)
        if role not in ("chat", "tailor", "both"):
            return f"Unknown role '{role}'. Use chat, tailor, or both."
        roles = ["chat", "tailor"] if role == "both" else [role]
        for r in roles:
            self.models[r] = resolved
        return f"Set {role} model to {resolved}."

    def _update_profile(self, updates: dict) -> str:
        if not updates:
            return "Nothing to update — no fields provided."
        merged = config.update_profile(updates)
        changed = ", ".join(updates.keys())
        have = bool(merged.get("full_name") and merged.get("email"))
        return json.dumps({
            "saved": True, "updated_fields": changed,
            "profile_complete_enough_to_apply": have,
        }, indent=2)

    def _add_jobs(self, urls: list[str]) -> str:
        added, invalid = [], []
        for url in urls:
            url = url.strip()
            ats = job_fetcher.detect_ats(url)
            # 1) cheap reject of obvious non-job links (feeds, notifications, etc.)
            if not job_fetcher.looks_like_job_url(url, ats):
                invalid.append({"url": url, "reason": "Not a job posting link (e.g. a "
                                "feed/notifications/search page) — not added."})
                continue
            job = job_fetcher.fetch_job(
                url, self.client, self.models["chat"],
                self.cfg.get("max_job_description_chars", 12000),
            )
            # 2) reject anything we couldn't actually read a job from
            if job.status == JobStatus.FETCH_FAILED or not job.description:
                invalid.append({"url": url,
                                "reason": job.status_reason or "Couldn't read a job posting here."})
                continue
            if self.cfg.get("us_jobs_only", True) and job.is_us is False:
                job.status = JobStatus.SKIPPED
                job.status_reason = "Job is not US-based (out of scope)."
            self.jobs.append(job)
            prior = reporter.already_applied(job.company, job.title)
            added.append({
                "index": len(self.jobs),
                "title": job.title, "company": job.company,
                "location": job.location, "is_us": job.is_us,
                "status": job.status.value, "reason": job.status_reason,
                "already_applied": bool(prior),
            })
        return json.dumps({"added": added, "invalid": invalid}, indent=2)

    def _inspect_requirements(self, indices: list[int]) -> str:
        out = []
        for i in indices:
            if i < 1 or i > len(self.jobs):
                out.append({"index": i, "result": "no such job number"})
                continue
            job = self.jobs[i - 1]
            info = inspector.inspect_job(job)
            verdict = info.get("verdict", "unknown")

            # Self-update: remember this ATS's behavior, but only if it's new/changed.
            learned = knowledge.record(job.ats, verdict, info.get("doable_reason", ""), job.url)

            out.append({
                "index": i, "job": job.short(), "ats": job.ats,
                "doable": verdict,                       # auto | manual | unknown
                "doable_reason": info.get("doable_reason", ""),
                "requires_login": info.get("requires_login"),
                "has_captcha": info.get("has_captcha"),
                "multistep": info.get("multistep"),
                "documents": info.get("documents", []),
                "auto_fillable_fields": [f.get("category") for f in info.get("profile_fields", [])],
                # Free-text questions the agent should DRAFT answers for.
                "essay_questions_to_draft": [
                    {"question": f.get("label"), "required": f.get("required")}
                    for f in info.get("custom_questions", [])
                    if f.get("kind") in ("text", "textarea", "email", "tel", "url", None)
                ],
                # Single-choice questions the USER answers themselves in the browser.
                "single_choice_questions_user_answers": [
                    {"question": f.get("label"), "required": f.get("required"),
                     "options": f.get("options", [])}
                    for f in info.get("custom_questions", [])
                    if f.get("kind") in ("radio", "select", "checkbox")
                ],
                # Material for drafting the essay answers above.
                "job_description": (job.description or "")[:6000],
                "your_profile": config.load_profile(),
                "learned_something_new": learned,
                "note": info.get("message", ""),
            })
        return json.dumps(out, indent=2)

    def _answer_questions(self, index: int, answers: list[dict]) -> str:
        if index < 1 or index > len(self.jobs):
            return json.dumps({"index": index, "result": "no such job number"})
        job = self.jobs[index - 1]
        clean = [{"question": (a.get("question") or "").strip(),
                  "answer": (a.get("answer") or "").strip()}
                 for a in (answers or [])
                 if (a.get("question") or "").strip() and (a.get("answer") or "").strip()]
        # Merge by question text (replace existing answer, keep others).
        by_q = {a["question"].lower(): a for a in job.custom_answers}
        for a in clean:
            by_q[a["question"].lower()] = a
        job.custom_answers = list(by_q.values())
        return json.dumps({"index": index, "saved": len(clean),
                           "total_on_file": len(job.custom_answers),
                           "note": "These will be auto-filled when the user clicks Auto-fill."})

    def _recall_knowledge(self) -> str:
        return json.dumps(knowledge.summary() or {"note": "Nothing learned yet."}, indent=2)

    def _list_jobs(self) -> str:
        out = [{
            "index": i, "title": j.title, "company": j.company,
            "location": j.location, "status": j.status.value,
            "saved_to": j.result_path or None,
            "has_cover_letter": bool(j.cover_letter_path),
        } for i, j in enumerate(self.jobs, start=1)]
        return json.dumps(out or {"note": "No jobs added yet."}, indent=2)

    def _generate_cover_letter(self, indices: list[int]) -> str:
        out = []
        for i in indices:
            if i < 1 or i > len(self.jobs):
                out.append({"index": i, "result": "no such job number"})
                continue
            job = self.jobs[i - 1]
            if not job.tailored_tex:
                out.append({"index": i, "result": "tailor this job first (need the resume)."})
                continue
            jdir = self.work_dir / f"job_{i}"
            jdir.mkdir(parents=True, exist_ok=True)
            ok, _text, pdf, msg = cover_letter.generate_cover_letter(
                job, config.load_profile(), job.tailored_tex,
                self.client, self.models["tailor"], jdir,
            )
            if ok:
                job.cover_letter_path = pdf
                path = reporter.save_result(job, applied=job.status == JobStatus.APPLIED,
                                            note="Cover letter generated.")
                out.append({"index": i, "result": "cover letter generated", "saved_to": path})
            else:
                out.append({"index": i, "result": f"cover letter failed: {msg}"})
        return json.dumps(out, indent=2)

    def _revise_resume(self, index: int, feedback: str) -> str:
        if index < 1 or index > len(self.jobs):
            return json.dumps({"index": index, "result": "no such job number"})
        job = self.jobs[index - 1]
        if not job.tailored_tex:
            return json.dumps({"index": index, "result": "tailor this job first."})
        jdir = self.work_dir / f"job_{index}"
        jdir.mkdir(parents=True, exist_ok=True)
        ok, tex, pdf, msg = revise_resume(job.tailored_tex, feedback, self.client,
                                          self.models["tailor"], jdir)
        if not ok:
            return json.dumps({"index": index, "result": f"revision didn't compile: {msg}"})
        job.tailored_tex, job.tailored_pdf_path = tex, pdf
        job.status = JobStatus.TAILORED
        path = reporter.save_result(job, applied=False, note=f"Revised per feedback: {feedback[:120]}")
        return json.dumps({"index": index, "result": "revised & recompiled", "saved_to": path})

    def _tailor_jobs(self, indices: list[int]) -> str:
        if not self.base_tex:
            return ("No base resume selected. Call list_base_resumes and ask the user "
                    "which to use (select_base_resume) before tailoring.")
        out = []
        for i in indices:
            if i < 1 or i > len(self.jobs):
                out.append({"index": i, "result": "no such job number"})
                continue
            job = self.jobs[i - 1]
            if job.status == JobStatus.SKIPPED:
                out.append({"index": i, "result": f"skipped: {job.status_reason}"})
                continue
            if job.status == JobStatus.FETCH_FAILED:
                out.append({"index": i, "result": f"cannot tailor: {job.status_reason}"})
                continue

            jdir = self.work_dir / f"job_{i}"
            jdir.mkdir(parents=True, exist_ok=True)
            ok, tex, pdf, msg = tailor_resume(
                job, self.base_tex, self.client,
                self.models["tailor"], jdir,
            )
            job.base_resume_name = self.base_resume_name
            if ok:
                job.tailored_tex, job.tailored_pdf_path = tex, pdf
                job.status = JobStatus.TAILORED
                # Always persist link + tailored resume now, so the user has it on
                # disk whether they auto-fill OR apply manually.
                path = reporter.save_result(
                    job, applied=False,
                    note="Tailored & saved. Use 'Auto-fill in browser' or apply manually "
                         "with this resume + the link above.",
                )
                out.append({"index": i, "result": "tailored OK; preview ready",
                            "saved_to": path})
            else:
                job.tailored_tex = tex
                job.status = JobStatus.TAILOR_FAILED
                job.status_reason = msg
                # Save link + the (non-compiling) .tex so nothing is lost.
                path = reporter.save_result(job, applied=False,
                                            note="Tailoring produced LaTeX that didn't compile; "
                                                 ".tex saved for manual fixup.")
                out.append({"index": i, "result": f"tailoring failed: {msg}", "saved_to": path})
        return json.dumps(out, indent=2)

    def dispatch(self, name: str, args: dict) -> str:
        if name == "list_base_resumes":
            return self._list_resumes()
        if name == "select_base_resume":
            return self._select_resume(args.get("name", ""))
        if name == "list_models":
            return self._list_models()
        if name == "set_model":
            return self._set_model(args.get("role", ""), args.get("model", ""))
        if name == "update_profile":
            return self._update_profile({k: v for k, v in args.items() if v is not None})
        if name == "add_jobs":
            return self._add_jobs(args.get("urls", []))
        if name == "inspect_requirements":
            return self._inspect_requirements(args.get("indices", []))
        if name == "answer_questions":
            return self._answer_questions(args.get("index", 0), args.get("answers", []))
        if name == "recall_knowledge":
            return self._recall_knowledge()
        if name == "list_jobs":
            return self._list_jobs()
        if name == "generate_cover_letter":
            return self._generate_cover_letter(args.get("indices", []))
        if name == "revise_resume":
            return self._revise_resume(args.get("index", 0), args.get("feedback", ""))
        if name == "tailor_jobs":
            return self._tailor_jobs(args.get("indices", []))
        return f"unknown tool: {name}"


def _assistant_content_to_params(content) -> list[dict]:
    params = []
    for b in content:
        if b.type == "text":
            params.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            params.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return params


def run_turn(ctx: AgentContext, history: list[dict], max_steps: int = 6) -> tuple[str, list[dict]]:
    """Run one user turn to completion (resolving any tool calls).

    `history` is the running message list (user/assistant dicts). Returns
    (assistant_text, updated_history).
    """
    model = ctx.models["chat"]
    final_text = ""

    for _ in range(max_steps):
        msg = ctx.client.messages.create(
            model=model, system=_system(), max_tokens=2000,
            tools=TOOLS, messages=history,
        )
        history.append({"role": "assistant", "content": _assistant_content_to_params(msg.content)})

        text = "".join(b.text for b in msg.content if b.type == "text")
        if text:
            final_text = text

        if msg.stop_reason != "tool_use":
            break

        results = []
        for b in msg.content:
            if b.type == "tool_use":
                results.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": ctx.dispatch(b.name, b.input),
                })
        history.append({"role": "user", "content": results})

    return final_text, history
