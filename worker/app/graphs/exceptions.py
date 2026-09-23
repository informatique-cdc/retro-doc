"""Graph exceptions.

This module defines the exceptions raised by the graph services.
"""


class PreparedSourceRequiredError(Exception):
    """Raised when a builder derives from the prepared source but
    that latter is `None`.
    """
