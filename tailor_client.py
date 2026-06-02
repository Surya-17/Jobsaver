"""Provider-agnostic chat client for resume tailoring.

Uses the OpenAI-compatible Chat Completions API so the backend can be local
Ollama (default), Groq, OpenRouter, etc. — switched entirely via .env:

    TAILOR_BASE_URL=http://localhost:11434/v1   # Ollama
    TAILOR_API_KEY=ollama                        # dummy for local
    TAILOR_MODEL=qwen3:8b

Kept separate from scrapers/llm.py (Gemini) so scraping and tailoring never
share a provider or quota.
"""
import os
import re
from functools import lru_cache


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass


@lru_cache(maxsize=1)
def _client():
    _load_env()
    from openai import OpenAI
    return OpenAI(
        base_url=os.environ.get("TAILOR_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("TAILOR_API_KEY", "ollama"),
    )


def _strip(text: str) -> str:
    """Remove Qwen3-style <think> traces and ```` ```latex ```` fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if "```" in text:
        # Keep the content of the first fenced block if present.
        m = re.search(r"```(?:latex|tex)?\s*(.*?)```", text, flags=re.S)
        if m:
            return m.group(1).strip()
    return text


def chat(prompt: str, *, model: str | None = None) -> str:
    """Blocking. Send a single-prompt chat completion; return cleaned text."""
    _load_env()
    m = model or os.environ.get("TAILOR_MODEL", "qwen3:8b")
    resp = _client().chat.completions.create(
        model=m,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return _strip(resp.choices[0].message.content or "")
