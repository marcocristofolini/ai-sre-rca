from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def evidence_hash_payload(evidence: Any) -> dict[str, Any]:
    if hasattr(evidence, "model_dump"):
        raw = evidence.model_dump(mode="json")
    else:
        raw = dict(evidence)
    raw.pop("integrity_sha256", None)
    return raw


def compute_evidence_hash(evidence: Any) -> str:
    return sha256_json(evidence_hash_payload(evidence))


def verify_evidence_hash(evidence: Any) -> bool:
    expected = getattr(evidence, "integrity_sha256", None)
    if not expected:
        return False
    return expected == compute_evidence_hash(evidence)
