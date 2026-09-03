"""Auth FastAPI dependencies.

This module implements the core security logic for authentication and defines
reusable FastAPI dependencies for user resolution. Requests are authenticated
with app-issued access tokens; external provider tokens are only exchanged for
app tokens at login.
"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.config import auth_settings
from app.auth.schemas import User
from app.auth.tokens import ACCESS_TOKEN_TYPE, decode_internal_token
from app.auth.utils import create_debug_payload, invalid_credentials_exception
from app.core.config import settings

# Define the HTTP Bearer scheme for FastAPI
http_bearer = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(http_bearer)],
) -> User:
    """Get the current authenticated user from the app-issued access token.

    Args:
        credentials(HTTPAuthorizationCredentials): The HTTP Bearer credentials
            extracted from the Authorization header.

    Returns:
        User: The authenticated user represented as a User model instance.

    Raises:
        HTTPException: If the token is invalid or authentication fails,
            an HTTP 401 Unauthorized error is raised.
    """
    try:
        payload = (
            decode_internal_token(
                credentials.credentials, expected_type=ACCESS_TOKEN_TYPE
            )
            if not (settings.APP_DEBUG and auth_settings.APP_AUTH_DEBUG)
            else create_debug_payload()
        )
        return User(**payload)
    except Exception:
        raise invalid_credentials_exception()


CurrentUser = Annotated[User, Depends(get_current_user)]
