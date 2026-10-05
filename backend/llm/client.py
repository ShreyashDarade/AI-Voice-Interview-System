from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import httpx

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """No provider configured / disabled."""


class LLMError(RuntimeError):
    """Provider call failed or returned unusable output."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str                  # anthropic | openai | ollama
    model: str
    api_key: str = ''
    base_url: str = ''
    timeout_s: float = 60.0
    max_retries: int = 1

    @property
    def local(self) -> bool:
        return self.provider == 'ollama' and any(h in self.base_url for h in ('localhost', '127.0.0.1', '[::1]'))


def _truthy(v: str | None, default=True) -> bool:
    return default if v is None else v.strip().lower() in ('1', 'true', 'yes', 'on')


def load_config(env: Mapping[str, str] | None = None) -> LLMConfig | None:
    """Provider auto-detection. Explicit ``LLM_PROVIDER`` wins; otherwise Anthropic > OpenAI > Ollama."""
    env = os.environ if env is None else env
    if not _truthy(env.get('LLM_ENABLED'), True):
        return None
    provider = (env.get('LLM_PROVIDER') or '').strip().lower()
    if provider in ('none', 'off', 'disabled'):
        return None
    if not provider:
        if env.get('ANTHROPIC_API_KEY'):
            provider = 'anthropic'
        elif env.get('OPENAI_API_KEY'):
            provider = 'openai'
        elif env.get('OLLAMA_MODEL') or env.get('OLLAMA_HOST'):
            provider = 'ollama'
        else:
            return None
    model = env.get('LLM_MODEL', '')
    timeout = float(env.get('LLM_TIMEOUT_S', '60'))
    if provider == 'anthropic':
        key = env.get('ANTHROPIC_API_KEY', '')
        if not key:
            return None
        return LLMConfig('anthropic', model or env.get('ANTHROPIC_MODEL', 'claude-sonnet-5-5'), key,
                         env.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com'), timeout)
    if provider == 'openai':      # also any OpenAI-compatible server via OPENAI_BASE_URL (vLLM, LM Studio, Azure proxies...)
        key = env.get('OPENAI_API_KEY', '')
        model = model or env.get('OPENAI_MODEL', '')
        if not key or not model:
            logger.warning('OpenAI provider needs OPENAI_API_KEY and LLM_MODEL/OPENAI_MODEL; LLM disabled')
            return None
        return LLMConfig('openai', model, key, env.get('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/'), timeout)
    if provider == 'ollama':
        model = model or env.get('OLLAMA_MODEL', '')
        if not model:
            logger.warning('Ollama provider needs OLLAMA_MODEL/LLM_MODEL; LLM disabled')
            return None
        return LLMConfig('ollama', model, '', env.get('OLLAMA_HOST', 'http://localhost:11434').rstrip('/'), timeout)
    logger.warning('Unknown LLM_PROVIDER %r; LLM disabled', provider)
    return None


_JSON_FENCE = re.compile(r'^```(?:json)?\s*|\s*```$', re.I)


def extract_json(text: str) -> Any:
    """Models wrap JSON in prose/fences; be tolerant but strict about the result being valid JSON."""
    t = _JSON_FENCE.sub('', text.strip())
    try:
        return json.loads(t)
    except ValueError:
        pass
    for open_, close in (('{', '}'), ('[', ']')):
        i, j = t.find(open_), t.rfind(close)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except ValueError:
                continue
    raise LLMError('model did not return valid JSON')


class LLMClient:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        self._http = httpx.Client(timeout=config.timeout_s, transport=transport)

    # -- provider calls -----------------------------------------------------------
    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        r = self._http.post(f'{self.config.base_url}/v1/messages', headers={
            'x-api-key': self.config.api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': self.config.model, 'max_tokens': max_tokens, 'system': system,
                  'messages': [{'role': 'user', 'content': user}]})
        r.raise_for_status()
        return ''.join(b.get('text', '') for b in r.json().get('content', []) if b.get('type') == 'text')

    def _openai(self, system: str, user: str, max_tokens: int) -> str:
        r = self._http.post(f'{self.config.base_url}/chat/completions', headers={
            'Authorization': f'Bearer {self.config.api_key}', 'content-type': 'application/json'},
            json={'model': self.config.model, 'max_tokens': max_tokens,
                  'response_format': {'type': 'json_object'},
                  'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]})
        r.raise_for_status()
        return r.json()['choices'][0]['message']['content'] or ''

    def _ollama(self, system: str, user: str, max_tokens: int) -> str:
        r = self._http.post(f'{self.config.base_url}/api/chat', json={
            'model': self.config.model, 'stream': False, 'format': 'json',
            'options': {'num_predict': max_tokens, 'temperature': 0.2},
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]})
        r.raise_for_status()
        return r.json().get('message', {}).get('content', '')

    def complete_text(self, system: str, user: str, max_tokens: int = 1500) -> str:
        fn: Callable[[str, str, int], str] = {'anthropic': self._anthropic, 'openai': self._openai,
                                              'ollama': self._ollama}[self.config.provider]
        last: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return fn(system, user, max_tokens)
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                last = exc
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status and 400 <= status < 500 and status != 429:
                    break                                   # client error: retrying will not help
        # never log prompt/response bodies (they may contain candidate data)
        raise LLMError(f'{self.config.provider} request failed: {type(last).__name__}') from last

    def complete_json(self, system: str, user: str, *, required: Mapping[str, type] | None = None,
                      max_tokens: int = 1500) -> dict:
        system = (system + '\n\nReply with a single JSON object only. No prose, no markdown fences.')
        data = extract_json(self.complete_text(system, user, max_tokens))
        if not isinstance(data, dict):
            raise LLMError('expected a JSON object')
        for key, typ in (required or {}).items():
            if not isinstance(data.get(key), typ):
                raise LLMError(f'field {key!r} missing or not {typ.__name__}')
        return data


_client: LLMClient | None = None
_client_loaded = False


def get_client(refresh: bool = False) -> LLMClient | None:
    """Process-wide client or None when no provider is configured."""
    global _client, _client_loaded
    if refresh or not _client_loaded:
        cfg = load_config()
        _client = LLMClient(cfg) if cfg else None
        _client_loaded = True
    return _client
