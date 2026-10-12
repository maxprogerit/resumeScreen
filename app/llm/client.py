"""LLM abstraction. Services depend on this protocol, never on the OpenAI SDK directly,
so tests run against a fake and never touch the network."""

from collections.abc import Sequence
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(Exception):
    """No API key, network failure, quota, etc. Not worth retrying immediately."""


class LLMInvalidOutput(Exception):
    """The model answered but the answer failed schema validation or was refused."""


class LLMClient(Protocol):
    chat_model: str
    embedding_model: str

    def parse(self, *, system: str, user: str, schema: type[T]) -> T:
        """Return a validated `schema` instance or raise LLMUnavailable / LLMInvalidOutput."""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
