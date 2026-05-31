# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit chat app that tailors a user's LaTeX resume to each job and helps apply. The user chats to pick a base resume, set their profile, paste job links, inspect requirements, tailor/revise the resume, optionally write a cover letter, and auto-fill the application form. **US jobs only.** Submission is always **human-in-the-loop** (review-then-submit); the agent never auto-submits.

## Commands

```bash
# Setup
pip install -r requirements.txt
python -m playwright install chromium        # headed + headless browsers for form automation
brew install tectonic                        # LaTeX -> PDF compiler (must be on PATH)
cp .env.example .env                          # then add ANTHROPIC_API_KEY

# Run the app
streamlit run app.py

# There is no test framework. Sanity-check edits by compiling everything:
python -m py_compile app.py apply_runner.py inspect_runner.py src/*.py

# Compile a resume directly (debugging LaTeX)
tectonic base_resumes/<file>.tex
```

Smoke tests are written ad hoc as inline `python - <<'PY' ... PY` scripts that exercise pure functions (e.g. `applicator.assess_doability`, `reporter.save_result`, `job_fetcher._ashby_raw`). Prefer testing the **pure** modules without spending API credits; only the chat loop, resume tailoring, and JD structuring call the Anthropic API.

## Architecture — the big picture

- **Two-process design:** Streamlit runs the agent in-process, but all **Playwright** work runs in **separate subprocesses** (`apply_runner.py` headed = fill+submit; `inspect_runner.py` headless = read the form) because Playwright's sync API conflicts with Streamlit's event loop. `src/inspector.py` and `app.py::run_autofill()` launch them via `subprocess`, exchanging JSON files. The runners are the only code that imports Playwright.
- **Pure-logic vs Playwright split:** `src/applicator.py` is Playwright-free (ATS detection, field rules, `assess_doability`, `policy_blocked`) so it stays unit-testable; `src/pw_forms.py` holds the Playwright helpers used only by the runners.
- **Agent loop:** `src/agent.py` — `AgentContext` holds session state (jobs, selected resume, models, temp work_dir); `run_turn()` is the Claude tool-use loop; ~12 tools dispatched in `dispatch()`. Everything is chat-driven; the sidebar is status-only.
- **Externalized prompts:** all prompts live in `prompts/*.md`, loaded fresh each call via `src/prompts.py` (no caching) so edits apply without restart. Templates use `str.format`.
- **Doability is judged LIVE, not hardcoded:** the inspector loads the real form and `assess_doability()` returns `auto`/`manual`/`unknown`. `settings.yaml: do_not_automate` is an optional, empty-by-default override. The auto-fill browser uses a fresh logged-out context, so the user's accounts are never touched.
- **Self-updating knowledge:** `src/knowledge.py` writes per-ATS verdicts to `data/learned_ats.yaml`, only when new or changed.
- **Results & tracking (`src/reporter.py`):** writes `results/<company-slug>/resume_<slug>.{pdf,tex,md}` (a second role at the same company → `<slug>_2/`). Re-saves reuse the existing path (overwrite, no duplicates). Every save refreshes `results/INDEX.md` (+ `.index.json` ledger). `already_applied(company, title)` (ledger-backed) powers the duplicate guard. Tailoring auto-saves immediately so even un-fillable jobs leave a resume to apply manually.
- **Job fetching (`src/job_fetcher.py`):** ATS detected from host; Greenhouse/Lever/Ashby use public JSON APIs; embedded-Greenhouse career sites (`gh_jid`) are resolved to `job-boards.greenhouse.io`; otherwise scrape + Claude structuring. `looks_like_job_url()` rejects non-job links (feeds/notifications). `apply_url()` normalizes to the actual form (Lever `/apply`, Ashby `/application`).

## Configuration
- `config/settings.yaml` — `models`, `model_aliases`, `do_not_automate`, `us_jobs_only`.
- `config/profile.yaml` — personal/EEO/work-authorization info; deep-merged by `update_profile`.
- `.env` — `ANTHROPIC_API_KEY`. `base_resumes/*.tex` — base resumes (+ any `resume.cls`/`*.sty` they depend on).

## Invariants to preserve
- **Resume tailoring never fabricates**; keep the base resume's `\documentclass`/packages and one-page layout.
- **Never auto-submit** applications — the human reviews and submits.
- Keep `src/applicator.py` Playwright-free; browser code belongs in `pw_forms.py`/runners.

---

# Behavioral guidelines

Guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
