"""Shared data structures used across the agent."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    """Lifecycle of a single job through the pipeline."""
    FETCHED = "fetched"          # JD pulled + parsed
    FETCH_FAILED = "fetch_failed"
    TAILORED = "tailored"        # resume tailored + compiled
    TAILOR_FAILED = "tailor_failed"
    APPLIED = "applied"          # result saved / submitted
    SKIPPED = "skipped"          # intentionally not applied (with reason)


@dataclass
class Job:
    """A single job posting and everything we derive from it."""
    url: str
    # --- parsed from the posting -------------------------------------------
    title: str = "Unknown role"
    company: str = "Unknown company"
    location: str = ""
    ats: str = "unknown"              # greenhouse | lever | ashby | linkedin | other
    description: str = ""             # full JD text
    is_us: Optional[bool] = None      # None = couldn't determine
    requires_login: bool = False      # account/login needed to apply
    # --- pipeline state -----------------------------------------------------
    status: JobStatus = JobStatus.FETCHED
    status_reason: str = ""
    # --- tailoring artifacts ------------------------------------------------
    tailored_tex: str = ""            # tailored LaTeX source
    tailored_pdf_path: str = ""       # path to compiled PDF (temp until saved)
    base_resume_name: str = ""        # which base resume was used
    cover_letter_path: str = ""       # optional generated cover letter (PDF)
    # --- confirmed answers to custom application questions ------------------
    # [{"question": str, "answer": str}] — drafted by the agent, confirmed by
    # the user in chat, then auto-filled into the form.
    custom_answers: list = field(default_factory=list)
    # --- result -------------------------------------------------------------
    result_path: str = ""             # final saved results/<Company>/<Job>.md

    def short(self) -> str:
        loc = f" — {self.location}" if self.location else ""
        return f"{self.title} @ {self.company}{loc}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d
