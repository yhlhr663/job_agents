# 🧑‍💼 Job Application Agent

A chat-driven assistant that tailors your **LaTeX resume** to each job and helps you
apply. You paste job links → it fetches each posting, tells you what's there and what
the application asks for, tailors your resume (and optionally a cover letter), shows a
**PDF preview**, and either pre-fills the real application form in a visible browser or
saves everything so you can apply by hand. **US jobs only.** Submission is always
**human-in-the-loop** — you review and click submit; the agent never auto-submits.

---

## Table of contents

- [What it does (and doesn't)](#what-it-does-and-doesnt)
- [ATS support](#ats-support)
- [Setup](#setup)
- [Run](#run)
- [How it works — the whole logic](#how-it-works--the-whole-logic)
  - [The big picture: two-process design](#the-big-picture-two-process-design)
  - [End-to-end flow](#end-to-end-flow)
  - [Job lifecycle (status state machine)](#job-lifecycle-status-state-machine)
- [The agent's tools](#the-agents-tools)
- [Module reference](#module-reference)
- [Where results go](#where-results-go)
- [Configuration](#configuration)
- [Prompts](#prompts)
- [Invariants & guarantees](#invariants--guarantees)
- [Development & sanity-checks](#development--sanity-checks)
- [Project layout](#project-layout)

---

## What it does (and doesn't)

| Step | Status |
|------|--------|
| Fetch + summarize job descriptions | ✅ |
| Inspect what an application requires (documents, questions, login, CAPTCHA, multi-step) — judged **live** | ✅ |
| Tailor your LaTeX resume per job (no fabrication) + compile to PDF | ✅ |
| Auto-fix LaTeX that doesn't compile (feeds the error back to the model) | ✅ |
| Enforce a one-page limit (asks the model to trim, then recompiles) | ✅ |
| Revise the resume from your feedback ("emphasize Python", "shorten summary") | ✅ |
| Draft answers to free-text / essay application questions (you confirm them) | ✅ |
| Generate an **optional** tailored cover letter (PDF) | ✅ |
| Show a PDF preview and let you review before applying | ✅ |
| Auto-fill the form in a **visible** browser (you review & submit) | ✅ |
| Save link + resume per job (even when skipped) so you can apply manually | ✅ |
| Run-level **tracker** (`results/INDEX.md`) across all applications | ✅ |
| Warn if you've **already applied** to a company+role | ✅ |
| Learn over time which ATS are doable (`data/learned_ats.yaml`) | ✅ |
| **Auto-submit** an application | ❌ never — always your click |
| **Invent** experience, titles, dates, or metrics | ❌ never |

### ATS support

| Platform | Fetch JD | Inspect | Auto-fill |
|----------|:--:|:--:|:--:|
| Greenhouse (incl. embedded `gh_jid`, e.g. Pinterest careers) | ✅ | ✅ | ✅ |
| Lever | ✅ | ✅ | ✅ |
| Ashby (React; uses `/application` page) | ✅ | ✅ | ✅ |
| Generic company career pages | ✅ best-effort | ✅ | ✅ best-effort |
| LinkedIn Easy Apply | ⚠️ often blocked | — | ⏭️ skip (login/ToS) |
| Workday | — | — | ⏭️ skip (account required) |
| Avature (e.g. Intuit) | — | — | ⏭️ skip (multi-step/account) |
| Indeed | — | — | ⏭️ skip (login/anti-bot) |

Skipped sites are still logged with a reason; you can tailor a resume and apply manually.
Forms with a **CAPTCHA** are flagged — auto-fill still works, but you solve the CAPTCHA
and submit yourself. Doability is judged **live** on the real form, not hardcoded.

---

## Setup

```bash
# 1. Python deps
pip install -r requirements.txt

# 2. Browser for form auto-fill + LaTeX -> PDF compiler
python -m playwright install chromium
brew install tectonic            # macOS; see tectonic-typesetting.github.io otherwise

# 3. API key
cp .env.example .env             # then edit .env and paste your ANTHROPIC_API_KEY

# 4. Your details for resumes + application forms
cp config/profile.example.yaml config/profile.yaml   # then fill it in
#   (or just tell the agent "my email is …, phone …" and it writes this file)

# 5. Your resume(s): drop your .tex into base_resumes/ — plus any class it needs
#    (e.g. resume.cls) in the project root or base_resumes/.
```

`tectonic` must be on your `PATH` (LaTeX → PDF). Playwright's Chromium is used by the
two browser subprocesses. `config/profile.yaml` is gitignored and auto-seeded from the
example on first run.

## Run

```bash
streamlit run app.py
```

Everything is driven from the **chat** — the sidebar is a read-only status panel:

- **Pick a resume:** "use my backend resume" → agent lists/selects from `base_resumes/`.
- **Set up your info:** "my email is …, phone …, based in Austin TX, US citizen, no
  sponsorship needed" → agent saves it to `config/profile.yaml`.
- **Switch models:** "what models can I use?" / "use opus for tailoring",
  "switch chat to haiku" (aliases live in `settings.yaml`).
- **Add jobs:** paste one or more job links → agent summarizes each and asks which to apply to.
- **Inspect:** "what does job 2 need?" → opens the form headlessly and reports documents,
  questions, login/CAPTCHA, and which fields auto-fill from your profile.
- **Tailor:** "apply to 1 and 3" → tailors your resume, shows a **PDF preview**.
- **Apply:** click **🤖 Auto-fill in browser** (opens the real form, pre-fills it, you
  review & submit) or **📨 Save result only**.

---

## How it works — the whole logic

### The big picture: two-process design

Streamlit runs the agent **in-process**, but all **Playwright** work runs in **separate
subprocesses**, because Playwright's sync API conflicts with Streamlit's event loop.

```
┌──────────────────────────────────────────────────────────┐
│ Streamlit process (app.py)                                 │
│  • chat UI, PDF preview, Apply/Revise/Cover-letter buttons │
│  • AgentContext + run_turn()  → Claude tool-use loop       │
│  • job_fetcher (HTTP + Claude), resume_tailor (Claude),    │
│    cover_letter, reporter, knowledge  ← all in-process     │
└───────────────┬───────────────────────┬───────────────────┘
                │ subprocess + JSON      │ subprocess + JSON
                ▼                        ▼
   inspect_runner.py (headless)   apply_runner.py (headed)
   READ the form, report          PRE-FILL the form, you
   requirements + doability       review & submit yourself
                \                 /
                 └── src/pw_forms.py (the only Playwright code)
```

- **The two runners are the only code that imports Playwright.** They exchange JSON files
  with the parent: the parent writes a `payload.json`, the runner writes a result/status
  JSON back, and the parent reads it.
- **Pure-logic vs Playwright split:** `src/applicator.py` (ATS detection, apply-URL
  normalization, field→profile rules, `assess_doability`, `policy_blocked`) is
  Playwright-free so it stays unit-testable. `src/pw_forms.py` holds the Playwright
  helpers the runners share.
- **The auto-fill browser uses a fresh, logged-out context** — your real accounts and
  cookies are never touched.

### End-to-end flow

```
1. Pick base resume        select_base_resume → config.read_base_resume()
2. Set profile             update_profile → deep-merge into config/profile.yaml
3. Paste job links         add_jobs:
                             • looks_like_job_url()  reject feeds/searches early
                             • detect_ats()          host → greenhouse/lever/ashby/…
                             • resolve embedded gh_jid → job-boards.greenhouse.io
                             • fetch_job():  ATS JSON API  OR  scrape+Claude structuring
                             • us_jobs_only → mark non-US SKIPPED
                             • already_applied() duplicate warning
4. (optional) Inspect      inspect_requirements → inspect_runner.py (headless):
                             • load real form, enumerate fields + radio groups
                             • categorize() each field: profile-covered vs custom
                             • assess_doability() → auto | manual | unknown
                             • knowledge.record() the ATS verdict (if new/changed)
                             • returns essay_questions_to_draft + single-choice qs
5. (optional) Draft answers The agent drafts essay answers → you confirm →
                            answer_questions stores them on the Job (custom_answers)
6. Tailor                  tailor_jobs → resume_tailor.tailor_resume():
                             • Claude rewrites the .tex (no fabrication)
                             • compile_tex() via tectonic; on error feed log back (×2)
                             • enforce max_resume_pages: ask to trim, recompile
                             • auto-save immediately (reporter.save_result)
7. Review                  app.py renders a card per tailored job with PDF preview
8. (optional) Revise       "emphasize X" → revise_resume → recompile → re-save
9. (optional) Cover letter generate_cover_letter → compile → re-save
10. Apply                  • 🤖 Auto-fill: apply_runner.py (headed) opens the form,
                             attaches the PDF, fills text/select via field rules,
                             fills confirmed essay answers, then WAITS for you to
                             review & submit. Detects a confirmation page → APPLIED.
                           • 📨 Save result only: marks APPLIED, you submit manually.
```

Every save (tailor, revise, cover letter, apply) writes the per-job report and refreshes
`results/INDEX.md`. Re-saves reuse the same folder/basename and overwrite — no duplicates.

### Job lifecycle (status state machine)

A `Job` (see `src/models.py`) moves through these statuses:

```
            add_jobs
              │
   ┌──────────┼─────────────┐
   ▼          ▼             ▼
FETCHED   FETCH_FAILED   SKIPPED        (non-US, or on do_not_automate, or bad link)
   │
   │ tailor_jobs
   ▼
TAILORED ──────────────► TAILOR_FAILED  (LaTeX wouldn't compile; .tex still saved)
   │  (revise loops here)
   │ Auto-fill submit confirmed  /  Save result only
   ▼
APPLIED
```

Only `TAILORED` / `APPLIED` jobs get a card in the UI (they have a PDF + apply controls).
`FETCHED`, `SKIPPED`, and failed jobs live in the chat — ask **"list my jobs"** for the
full session overview.

---

## The agent's tools

`src/agent.py` runs a Claude tool-use loop (`run_turn`). Each user turn loops up to 6
steps, dispatching tool calls through `AgentContext.dispatch()`. The system prompt lives
in `prompts/agent_system.md` and is reloaded every turn (so prompt edits apply live).

| Tool | Args | What it does |
|------|------|--------------|
| `list_base_resumes` | — | List `.tex` files in `base_resumes/` and the currently selected one. |
| `select_base_resume` | `name` | Choose the base resume to tailor from (fuzzy/partial match, e.g. `backend` → `swe_backend.tex`). Must run before tailoring. |
| `add_jobs` | `urls[]` | Fetch + parse postings, store them, return per-job summaries (title, company, location, US?, status, `already_applied?`). Rejects non-job links and unreadable pages. |
| `inspect_requirements` | `indices[]` | Open each job's form (headless) and report documents, login/CAPTCHA/multi-step, a live `auto`/`manual`/`unknown` verdict, auto-fillable fields, essay questions to draft, and single-choice questions you answer yourself. |
| `answer_questions` | `index`, `answers[{question,answer}]` | Save **user-confirmed** answers to free-text/essay questions. These get auto-filled later. Not for single-choice questions (you answer those in the browser). |
| `revise_resume` | `index`, `feedback` | Revise an already-tailored resume from your feedback, recompile, re-save. |
| `tailor_jobs` | `indices[]` | Tailor the selected base resume to the chosen jobs, compile each to PDF, auto-save. |
| `generate_cover_letter` | `indices[]` | **Optional** — generate a tailored cover-letter PDF (job must already be tailored). |
| `list_jobs` | — | List every job in the session (number, title, company, status, saved path, has-cover-letter). |
| `recall_knowledge` | — | Recall what's been learned about ATS behavior (`data/learned_ats.yaml`). |
| `list_models` | — | Show available models + which are selected for chat / tailoring. |
| `set_model` | `role` (chat\|tailor\|both), `model` | Switch the model (alias `opus`/`sonnet`/`haiku` or full id). |
| `update_profile` | personal/links/work-auth/EEO/preferences fields | Deep-merge details into `config/profile.yaml`; existing values preserved. |

**Note:** Auto-fill and "Save result only" are **UI buttons**, not agent tools — that's
the human-in-the-loop boundary. The agent fetches, inspects, tailors, drafts, and tracks;
the human reviews and submits.

---

## Module reference

| File | Role |
|------|------|
| `app.py` | Streamlit chat UI: replays history, renders tailored-job cards (PDF preview, Auto-fill, Save, Revise, Cover letter, Download), launches `apply_runner.py`. |
| `src/agent.py` | `AgentContext` (session state: jobs, selected resume, models, temp `work_dir`) + the ~13 tools + `run_turn()` tool-use loop. |
| `src/job_fetcher.py` | URL → structured `Job`. `detect_ats()` from host; Greenhouse/Lever/Ashby via public JSON APIs; embedded-Greenhouse (`gh_jid`) resolved to `job-boards.greenhouse.io`; else scrape HTML → text → Claude structuring. `looks_like_job_url()` rejects non-job links. |
| `src/resume_tailor.py` | Claude tailoring + revising. `_generate_and_compile()` compiles, feeds compile errors back to the model to self-correct (×2), and enforces the page limit by asking for a trim then recompiling. |
| `src/cover_letter.py` | Optional cover letter: Claude writes the body, `_wrap()` puts it in a minimal LaTeX article (escaping LaTeX specials), compiles to PDF. |
| `src/applicator.py` | **Playwright-free** pure logic: `policy_blocked`, `assess_doability` (login→manual, 0 fields→unknown, multi-step→manual, else auto), `apply_url` normalization (Lever `/apply`, Ashby `/application`), `build_field_rules` (label→profile value) + `match_rule`, `categorize` (field→category). |
| `src/pw_forms.py` | Shared Playwright helpers for the runners: `field_key`, `enumerate_fields`, `enumerate_radio_groups`, `list_file_inputs`, `has_captcha`, `detect_multistep`, `detect_login_wall`, `fill_text`, `fill_select`. |
| `inspect_runner.py` | Headless subprocess: reads a form, categorizes fields, returns the doability verdict + requirements. |
| `apply_runner.py` | Headed subprocess: attaches the resume PDF, fills profile fields + confirmed essay answers, then waits for you to review & submit (watches for a confirmation page). |
| `src/inspector.py` | Thin launcher for `inspect_runner.py` (subprocess + JSON). |
| `src/reporter.py` | Writes `results/<slug>/resume_<slug>.{pdf,tex,md}`, refreshes `INDEX.md` + `.index.json` ledger, powers `already_applied()`. |
| `src/knowledge.py` | Self-updating ATS memory in `data/learned_ats.yaml` — records a verdict only when new or changed. |
| `src/latex_compiler.py` | `compile_tex()` via tectonic (copies any `.cls`/`.sty` into the build dir), `page_count()`, `tectonic_available()`. |
| `src/config.py` | Loads `settings.yaml` / `profile.yaml` / `.env`; `update_profile` deep-merge; resume + results dir helpers. |
| `src/models.py` | `Job` dataclass + `JobStatus` enum. |
| `src/prompts.py` | Loads `prompts/*.md` fresh each call (no caching). |

---

## Where results go

```
results/
  <company-slug>/                  # e.g. speak/  (a 2nd role there -> speak_2/)
    resume_<company-slug>.pdf      # tailored resume
    resume_<company-slug>.tex      # tailored LaTeX source
    resume_<company-slug>.md       # report: status, reason, links, captured JD
    resume_<company-slug>_cover_letter.pdf   # only if you generated one
  INDEX.md                         # human-readable tracker across all applications
  .index.json                      # ledger behind INDEX.md (powers already_applied)
```

Re-tailoring / revising / applying overwrites the same files (no duplicates).
`results/` is gitignored, so it stays empty on GitHub.

---

## Configuration

- **`config/settings.yaml`** — `models` (default chat + tailor), `model_aliases`
  (opus/sonnet/haiku → full ids), `us_jobs_only`, `max_resume_pages` (tailor loop trims
  to fit), `max_job_description_chars` (cost cap), `results_dir`, `base_resumes_dir`, and
  `do_not_automate` (an **optional, empty-by-default** force-skip list of ATS keys —
  everything else is judged live).
- **`config/profile.yaml`** — your personal info / links / work-authorization / EEO /
  preferences. Deep-merged by `update_profile`; gitignored; auto-seeded from
  `config/profile.example.yaml`.
- **`.env`** — `ANTHROPIC_API_KEY`.
- **`base_resumes/*.tex`** — your base resumes (+ any `resume.cls` / `*.sty` they need,
  in the project root or `base_resumes/`).
- **`data/learned_ats.yaml`** — written by the agent; what it's learned about each ATS.

## Prompts

All model prompts live in `prompts/*.md` and are loaded **fresh each call** (no caching),
so edits apply without restarting:

| Prompt | Used by |
|--------|---------|
| `agent_system.md` | The chat agent's system prompt. |
| `extract_job.md` | Structuring a scraped page into title/company/location/US?/login?. |
| `tailor_system.md` / `tailor_user.md` | Resume tailoring. |
| `tailor_fix.md` | Feeding a compile error back to fix the LaTeX. |
| `tailor_shorten.md` | Trimming a resume that exceeds the page limit. |
| `tailor_revise.md` | Applying your feedback to an already-tailored resume. |
| `cover_letter.md` | Drafting the cover-letter body. |

Templates use `str.format`, so keep their `{placeholders}` intact when editing.

---

## Invariants & guarantees

- **Never auto-submits** — the human reviews and submits every application.
- **Resume tailoring never fabricates** — it only reorders, rephrases, and emphasizes
  what's already true in your base resume, and keeps the base's `\documentclass`,
  packages, and one-page layout.
- **`src/applicator.py` stays Playwright-free** — browser code belongs in `pw_forms.py`
  and the runners.
- **The auto-fill browser is logged-out** — your accounts/cookies are never used.
- **US jobs only** — non-US postings are skipped with a logged reason.

---

## Development & sanity-checks

There is no test framework. Sanity-check edits by compiling everything:

```bash
python -m py_compile app.py apply_runner.py inspect_runner.py src/*.py
```

Smoke-test the **pure** modules (no API credits) with ad-hoc inline scripts, e.g.:

```bash
python - <<'PY'
from src.applicator import assess_doability
print(assess_doability(requires_login=False, num_fields=8, has_captcha=False, multistep=False))
# -> ('auto', 'Standard application form detected — I can pre-fill it; you review and click submit.')
PY
```

Compile a resume directly when debugging LaTeX: `tectonic base_resumes/<file>.tex`.

## Project layout

```
app.py                 Streamlit chat UI (preview, auto-fill, revise, cover letter)
apply_runner.py        Playwright subprocess: fill the form, you review & submit (headed)
inspect_runner.py      Playwright subprocess: read what a form requires (headless)
src/agent.py           Claude tool-use loop (resume/model/profile/jobs/inspect tools)
src/job_fetcher.py     URL -> structured job (ATS APIs + scrape + Claude parse)
src/resume_tailor.py   Claude tailoring/revising + compile + one-page enforcement
src/cover_letter.py    optional cover-letter generation
src/applicator.py      pure logic: ATS rules, apply-URL, field rules, live doability
src/pw_forms.py        Playwright form helpers (used by the runners)
src/inspector.py       launcher for the headless inspect runner
src/reporter.py        writes results/<slug>/resume_<slug>.* + INDEX.md tracker
src/knowledge.py       learns which ATS are doable (data/learned_ats.yaml)
src/latex_compiler.py  tectonic wrapper (compile + page count + class-asset copy)
src/{config,models,prompts}.py   settings/profile/env, Job dataclass, prompt loader
prompts/*.md           all model prompts (editable, no restart needed)
config/                settings.yaml + profile.yaml (+ examples)
base_resumes/          your .tex resumes (+ resume.cls / *.sty)
results/               tailored resumes + reports + tracker (gitignored)
data/learned_ats.yaml  agent's learned ATS knowledge
```
