"""Offline embedder used ONLY for demo seeding without an API key.

It is a hashed bag-of-words vector, i.e. lexical overlap, not semantic understanding.
Rows embedded with it carry embedding_model="offline-hash-demo" so they are never
compared with real embeddings.
"""

import hashlib
import re
from collections.abc import Sequence

import numpy as np

OFFLINE_MODEL = "offline-hash-demo"
_DIM = 256
_TOKEN = re.compile(r"[a-z0-9+#.]+")


def hash_embed(text: str) -> list[float]:
    vec = np.zeros(_DIM, dtype=np.float32)
    for token in _TOKEN.findall(text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        vec[int.from_bytes(digest[:4], "little") % _DIM] += 1.0
    norm = float(np.linalg.norm(vec))
    return (vec / norm).tolist() if norm else vec.tolist()


class OfflineEmbedder:
    """Embedding-only client. `parse` is unsupported on purpose."""

    chat_model = "none"
    embedding_model = OFFLINE_MODEL

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [hash_embed(t) for t in texts]
