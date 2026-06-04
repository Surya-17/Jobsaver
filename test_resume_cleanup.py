"""Regression tests for resume output cleanup.

Reproduces two real leaks seen in generated PDFs (2026-06-03):
- Aegistech: leading commentary printed above the name.
- Anblicks: an unbalanced "</think>" reasoning remnant above the name.

Run: python -m pytest test_resume_cleanup.py   (or: python test_resume_cleanup.py)
"""
from resume import _extract_body
from tailor_client import _strip

BODY = (
    r"\begin{center}" "\n"
    r"{\Huge \textbf{Surya Kartheek Birudukota}}" "\n"
    r"\end{center}" "\n"
    r"\section{Experience}" "\n"
    r"\resumeSubHeadingListStart" "\n"
    r"\resumeSubHeadingListEnd"
)


def test_strip_unbalanced_think():
    # Anblicks: closing tag with no opening tag must still be removed.
    raw = ".ReadByte </think>\n" + BODY
    assert _strip(raw) == BODY


def test_strip_matched_think():
    raw = "<think>let me tailor this</think>\n" + BODY
    assert _strip(raw) == BODY


def test_strip_code_fence():
    raw = "```latex\n" + BODY + "\n```"
    assert _strip(raw) == BODY


def test_extract_body_leading_commentary():
    # Aegistech: prose + an em-dash separator before the resume.
    raw = ("Here's a tailored version of your resume focusing on the job "
           "requirements for an AI Engineer at AegisTech:\n—\n" + BODY)
    assert _extract_body(raw) == BODY


def test_extract_body_trailing_commentary():
    raw = BODY + "\n\nThis version positions you as a strong AI Engineer candidate."
    assert _extract_body(raw) == BODY


def test_extract_body_leading_and_trailing():
    raw = ("Sure! Here is the tailored body:\n" + BODY
           + "\n\nLet me know if you'd like any changes.")
    assert _extract_body(raw) == BODY


def test_clean_body_passthrough():
    assert _extract_body(BODY) == BODY


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")
