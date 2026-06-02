"""Minimal Gemini client for the smart-extraction scraper.

Reads GEMINI_API_KEY from the environment or a .env file. Free tier
(aistudio.google.com) is enough for the per-company extraction calls.
"""
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.0-flash"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass


def have_key() -> bool:
    _load_env()
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


@lru_cache(maxsize=1)
def _client():
    _load_env()
    from google import genai
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set (add it to .env)")
    return genai.Client(api_key=key)


def ask(prompt: str, model: str | None = None) -> str:
    _load_env()
    m = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    resp = _client().models.generate_content(model=m, contents=prompt)
    return resp.text or ""
