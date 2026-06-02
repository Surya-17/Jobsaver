"""Resume tailoring: base LaTeX + JD + instructions -> tailored LaTeX -> PDF.

The LLM call goes through tailor_client (local Ollama by default). The LaTeX is
compiled by Tectonic, isolated in _run_tectonic so the engine can be swapped.
"""
import logging
import shutil
import subprocess
from pathlib import Path

import tailor_client

logger = logging.getLogger(__name__)

BASE = Path(__file__).parent
BASE_RESUME = BASE / "resume" / "base_resume.tex"
INSTRUCTIONS = BASE / "resume" / "instructions.md"
OUTPUT_DIR = BASE / "output" / "resumes"

_DEFAULT_INSTRUCTIONS = (
    "Tailor the resume to the job description: reorder and reword bullet points to "
    "surface the most relevant experience and incorporate the job's key terminology. "
    "Do NOT invent experience, employers, dates, or skills the candidate doesn't have. "
    "Keep it to the same length (one page if the base is one page)."
)


class ResumeError(Exception):
    def __init__(self, message: str, log: str | None = None):
        super().__init__(message)
        self.log = log


def _build_prompt(base_tex: str, jd: str, instructions: str) -> str:
    return (
        "You are an expert resume editor working in LaTeX.\n"
        "Given a base resume (LaTeX), a target job description, and instructions, "
        "produce a tailored version.\n\n"
        "Rules:\n"
        "- Output ONLY a complete, compilable LaTeX document — no commentary, no markdown.\n"
        "- Preserve the original \\documentclass and all \\usepackage lines exactly.\n"
        "- Do not fabricate any experience, skills, employers, or dates.\n"
        f"- {instructions}\n\n"
        "=== JOB DESCRIPTION ===\n"
        f"{jd[:12000]}\n\n"
        "=== BASE RESUME (LaTeX) ===\n"
        f"{base_tex}\n\n"
        "=== TAILORED RESUME (LaTeX only) ==="
    )


def generate_tailored_latex(jd: str, *, model: str | None = None) -> str:
    if not BASE_RESUME.exists():
        raise ResumeError(f"Base resume not found at {BASE_RESUME}. Add your resume/base_resume.tex.")
    base_tex = BASE_RESUME.read_text(encoding="utf-8")
    instructions = (INSTRUCTIONS.read_text(encoding="utf-8")
                    if INSTRUCTIONS.exists() else _DEFAULT_INSTRUCTIONS)
    try:
        latex = tailor_client.chat(_build_prompt(base_tex, jd, instructions), model=model)
    except Exception as exc:
        raise ResumeError(f"LLM request failed: {exc}. Is Ollama running (ollama serve)?")
    if "\\documentclass" not in latex:
        raise ResumeError("Model did not return a valid LaTeX document.", log=latex[:2000])
    return latex


def _run_tectonic(tex_path: Path, out_dir: Path) -> Path:
    """ISOLATED engine call — swap this one function to change LaTeX engines."""
    if shutil.which("tectonic") is None:
        raise ResumeError("Tectonic is not installed or not on PATH "
                          "(install: scoop install tectonic).")
    try:
        proc = subprocess.run(
            ["tectonic", str(tex_path), "--outdir", str(out_dir), "--keep-logs"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise ResumeError("LaTeX compile timed out after 120s.")
    pdf = out_dir / (tex_path.stem + ".pdf")
    if proc.returncode != 0 or not pdf.exists():
        raise ResumeError("LaTeX compile failed.", log=(proc.stderr or proc.stdout))
    return pdf


def compile_pdf(tex_source: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / "resume.tex"
    tex_path.write_text(tex_source, encoding="utf-8")
    return _run_tectonic(tex_path, out_dir)


def tailor_resume_for_job(job_id: int, jd: str, *, model: str | None = None) -> Path:
    """Blocking. Generate tailored LaTeX and compile to PDF; return the PDF path."""
    latex = generate_tailored_latex(jd, model=model)
    return compile_pdf(latex, OUTPUT_DIR / str(job_id))
