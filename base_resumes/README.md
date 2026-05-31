# Base resumes

Drop one or more **LaTeX resumes** (`.tex` files) in this folder.

When you launch the app, each `.tex` file here shows up in the sidebar dropdown
so you can pick which base resume to tailor for a given job.

Tips:
- Name them descriptively, e.g. `swe_backend.tex`, `data_scientist.tex`.
- The agent edits the *content* (bullet wording, ordering, emphasis, keywords)
  but preserves your layout/macros — so make sure each file compiles on its own
  with `tectonic <file>.tex` before using it.
- The agent will **never invent** experience, employers, titles, or dates.
