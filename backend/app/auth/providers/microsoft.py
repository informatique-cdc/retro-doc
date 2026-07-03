"""Microsoft (Entra ID) identity provider.

This module validates a Microsoft-issued OIDC id_token via JWKS and maps its
claims onto a normalized `ProviderIdentity`.

Microsoft recommends the `tid`+`oid` pair as the immutable, globally-unique key
for a user: `oid` (the object id) is unique only within a tenant, so it must be
combined with `tid` (the tenant id) to be globally unique. `sub` is a pairwise
(per-application) identifier and so unsuitable as a cross-app identity. The
derived `User.uid` is built from this pair, so every user's existing data is
keyed to it — which is why both claims must be preserved.
"""

from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError

from app.auth.oidc import validate_oidc_token
from app.auth.providers.base import AuthProvider, ProviderIdentity


class MicrosoftAuthProvider(AuthProvider):
    """Authenticates users with a Microsoft Entra ID id_token."""

    async def authenticate(self, credential: str) -> ProviderIdentity:
        """Validate a Microsoft id_token and extract the user's identity.

        Args:
            credential (str): The Microsoft-issued OIDC id_token.

        Returns:
            ProviderIdentity: The normalized identity of the authenticated user.

        Raises:
            InvalidTokenError: If the token is invalid or required claims are missing.
        """
        claims = await validate_oidc_token(credential)
        try:
            return ProviderIdentity.model_validate(claims)
        except ValidationError as e:
            raise InvalidTokenError("Token missing required claims") from e
