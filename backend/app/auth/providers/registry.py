"""Auth provider registry.

This module maintains the mapping of supported provider names to their
`AuthProvider` implementations. The login flow uses this registry to
look up the appropriate provider for a given request.
"""

from app.auth.providers.base import AuthProvider
from app.auth.providers.microsoft import MicrosoftAuthProvider
from app.auth.schemas import AuthProviderName

_PROVIDERS: dict[AuthProviderName, AuthProvider] = {
    AuthProviderName.MICROSOFT: MicrosoftAuthProvider(),
}


def get_provider(name: AuthProviderName) -> AuthProvider:
    """Return the auth provider registered for the given name.

    Args:
        name (AuthProviderName): The provider identifier from the request path.

    Returns:
        AuthProvider: The matching provider implementation.

    Raises:
        KeyError: If no provider is registered for `name`.
    """
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise KeyError(f"Unsupported auth provider: {name}")
    return provider
