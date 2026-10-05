"""
Post-interview evaluation from the transcript.

With a configured LLM the transcript is summarised into evidence-backed topic notes; without one a deterministic
fallback reports coverage statistics only. Either way:

* the output is **decision support for a human**, never a hire/reject verdict (EU AI Act Annex III, NYC LL144, FCRA);
* proctoring/integrity data is deliberately **not** passed to the model, and the model's output is never fed back into
  integrity decisions.
"""
from __future__ import annotations

import re
from typing import Any

from . import safety
from .client import LLMClient, LLMError, get_client

SCHEMA = 'interview-evaluation/1'
SIGNALS = ('strong_signal', 'mixed_signal', 'weak_signal', 'insufficient_data')
MAX_TRANSCRIPT_CHARS = 24000

SYSTEM = f"""You assist a human hiring panel by summarising a voice-interview transcript.
{safety.DATA_RULE}
Rules: judge only evidence present in the transcript; quote short evidence snippets; do not infer or mention age,
gender, ethnicity, nationality, health, accent or any protected attribute; do not give a hire/reject decision.
Return JSON: {{"summary": str (<=600 chars), "topics": [{{"topic": str, "score": int 0-5, "evidence": str (<=200 chars),
"concern": str (<=200 chars, may be empty)}}], "strengths": [str], "concerns": [str],
"overall_signal": one of {list(SIGNALS)}}}"""


def _turns(transcript: list[dict]) -> tuple[list[str], list[str]]:
    cand = [t['text'] for t in transcript if t.get('role') == 'candidate' and t.get('text')]
    intr = [t['text'] for t in transcript if t.get('role') == 'interviewer' and t.get('text')]
    return cand, intr


def rule_based(transcript: list[dict], topics: list[str]) -> dict[str, Any]:
    cand, intr = _turns(transcript)
    words = ' '.join(cand).lower()
    n_words = len(words.split())
    cov = []
    for t in topics[:12]:
        hits = len(re.findall(r'\b' + re.escape(t.lower()) + r'\b', words))
        cov.append({'topic': t, 'mentions': hits, 'covered': hits > 0})
    signal = 'insufficient_data' if n_words < 150 else 'mixed_signal'
    return {
        'schema': SCHEMA, 'source': 'rules', 'provider': None,
        'summary': (f'{len(cand)} candidate segments, ~{n_words} words; '
                    f'{sum(c["covered"] for c in cov)}/{len(cov)} planned topics mentioned.'),
        'topics': [{'topic': c['topic'], 'score': None, 'evidence': f'{c["mentions"]} mention(s)', 'concern': ''} for c in cov],
        'strengths': [], 'concerns': [] if n_words >= 150 else ['Very little candidate speech was captured.'],
        'overall_signal': signal, 'stats': {'candidate_segments': len(cand), 'candidate_words': n_words,
                                            'interviewer_segments': len(intr)},
        'human_review_required': True,
    }


def _validate(data: dict) -> dict:
    topics = []
    for t in (data.get('topics') or [])[:12]:
        if not isinstance(t, dict):
            continue
        try:
            score = max(0, min(5, int(t.get('score'))))
        except (TypeError, ValueError):
            score = None
        topics.append({'topic': safety.clip_str(t.get('topic', ''), 80), 'score': score,
                       'evidence': safety.clip_str(t.get('evidence', ''), 200),
                       'concern': safety.clip_str(t.get('concern', ''), 200)})
    sig = data.get('overall_signal')
    return {
        'summary': safety.clip_str(data.get('summary', ''), 600), 'topics': topics,
        'strengths': [safety.clip_str(x, 200) for x in (data.get('strengths') or [])[:6] if isinstance(x, str)],
        'concerns': [safety.clip_str(x, 200) for x in (data.get('concerns') or [])[:6] if isinstance(x, str)],
        'overall_signal': sig if sig in SIGNALS else 'mixed_signal',
    }


def evaluate(transcript: list[dict], topics: list[str], level: str = '', client: LLMClient | None = None) -> dict:
    base = rule_based(transcript, topics)
    client = client or get_client()
    if client is None or base['stats']['candidate_words'] < 150:
        return base
    text = '\n'.join(f"{t.get('role', '?').upper()}: {t.get('text', '')}" for t in transcript if t.get('text'))
    user = (f'Experience level: {safety.clip_str(level, 20)}\nPlanned topics: {", ".join(topics[:12])}\n'
            + safety.as_data('transcript', text[-MAX_TRANSCRIPT_CHARS:], MAX_TRANSCRIPT_CHARS))
    try:
        data = client.complete_json(SYSTEM, user, required={'summary': str, 'topics': list}, max_tokens=1800)
    except LLMError:
        base['llm_error'] = True
        return base
    out = _validate(data)
    return {**base, **out, 'source': 'llm', 'provider': client.config.provider, 'model': client.config.model,
            'local_model': client.config.local, 'human_review_required': True}
