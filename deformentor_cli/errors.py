import json
import sys

from stockholm_freja import (
    FrejaError,
    FrejaHttpError,
    FrejaInputError,
    FrejaRedirectError,
    FrejaRejectedError,
    FrejaTimeoutError,
)


EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_NETWORK = 5


class AuthenticationError(Exception):
    """Authentication failed outside the Freja approval step."""


class OAuthSetupRequired(AuthenticationError):
    """Stored OAuth authentication must be renewed through explicit setup."""


class UpstreamStateError(Exception):
    """An upstream operation completed without the expected state change."""


class CalendarDetailUnavailable(UpstreamStateError):
    """InfoMentor does not currently serve calendar-notification details."""


def emit_error(code, message, exit_code=EXIT_ERROR):
    """Write structured JSON error to stderr and exit."""
    error = {"error": code, "message": message}
    print(json.dumps(error), file=sys.stderr)
    sys.exit(exit_code)
