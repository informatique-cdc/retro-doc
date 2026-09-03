"""Auth router.

This module defines the API endpoints related to authentication and user identity.
"""

import httpx
from fastapi import APIRouter, HTTPException, status
from jwt.exceptions import InvalidTokenError

from app.auth.dependencies import CurrentUser
from app.auth.schemas import (
    AuthProviderName,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    TokenResponse,
)
from app.auth.service import login, refresh
from app.auth.utils import invalid_credentials_exception

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/login/{provider}", response_model=TokenResponse)
async def login_endpoint(
    provider: AuthProviderName, body: LoginRequest
) -> TokenResponse:
    """Exchange a provider credential for app-issued access and refresh tokens.

    Args:
        provider(AuthProviderName): The identity provider (path parameter).
        body(LoginRequest): The provider-issued credential to validate.

    Returns:
        TokenResponse: The issued access and refresh tokens.

    Raises:
        HTTPException: 401 if the credential is invalid; 503 if the identity
            provider cannot be reached.
    """
    try:
        return await login(provider, body.token)
    except InvalidTokenError as e:
        raise invalid_credentials_exception() from e
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity provider is unavailable",
        ) from e


@auth_router.get("/me", response_model=MeResponse)
async def me_endpoint(user: CurrentUser) -> MeResponse:
    """Return the identity of the currently authenticated user.

    Args:
        user(CurrentUser): The authenticated user (injected by FastAPI).

    Returns:
        MeResponse: The current user's stable id and display fields.
    """
    return MeResponse.model_validate(user, from_attributes=True)


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh_endpoint(body: RefreshRequest) -> TokenResponse:
    """Issue a fresh token pair from a valid refresh token.

    Args:
        body(RefreshRequest): The refresh token to exchange.

    Returns:
        TokenResponse: The newly issued access and refresh tokens.

    Raises:
        HTTPException: 401 if the refresh token is invalid, expired, or not a
            refresh token.
    """
    try:
        return refresh(body.refresh_token)
    except InvalidTokenError as e:
        raise invalid_credentials_exception() from e
