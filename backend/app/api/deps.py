import hmac

from fastapi import Header, HTTPException, status

from app.core.settings import load_settings


def require_bearer_token(authorization: str | None = Header(default=None)) -> None:
    if authorization is None:
        raise _unauthorized()

    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme != "Bearer" or not token:
        raise _unauthorized()

    settings = load_settings()
    if not hmac.compare_digest(token, settings.api_token):
        raise _unauthorized()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authorization credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
