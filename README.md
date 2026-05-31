# 🧑‍💼 Job Application Agent

Chat-driven assistant that tailors your **LaTeX resume** to each job and helps you
apply. Paste job links → it fetches each posting, tells you what's there, tailors
your resume, shows a PDF preview, and you click **Apply** to save the result.
**US jobs only.**

## What it does (and doesn't)

| Step | Status |
|------|--------|
| Fetch + summarize job descriptions | ✅ |
| Inspect what an application requires (documents, questions, login, CAPTCHA) — judged **live** | ✅ |
| Tailor your LaTeX resume per job (no fabrication) + compile to PDF | ✅ |
| Revise the resume from your feedback ("emphasize Python", "shorten summary") | ✅ |
| Generate an **optional** tailored cover letter (PDF) | ✅ |
| Show PDF preview, let you review before applying | ✅ |
| Auto-fill the form in a visible browser (you review & submit) | ✅ |
| Save link + resume per job (even when skipped) so you can apply manually | ✅ |
| Run-level **tracker** (`results/INDEX.md`) across all applications | ✅ |
| Warn if you've **already applied** to a company+role | ✅ |
| Learn over time which ATS are doable (`data/learned_ats.yaml`) | ✅ |

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

Skipped sites are still logged with a reason; you can tailor a resume and apply manually. Forms with a **CAPTCHA** are flagged — auto-fill still works, but you solve the CAPTCHA and submit yourself.

> The agent **never invents** experience, titles, dates, or metrics — it only
> reorders, rephrases, and emphasizes what's already true in your base resume.

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
#    A sample_resume.tex is included so you can try it immediately.
```

## Run

```bash
streamlit run app.py
```

Everything is driven from the **chat** (the sidebar is just a status panel):

- **Pick a resume:** "use my backend resume" → agent lists/selects from `base_resumes/`.
- **Set up your info:** "my email is …, phone …, based in Austin TX, US citizen, no
  sponsorship needed" → agent saves it to `config/profile.yaml`.
- **Switch models:** "what models can I use?" / "use opus for tailoring",
  "switch chat to haiku" (aliases live in `settings.yaml`).
- **Add jobs:** paste one or more job links → agent summarizes each and asks which to apply to.
- **Tailor:** "apply to 1 and 3" → tailors your resume, shows a **PDF preview**.
- **Apply:** click **🤖 Auto-fill in browser** (opens the real form, pre-fills it, you
  review & submit) or **📨 Save result only**.

## Where results go

```
results/
  <company-slug>/                  # e.g. speak/  (a 2nd role there -> speak_2/)
    resume_<company-slug>.pdf      # tailored resume
    resume_<company-slug>.tex      # tailored LaTeX source
    resume_<company-slug>.md       # report: status, reason, links, JD
    resume_<company-slug>_cover_letter.pdf   # only if you generated one
  INDEX.md                         # tracker across all applications
```

Re-tailoring / revising overwrites the same files (no duplicates). `results/` is
gitignored, so it stays empty on GitHub.

## Configuration

- `config/settings.yaml` — models + aliases, `us_jobs_only`, `max_resume_pages`,
  `do_not_automate` (optional force-skip list, empty by default).
- `config/profile.yaml` — your personal info / EEO / work-authorization answers
  (gitignored; copy from `config/profile.example.yaml`).

## Project layout

```
app.py                 Streamlit chat UI (preview, auto-fill, revise, cover letter)
apply_runner.py        Playwright subprocess: fill the form, you review & submit
inspect_runner.py      Playwright subprocess: read what a form requires (headless)
src/agent.py           Claude tool-use loop (resume/model/profile/jobs tools)
src/job_fetcher.py     URL -> structured job (ATS APIs + scrape + Claude parse)
src/resume_tailor.py   Claude tailoring/revising + compile + one-page enforcement
src/cover_letter.py    optional cover-letter generation
src/applicator.py      pure logic: ATS rules, live doability verdict
src/pw_forms.py        Playwright form helpers (used by the runners)
src/reporter.py        writes results/<slug>/resume_<slug>.* + INDEX.md tracker
src/knowledge.py       learns which ATS are doable (data/learned_ats.yaml)
src/{config,models,prompts}.py
prompts/*.md           all model prompts (editable, no restart needed)
```
