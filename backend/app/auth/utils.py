"""Auth utilities.

This module provides utility functions for authentication in the Retro-Documentation Backend application.
"""


def gen_debug_payload() -> dict[str, str | int]:
    """Generate a fixed JWT payload for debugging purposes when authentication is disabled.

    Returns:
        dict[str, str | int]: A dictionary representing the JWT payload with fixed values.
    """
    return dict(
        iss="https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0",
        sub="12345678-1234-1234-1234-123456789012",
        aud="debug-client-id",
        exp=9999999999,
        nbf=0,
        iat=0,
        name="John Doe",
        preferred_username="johndoe",
        oid="12345678-1234-1234-1234-123456789012",
        tid="00000000-0000-0000-0000-00000",
    )
