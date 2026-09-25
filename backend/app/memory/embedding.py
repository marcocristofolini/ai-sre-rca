from __future__ import annotations

import hashlib
import math
import re

EMBEDDING_DIM = 128
TOKEN_RE = re.compile(r"[a-zA-Z0-9_.:/-]+")


def hash_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Stable local embedding for zero-dependency incident-memory retrieval.

    It is intentionally simple: production deployments can replace this provider
    with a dedicated embedding model without changing the repository contract.
    """
    vector = [0.0] * dim
    for token in TOKEN_RE.findall(text.lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + min(len(token), 20) / 20.0
        vector[index] += sign * weight

    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))
