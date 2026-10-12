import numpy as np

from app.services.scoring import Vector


def to_bytes(vec: list[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_bytes(data: bytes | None) -> Vector | None:
    if not data:
        return None
    return np.frombuffer(data, dtype=np.float32)
