"""
Tamper-evident audit log. Every persisted proctoring event carries
``hash = sha256(prev_hash || canonical_json(record))``. Editing, deleting or
reordering any row breaks the chain from that point on, which ``verify_chain``
reports. (Tamper-*evident*, not tamper-proof: a DB admin who rewrites the
whole tail can re-hash it, so anchor the head hash externally -- webhooks send
it -- if you need non-repudiation.)
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

GENESIS = '0' * 64


def canonical(record: Mapping) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(',', ':'), default=str).encode()


def link(prev_hash: str, record: Mapping) -> str:
    return hashlib.sha256(prev_hash.encode() + canonical(record)).hexdigest()


def verify_chain(records: Iterable[Mapping]) -> tuple[bool, int | None]:
    """records: ordered dicts with keys ``prev_hash``, ``hash`` and ``body``.
    Returns (ok, index_of_first_bad_record)."""
    prev = GENESIS
    for i, r in enumerate(records):
        if r['prev_hash'] != prev or link(prev, r['body']) != r['hash']:
            return False, i
        prev = r['hash']
    return True, None
