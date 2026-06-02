"""Streamlit chat frontend for the job-application agent.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st
from anthropic import Anthropic

from src import config, cover_letter, resume_tailor
from src.agent import AgentContext, run_turn
from src.applicator import policy_blocked
from src.models import Job, JobStatus
from src.reporter import save_result

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="Job Application Agent", page_icon="🧑‍💼", layout="wide")

STATUS_BADGE = {
    JobStatus.FETCHED: "🆕 fetched",
    JobStatus.FETCH_FAILED: "❌ fetch failed",
    JobStatus.TAILORED: "✅ tailored",
    JobStatus.TAILOR_FAILED: "⚠️ tailor failed",
    JobStatus.SKIPPED: "⏭️ skipped",
    JobStatus.APPLIED: "📨 applied",
}


# --------------------------------------------------------------------------- #
# Session setup
# --------------------------------------------------------------------------- #
def get_client() -> Anthropic | None:
    key = config.anthropic_api_key()
    return Anthropic(api_key=key) if key else None


def ensure_state():
    st.session_state.setdefault("history", [])   # raw agent messages
    st.session_state.setdefault("display", [])    # [(role, text)] for the chat UI
    st.session_state.setdefault("ctx", None)


def pdf_iframe(pdf_path: str, height: int = 520):
    data = Path(pdf_path).read_bytes()
    b64 = base64.b64encode(data).decode()
    st.markdown(
        f'<iframe src="data:application/pdf;base64,{b64}" '
        f'width="100%" height="{height}" style="border:1px solid #ddd;border-radius:6px;">'
        f"</iframe>",
        unsafe_allow_html=True,
    )


def run_autofill(job: Job):
    """Launch the Playwright subprocess (visible browser) to pre-fill the form.

    Blocks (with a spinner) while you review & submit in the browser, then reads
    the status file the subprocess wrote and updates the job.
    """
    tmp = Path(tempfile.mkdtemp(prefix="apply_"))
    status_out = tmp / "status.json"
    payload = {
        "job": job.to_dict(),
        "profile": config.load_profile(),
        "custom_answers": job.custom_answers,
        "status_out": str(status_out),
    }
    payload_path = tmp / "payload.json"
    payload_path.write_text(json.dumps(payload))

    proc = subprocess.run(
        [sys.executable, str(ROOT / "apply_runner.py"), str(payload_path)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if status_out.exists():
        status = json.loads(status_out.read_text())
        job.result_path = status.get("saved_path", "")
        job.status = JobStatus.APPLIED if status.get("submitted") else JobStatus.TAILORED
        return status, proc
    return {"message": "No status returned (the browser may have failed to open).",
            "submitted": False}, proc


def do_revise(ctx: AgentContext, job: Job, feedback: str) -> tuple[bool, str]:
    """Apply the user's typed feedback to the tailored resume + recompile, then
    re-save (overwriting the existing PDF/tex/report in results/)."""
    jdir = ctx.work_dir / f"ui_revise_{id(job)}"
    jdir.mkdir(parents=True, exist_ok=True)
    try:
        ok, tex, pdf, msg = resume_tailor.revise_resume(
            job.tailored_tex, feedback, ctx.client, ctx.models["tailor"], jdir)
    except Exception as e:  # noqa: BLE001 — surface API/compile errors to the UI
        return False, f"{type(e).__name__}: {e}"
    if ok:
        job.tailored_tex, job.tailored_pdf_path = tex, pdf
        save_result(job, applied=job.status == JobStatus.APPLIED,
                    note=f"Revised per feedback: {feedback[:120]}")
    return ok, msg


def do_cover_letter(ctx: AgentContext, job: Job) -> tuple[bool, str]:
    """Generate an optional cover-letter PDF + re-save."""
    jdir = ctx.work_dir / f"ui_cover_{id(job)}"
    jdir.mkdir(parents=True, exist_ok=True)
    try:
        ok, _text, pdf, msg = cover_letter.generate_cover_letter(
            job, config.load_profile(), job.tailored_tex, ctx.client, ctx.models["tailor"], jdir)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if ok:
        job.cover_letter_path = pdf
        save_result(job, applied=job.status == JobStatus.APPLIED, note="Cover letter generated.")
    return ok, msg


# --------------------------------------------------------------------------- #
# Sidebar — config + resume picker
# --------------------------------------------------------------------------- #
def sidebar(ctx: AgentContext | None):
    """Read-only status panel. Everything is driven from the chat."""
    st.sidebar.header("⚙️ Status")

    if config.anthropic_api_key():
        st.sidebar.success("ANTHROPIC_API_KEY detected")
    else:
        st.sidebar.error("No ANTHROPIC_API_KEY — add it to .env and restart.")

    resumes = config.list_base_resumes()
    if not resumes:
        st.sidebar.warning(
            f"No resumes found. Drop a `.tex` file into "
            f"`{config.base_resumes_dir().name}/`, then say so in chat."
        )
    else:
        selected = ctx.base_resume_name if ctx and ctx.base_resume_name else None
        st.sidebar.markdown("📄 **Base resume**")
        st.sidebar.caption(
            f"Selected: **{selected}**" if selected
            else "None yet — tell me which in chat."
        )
        st.sidebar.caption("Available: " + ", ".join(resumes))

    prof = config.load_profile()
    have_profile = bool(prof.get("full_name") and prof.get("email"))
    st.sidebar.markdown("👤 **Profile**")
    if have_profile:
        st.sidebar.caption(f"{prof.get('full_name')} · {prof.get('email')}")
    else:
        st.sidebar.caption("Incomplete — share your info in chat to fill it in.")

    st.sidebar.divider()
    index_md = config.results_dir() / "INDEX.md"
    if index_md.exists():
        with st.sidebar.expander("📊 Application tracker"):
            st.markdown(index_md.read_text())

    models = ctx.models if ctx else config.settings()["models"]
    st.sidebar.caption(f"💬 Chat model: {models['chat']}")
    st.sidebar.caption(f"✍️ Tailor model: {models['tailor']}")
    st.sidebar.caption("Switch resume / model / profile by just asking in chat.")
    if st.sidebar.button("🔄 Reset conversation"):
        for k in ("history", "display", "ctx"):
            st.session_state[k] = [] if k != "ctx" else None
        st.rerun()


# --------------------------------------------------------------------------- #
# Job cards (preview + Apply)
# --------------------------------------------------------------------------- #
def render_jobs(ctx: AgentContext):
    # Only show cards for jobs that have been tailored (they have a preview + the
    # apply/auto-fill/revise controls). Fetched-but-not-tailored and skipped jobs
    # live in the chat; ask "list my jobs" for the full valid list.
    if not ctx:
        return
    tailored = [(i, j) for i, j in enumerate(ctx.jobs, start=1)
                if j.status in (JobStatus.TAILORED, JobStatus.APPLIED)]
    if not tailored:
        return
    st.subheader("📄 Tailored resumes — review & apply")
    for idx, job in tailored:
        badge = STATUS_BADGE.get(job.status, job.status.value)
        with st.expander(f"{idx}. {job.short()}  —  {badge}", expanded=job.status == JobStatus.TAILORED):
            cols = st.columns([2, 3])
            with cols[0]:
                st.write(f"**Company:** {job.company}")
                st.write(f"**Location:** {job.location or 'n/a'}")
                st.write(f"**US:** {job.is_us}")
                st.write(f"**ATS:** {job.ats}")
                if job.requires_login:
                    st.warning("Applying needs login/account — submit manually.")
                if job.status_reason:
                    st.caption(job.status_reason)
                st.markdown(f"[🔗 Open job posting]({job.url})")

                if job.status in (JobStatus.TAILORED, JobStatus.APPLIED) and job.tailored_pdf_path:
                    if job.result_path:
                        saved_pdf = Path(job.result_path).with_suffix(".pdf")
                        st.success(f"Saved → {saved_pdf}")
                        if saved_pdf.exists():
                            # Serve over http (Chrome blocks file:// links from a web page).
                            static_dir = ROOT / "static"
                            static_dir.mkdir(exist_ok=True)
                            static_name = f"{saved_pdf.parent.name}.pdf"   # e.g. speak.pdf / speak_2.pdf
                            shutil.copy2(saved_pdf, static_dir / static_name)
                            st.markdown(
                                f'<a href="app/static/{static_name}" target="_blank" '
                                f'rel="noopener">🔗 Open saved resume in browser</a>',
                                unsafe_allow_html=True)

                    if job.custom_answers:
                        with st.expander(f"📝 {len(job.custom_answers)} answer(s) ready to auto-fill"):
                            for qa in job.custom_answers:
                                st.markdown(f"**{qa.get('question','')}**")
                                st.caption(qa.get("answer", ""))

                    blocked, block_reason = policy_blocked(job.ats, config.do_not_automate())
                    if not blocked:
                        if st.button("🤖 Auto-fill in browser", key=f"auto_{idx}",
                                     help="Opens the application page, checks if it's doable, "
                                          "pre-fills it, and waits for you to review & submit."):
                            with st.spinner("Browser open — review & submit there, then close it…"):
                                status, proc = run_autofill(job)
                            if status.get("submitted"):
                                st.success(status.get("message", "Submitted."))
                            else:
                                st.info(status.get("message", "Filled — not confirmed submitted."))
                            st.rerun()
                    else:
                        st.caption(f"Auto-fill off (your settings): {block_reason}")

                    if not job.result_path and st.button("📨 Save result only", key=f"apply_{idx}"):
                        path = save_result(job, applied=True,
                                           note="Tailored resume saved; submit on the job page.")
                        job.status = JobStatus.APPLIED
                        st.success(f"Saved → {path}")
                        st.rerun()
                    dl_name = (Path(job.result_path).with_suffix(".pdf").name
                               if job.result_path else "resume.pdf")
                    st.download_button(
                        "⬇️ Download resume PDF",
                        data=Path(job.tailored_pdf_path).read_bytes(),
                        file_name=dl_name,
                        key=f"dl_{idx}",
                    )

                    # --- optional cover letter ---------------------------------
                    if job.cover_letter_path and Path(job.cover_letter_path).exists():
                        st.download_button(
                            "⬇️ Download cover letter",
                            data=Path(job.cover_letter_path).read_bytes(),
                            file_name=dl_name.replace(".pdf", "_cover_letter.pdf"),
                            key=f"cldl_{idx}",
                        )
                    elif st.button("✍️ Generate cover letter (optional)", key=f"cl_{idx}"):
                        with st.spinner("Writing cover letter…"):
                            ok, msg = do_cover_letter(ctx, job)
                        st.success("Cover letter ready.") if ok else st.error(msg)
                        st.rerun()

                    # --- resume feedback / revise (no nested expander!) --------
                    st.markdown("**🔁 Revise resume with feedback**")
                    fb = st.text_area(
                        "What should change?",
                        placeholder="e.g. emphasize Python & distributed systems; "
                                    "shorten the summary; move education below experience.",
                        key=f"fb_{idx}", label_visibility="collapsed")
                    if st.button("Apply feedback", key=f"rev_{idx}"):
                        if not fb.strip():
                            st.warning("Type what you'd like changed first.")
                        else:
                            with st.spinner("Revising & recompiling…"):
                                ok, msg = do_revise(ctx, job, fb.strip())
                            if ok:
                                st.success("Revised — resume & PDF updated.")
                                st.rerun()
                            else:
                                st.error(f"Couldn't revise: {msg}")
            with cols[1]:
                if job.tailored_pdf_path:
                    pdf_iframe(job.tailored_pdf_path)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ensure_state()
    st.title("🧑‍💼 Job Application Agent")
    st.caption("Chat to set your resume/model/profile, paste job links, and tailor & "
               "apply. The agent fetches each posting and lets you review before applying. "
               "US jobs only.")

    sidebar(st.session_state.ctx)
    client = get_client()

    # Replay chat history.
    for role, text in st.session_state.display:
        with st.chat_message(role):
            st.markdown(text)

    if st.session_state.ctx:
        render_jobs(st.session_state.ctx)

    prompt = st.chat_input("Paste job links, share your info, or tell me what to do…")
    if not prompt:
        return

    if client is None:
        st.error("Set ANTHROPIC_API_KEY in .env and restart.")
        return

    # The agent manages resume/model/profile selection through chat tools.
    if st.session_state.ctx is None:
        st.session_state.ctx = AgentContext(client)
    ctx = st.session_state.ctx
    st.session_state.display.append(("user", prompt))
    st.session_state.history.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        with st.spinner("Working…"):
            reply, st.session_state.history = run_turn(ctx, st.session_state.history)
        st.markdown(reply or "_(done)_")
    st.session_state.display.append(("assistant", reply or "_(done)_"))
    st.rerun()


if __name__ == "__main__":
    main()
