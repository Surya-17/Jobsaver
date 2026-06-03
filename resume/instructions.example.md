# Resume Tailoring Instructions (example)

Copy this file to `resume/instructions.md` and edit it for your own resume.
Everything here is injected **verbatim** into the tailoring prompt, so keep it
clear and imperative. `resume/instructions.md` is gitignored, so your personal
details stay on your machine.

Note: the tailoring code only sends the resume **body** to the model (your
LaTeX preamble and custom macros are preserved automatically), so you don't need
rules about `\documentclass` or `\usepackage`.

---

## Core rules

1. Keep the resume to one page.
2. Keep the same overall content density and length — if you add something, trim something of equal length.
3. Do not invent companies, degrees, employment, dates, metrics, or production claims.
4. It is okay to reframe real experience to match the JD, but keep it believable and defensible.
5. Make bullets specific, engineering-focused, ATS-friendly, and credible.
6. Avoid generic phrases ("worked on", "helped with", "responsible for").
7. Prefer strong verbs: built, designed, developed, implemented, integrated, evaluated, automated, optimized.
8. Order sections by relevance (e.g. Experience, Projects, Technical Skills, Education).
9. Update the Technical Skills section to match the JD's language without overclaiming.
10. Make only surgical edits to experience bullets — change what strengthens relevance, no more.
11. Do not change project names; project bullets may be rewritten, but names stay locked.

---

## Locked details (never change) — fill in your own

| Field | Value |
|---|---|
| Name | YOUR NAME |
| Email | your.email@example.com |
| Phone | (000) 000-0000 |
| Location | City, ST |
| Current/most recent title | Your Title |
| Dates | Mon YYYY — Mon YYYY |
| Education | Your Degree, Your School (Mon YYYY — Mon YYYY) |
| Project names | Project A, Project B |

---

## JD category routing — emphasize different skills per JD type

| JD type | Emphasize |
|---|---|
| AI/LLM/RAG/Agentic | LLM APIs, RAG, prompt engineering, structured outputs, evaluation, guardrails, vector search, Docker, cloud |
| ML-heavy | scikit-learn, XGBoost, PyTorch, feature engineering, model evaluation, MLflow, pipelines, monitoring |
| Backend/platform | Python, FastAPI/Flask, REST APIs, Docker, CI/CD, SQL, observability, logging |
| Client-facing/consulting | communication, stakeholder demos, documentation, cross-functional collaboration |
| Regulated (health/finance/legal) | responsible AI, auditability, human-in-the-loop review, monitoring, reliability |
