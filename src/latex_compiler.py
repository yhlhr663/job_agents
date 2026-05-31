"""Compile LaTeX source to a PDF using Tectonic."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config


class LatexNotInstalled(RuntimeError):
    pass


def tectonic_available() -> bool:
    return shutil.which("tectonic") is not None


def _copy_class_assets(work: Path):
    """Copy any custom .cls/.sty (e.g. resume.cls) the base resume needs into the
    build dir, so tailored resumes compile with the user's real class/format."""
    for src in (config.base_resumes_dir(), config.ROOT):
        for pat in ("*.cls", "*.sty"):
            for f in Path(src).glob(pat):
                dst = work / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)


def page_count(pdf_path: str) -> int:
    """Best-effort PDF page count (no extra deps)."""
    data = Path(pdf_path).read_bytes()
    n = len(re.findall(rb"/Type\s*/Page[^s]", data))
    return n or 1


def compile_tex(tex_source: str, out_dir: Path | None = None,
                basename: str = "resume") -> tuple[bool, str, str]:
    """Compile `tex_source` to a PDF.

    Returns (ok, pdf_path, log). On failure, pdf_path is "" and log holds the
    tectonic error output so a caller can feed it back to the model for a fix.
    """
    if not tectonic_available():
        raise LatexNotInstalled(
            "tectonic not found on PATH. Install with: brew install tectonic"
        )

    work = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="resume_"))
    work.mkdir(parents=True, exist_ok=True)
    tex_path = work / f"{basename}.tex"
    tex_path.write_text(tex_source, encoding="utf-8")
    _copy_class_assets(work)

    proc = subprocess.run(
        ["tectonic", "--keep-logs", "--print", str(tex_path)],
        cwd=str(work),
        capture_output=True,
        text=True,
        timeout=120,
    )

    pdf_path = work / f"{basename}.pdf"
    if proc.returncode == 0 and pdf_path.exists():
        return True, str(pdf_path), proc.stderr

    # Tectonic writes the useful diagnostics to stderr.
    log = (proc.stderr or "") + "\n" + (proc.stdout or "")
    return False, "", log.strip()
