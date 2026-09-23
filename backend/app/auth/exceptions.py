"""Auth exceptions.

Exception classes for the auth domain.
"""

from fastapi import HTTPException, status


class InvalidCredentialsHTTPException(HTTPException):
    """401 Unauthorized raised for any authentication failure.

    Subclasses `HTTPException` with a fixed status, detail and
    `WWW-Authenticate` header so every auth-failure path returns an identical
    response. FastAPI's built-in `HTTPException` handler serialises it.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
