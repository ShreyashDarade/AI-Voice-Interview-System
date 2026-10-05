"""
Optional LLM gap-filling for resumes.

The offline engine stays authoritative. The model is only asked to propose a job *title* and *company* for experience
entries where the rules found none, and anything it returns is tagged ``source: "llm"`` and merged only into those
empty slots. Skills, dates, totals and integrity flags are never touched by a model.
"""
from __future__ import annotations

from . import safety
from .client import LLMClient, LLMError, get_client

SYSTEM = f"""You repair a parsed resume. {safety.DATA_RULE}
For each listed experience entry that lacks a title or company, read its lines and propose them.
Return JSON {{"entries": [{{"index": int, "title": str, "company": str}}]}}. Use "" when unsure. Never invent."""


def enrich(engine: dict, text: str, client: LLMClient | None = None) -> dict:
    """Mutates and returns ``engine``; adds ``llm_enrichment`` metadata when the model was consulted."""
    client = client or get_client()
    exp = engine.get('experience') or []
    gaps = [i for i, e in enumerate(exp) if not e.get('title') or not e.get('company')]
    if client is None or not gaps or not text:
        return engine
    lines = []
    for i in gaps[:10]:
        e = exp[i]
        lines.append(f"[{i}] dates={e.get('start')}..{e.get('end') or 'present'} title={e.get('title') or '?'} "
                     f"company={e.get('company') or '?'} bullets={' | '.join((e.get('bullets') or [])[:3])}")
    user = ('Entries needing repair:\n' + safety.as_data('entries', '\n'.join(lines), 4000)
            + '\nFull resume text:\n' + safety.as_data('resume', text, 9000))
    try:
        data = client.complete_json(SYSTEM, user, required={'entries': list}, max_tokens=900)
    except LLMError:
        return engine
    filled = 0
    for item in data['entries'][:10]:
        if not isinstance(item, dict) or not isinstance(item.get('index'), int) or item['index'] not in gaps:
            continue
        e = exp[item['index']]
        for key in ('title', 'company'):
            val = safety.clip_str(item.get(key, ''), 120)
            if val and not e.get(key):
                e[key] = val
                e.setdefault('filled_by', {})[key] = 'llm'
                filled += 1
    engine['llm_enrichment'] = {'provider': client.config.provider, 'model': client.config.model,
                                'fields_filled': filled, 'local_model': client.config.local}
    return engine
