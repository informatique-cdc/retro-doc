"""Auth service.

This module contains the business logic for authentication, including
the login and refresh flows. It orchestrates the interaction between
identity providers, token issuance, and user session management.
"""

from app.auth.config import auth_settings
from app.auth.providers.base import ProviderIdentity
from app.auth.providers.registry import get_provider
from app.auth.schemas import AuthProviderName, TokenClaims, TokenResponse
from app.auth.tokens import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_internal_token,
)
from app.auth.utils import compute_uid


def _claims_from_identity(identity: ProviderIdentity) -> TokenClaims:
    """Build token claims (with a stable uid) from a provider identity.

    Args:
        identity (ProviderIdentity): The identity information from the provider.

    Returns:
        TokenClaims: The claims to be embedded in the token.
    """
    uid = compute_uid(
        oid=identity.oid, tid=identity.tid, iss=identity.iss, sub=identity.sub
    )
    return TokenClaims(
        uid=uid,
        sub=identity.sub,
        name=identity.name,
        preferred_username=identity.preferred_username,
        oid=identity.oid,
        tid=identity.tid,
    )


def _issue_tokens(claims: TokenClaims) -> TokenResponse:
    """Issue an access + refresh token pair for the given claims.

    Args:
        claims (TokenClaims): The claims to be embedded in the tokens.

    Returns:
        TokenResponse: The issued access and refresh tokens.
    """
    return TokenResponse(
        access_token=create_access_token(claims),
        refresh_token=create_refresh_token(claims),
        expires_in=auth_settings.JWT_ACCESS_TOKEN_DURATION_S,
    )


async def login(provider_name: AuthProviderName, credential: str) -> TokenResponse:
    """Exchange a provider credential for app-issued tokens.

    Args:
        provider_name (AuthProviderName): The identity provider to authenticate with.
        credential (str): The provider-issued credential (an OIDC id_token).

    Returns:
        TokenResponse: The issued access and refresh tokens.

    Raises:
        KeyError: If no provider is registered for `provider_name`.
        jwt.exceptions.InvalidTokenError: If the credential is invalid.
    """
    provider = get_provider(provider_name)
    identity = await provider.authenticate(credential)
    return _issue_tokens(_claims_from_identity(identity))


def refresh(refresh_token: str) -> TokenResponse:
    """Issue a fresh token pair from a valid refresh token (rotation).

    Args:
        refresh_token (str): A previously issued refresh token.

    Returns:
        TokenResponse: The newly issued access and refresh tokens.

    Raises:
        jwt.exceptions.InvalidTokenError: If the refresh token is invalid,
            expired, or not a refresh token.
    """
    payload = decode_internal_token(refresh_token, expected_type=REFRESH_TOKEN_TYPE)
    claims = TokenClaims(
        uid=payload["uid"],
        sub=payload["sub"],
        name=payload.get("name"),
        preferred_username=payload.get("preferred_username"),
        oid=payload.get("oid"),
        tid=payload.get("tid"),
    )
    return _issue_tokens(claims)
