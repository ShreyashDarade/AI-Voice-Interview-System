"""
Optional LLM layer (text only).

* Off unless a provider is configured: ``ANTHROPIC_API_KEY`` | ``OPENAI_API_KEY`` | ``OLLAMA_MODEL``/``OLLAMA_HOST``
  (or an explicit ``LLM_PROVIDER``). ``LLM_ENABLED=false`` is a kill switch.
* **Never part of proctoring.** Video, audio-visual and integrity decisions are made by deterministic classical
  code; ``proctoring`` must not import this package (enforced by a test).
* Everything sent is redacted of direct identifiers, wrapped as data, and every response is parsed, validated and
  clipped. Callers always have a deterministic fallback.
"""
from .client import LLMClient, LLMError, LLMUnavailable, get_client  # noqa: F401
