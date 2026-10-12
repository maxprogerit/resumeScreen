"""Browser login: exchange the API key for an HttpOnly session cookie, so the key never
lives in JavaScript-readable storage."""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.errors import ApiError
from app.security import (
    SESSION_COOKIE,
    SESSION_TTL_S,
    enforce_login_rate_limit,
    key_matches,
    make_session_token,
    require_api_key,
)

public_router = APIRouter(tags=["session"])
protected_router = APIRouter(tags=["session"], dependencies=[Depends(require_api_key)])


class LoginBody(BaseModel):
    key: str = Field(min_length=1, max_length=500)


@public_router.post("/session", dependencies=[Depends(enforce_login_rate_limit)])
def login(body: LoginBody, request: Request, response: Response) -> dict[str, bool]:
    configured = request.app.state.settings.api_key_admin
    if configured is None:
        raise ApiError(503, "auth_not_configured", "API_KEY_ADMIN is not configured on the server")
    api_key = configured.get_secret_value()
    if not key_matches(body.key, api_key):
        raise ApiError(401, "unauthorized", "The API key was rejected")
    response.set_cookie(
        SESSION_COOKIE,
        make_session_token(api_key),
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return {"authenticated": True}


@public_router.delete("/session", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@protected_router.get("/session")
def session_status() -> dict[str, bool]:
    return {"authenticated": True}
