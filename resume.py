"""Resume tailoring: base LaTeX + JD + instructions -> tailored LaTeX -> PDF.

The LLM call goes through tailor_client (local Ollama by default). The LaTeX is
compiled by the engine set in RESUME_LATEX_ENGINE (pdflatex by default; tectonic
optional), dispatched in _run_latex so the engine can be swapped.
"""
import logging
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import tailor_client

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE = Path(__file__).parent
BASE_RESUME = BASE / "resume" / "base_resume.tex"
INSTRUCTIONS = BASE / "resume" / "instructions.md"
# Where tailored resumes are written. Override with RESUME_OUTPUT_DIR in .env.
OUTPUT_DIR = Path(os.environ.get("RESUME_OUTPUT_DIR") or (BASE / "output" / "resumes"))

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


_BEGIN = r"\begin{document}"
_END = r"\end{document}"


def _split_document(base_tex: str) -> tuple[str, str]:
    """Return (preamble_incl_begin, body). Preamble keeps \\documentclass, all
    packages, and custom \\newcommand definitions — the model never touches it."""
    b = base_tex.find(_BEGIN)
    e = base_tex.rfind(_END)
    if b == -1 or e == -1 or e < b:
        raise ResumeError(
            r"Base resume must contain \begin{document} and \end{document}.")
    preamble = base_tex[: b + len(_BEGIN)]
    body = base_tex[b + len(_BEGIN) : e]
    return preamble, body


def _extract_body(model_out: str) -> str:
    """The model is asked for a body fragment, but be defensive: if it wrapped
    the answer in a full document, keep only what's between the markers, and
    drop any prose the model emits around the resume. Two real failures seen:
    - leading commentary ("Here's a tailored version...") or a stray reasoning
      remnant ("...</think>") that then prints above the name;
    - trailing explanations ("This version positions you as...").
    The resume body is pure LaTeX, so every meaningful line starts with a
    command — we keep only from the first such line through the last."""
    out = model_out
    if _BEGIN in out:
        out = out.split(_BEGIN, 1)[1]
    if _END in out:
        out = out.split(_END, 1)[0]
    lines = out.strip().splitlines()
    first_cmd = next((i for i in range(len(lines))
                      if lines[i].lstrip().startswith("\\")), None)
    last_cmd = next((i for i in range(len(lines) - 1, -1, -1)
                     if lines[i].lstrip().startswith("\\")), None)
    if first_cmd is not None and last_cmd is not None:
        lines = lines[first_cmd : last_cmd + 1]
    return "\n".join(lines).strip()


def _build_prompt(base_body: str, jd: str, instructions: str) -> str:
    return (
        "You are an expert resume editor working in LaTeX.\n"
        "You are given the BODY of a resume (the content between "
        "\\begin{document} and \\end{document}), a target job description, and "
        "instructions. Produce a tailored version of the body.\n\n"
        "Rules:\n"
        "- Output ONLY the tailored body LaTeX — no commentary, no markdown fences.\n"
        "- Do NOT output \\documentclass, \\usepackage, \\newcommand, "
        "\\begin{document}, or \\end{document}. The preamble is fixed and added "
        "separately.\n"
        "- Use ONLY commands that already appear in the body below (e.g. "
        "\\resumeSubheading, \\resumeItem, \\faPhone). Do not invent new commands.\n"
        "- Do not fabricate any experience, skills, employers, or dates.\n"
        f"- {instructions}\n\n"
        "=== JOB DESCRIPTION ===\n"
        f"{jd[:12000]}\n\n"
        "=== RESUME BODY (LaTeX) ===\n"
        f"{base_body}\n\n"
        "=== TAILORED BODY (LaTeX only) ==="
    )


