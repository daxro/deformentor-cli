"""Compatibility shim for Stockholms stad Freja authentication."""

from stockholm_freja import (
    FrejaError,
    FrejaHttpError,
    FrejaInputError,
    FrejaRedirectError,
    FrejaRejectedError,
    FrejaTimeoutError,
    freja_login,
)

__all__ = [
    "FrejaError",
    "FrejaHttpError",
    "FrejaInputError",
    "FrejaRedirectError",
    "FrejaRejectedError",
    "FrejaTimeoutError",
    "freja_login",
]
