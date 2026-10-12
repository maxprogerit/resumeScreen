from collections.abc import Sequence

import openai
from openai import OpenAI
from pydantic import ValidationError

from app.config import Settings
from app.llm.client import LLMInvalidOutput, LLMUnavailable, T

_TIMEOUT_S = 60.0


def _unavailable(exc: openai.OpenAIError) -> LLMUnavailable:
    """Map an SDK failure to a reason the recruiter can act on (key, model, quota)."""
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        reason = "llm_auth_error"
    elif isinstance(exc, openai.NotFoundError):
        reason = "llm_model_not_found"
    elif isinstance(exc, openai.RateLimitError):
        reason = "llm_rate_limited"
    else:
        reason = "llm_unavailable"
    return LLMUnavailable(type(exc).__name__, reason)


class OpenAIClient:
    def __init__(self, settings: Settings) -> None:
        if settings.openai_api_key is None:
            raise LLMUnavailable("OPENAI_API_KEY is not configured", "llm_not_configured")
        self._client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(), timeout=_TIMEOUT_S, max_retries=2
        )
        self.chat_model = settings.openai_chat_model
        self.embedding_model = settings.openai_embedding_model

    def parse(self, *, system: str, user: str, schema: type[T]) -> T:
        try:
            completion = self._client.chat.completions.parse(
                model=self.chat_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=schema,
                temperature=0,
            )
        except (
            ValidationError,
            openai.LengthFinishReasonError,
            openai.ContentFilterFinishReasonError,
        ) as exc:
            # Do not chain: the exception text may contain model output derived from the resume.
            raise LLMInvalidOutput(type(exc).__name__) from None
        except openai.OpenAIError as exc:
            raise _unavailable(exc) from None
        message = completion.choices[0].message
        if message.refusal or message.parsed is None:
            raise LLMInvalidOutput("refusal_or_empty")
        return message.parsed

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(model=self.embedding_model, input=list(texts))
        except openai.OpenAIError as exc:
            raise _unavailable(exc) from None
        return [item.embedding for item in response.data]