def generate_tailored_latex(jd: str, *, model: str | None = None) -> str:
    if not BASE_RESUME.exists():
        raise ResumeError(f"Base resume not found at {BASE_RESUME}. Add your resume/base_resume.tex.")
    base_tex = BASE_RESUME.read_text(encoding="utf-8")
    preamble, base_body = _split_document(base_tex)
    instructions = (INSTRUCTIONS.read_text(encoding="utf-8")
                    if INSTRUCTIONS.exists() else _DEFAULT_INSTRUCTIONS)
    try:
        raw = tailor_client.chat(_build_prompt(base_body, jd, instructions), model=model)
    except Exception as exc:
        raise ResumeError(f"LLM request failed: {exc}. Is Ollama running (ollama serve)?")
    body = _extract_body(raw)
    if not body:
        raise ResumeError("Model returned an empty resume body.", log=raw[:2000])
    return f"{preamble}\n{body}\n{_END}\n"


# Which LaTeX engine to compile with. Default pdflatex (MiKTeX/TeX Live);
# set RESUME_LATEX_ENGINE=tectonic to fall back to Tectonic/XeTeX.
_ENGINE = (os.environ.get("RESUME_LATEX_ENGINE") or "pdflatex").lower()


def _run_tectonic(tex_path: Path, out_dir: Path) -> Path:
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


def _run_pdflatex(tex_path: Path, out_dir: Path) -> Path:
    if shutil.which("pdflatex") is None:
        raise ResumeError("pdflatex not found on PATH (install MiKTeX or TeX Live, "
                          "or set RESUME_LATEX_ENGINE=tectonic).")
    cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
           f"-output-directory={out_dir}", str(tex_path)]
    proc = None
    try:
        # Two passes: the second settles \section rules / tabular spacing.
        for _ in range(2):
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                  cwd=str(out_dir))
    except subprocess.TimeoutExpired:
        raise ResumeError("LaTeX compile timed out after 120s.")
    pdf = out_dir / (tex_path.stem + ".pdf")
    if proc.returncode != 0 or not pdf.exists():
        log_file = out_dir / (tex_path.stem + ".log")
        log = (log_file.read_text(encoding="utf-8", errors="ignore")[-3000:]
               if log_file.exists() else (proc.stderr or proc.stdout))
        raise ResumeError("LaTeX compile failed.", log=log)
    return pdf


def _run_latex(tex_path: Path, out_dir: Path) -> Path:
    """ISOLATED engine call — dispatch by RESUME_LATEX_ENGINE."""
    return _run_tectonic(tex_path, out_dir) if _ENGINE == "tectonic" else _run_pdflatex(tex_path, out_dir)


def compile_pdf(tex_source: str, out_dir: Path, stem: str = "resume") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / f"{stem}.tex"
    tex_path.write_text(tex_source, encoding="utf-8")
    pdf = _run_latex(tex_path, out_dir)
    # Keep only the PDF — drop the .tex and pdflatex/tectonic scratch files.
    # (On failure _run_latex raises before this, leaving the .log for debugging.)
    for ext in (".tex", ".aux", ".log", ".out", ".fls", ".fdb_latexmk", ".synctex.gz"):
        scratch = out_dir / f"{stem}{ext}"
        if scratch != pdf and scratch.exists():
            scratch.unlink()
    return pdf


def _safe_filename(name: str) -> str:
    """Make a company name safe to use as a filename stem on Windows."""
    s = re.sub(r'[\\/:*?"<>|]+', "", name or "")  # strip forbidden chars
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return s or "resume"


def tailor_resume_for_job(job_id: int, jd: str, company_name: str = "resume",
                          *, model: str | None = None) -> Path:
    """Blocking. Generate tailored LaTeX and compile to PDF; return the PDF path.

    Saved as OUTPUT_DIR/<YYYY-MM-DD>/<Company>.pdf (a new folder per day)."""
    latex = generate_tailored_latex(jd, model=model)
    out_dir = OUTPUT_DIR / date.today().strftime("%Y-%m-%d")
    return compile_pdf(latex, out_dir, stem=_safe_filename(company_name))
