import json
import sys


EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_NETWORK = 5


class FrejaError(Exception):
    """Base error for Freja eID+ authentication."""


class FrejaTimeoutError(FrejaError):
    """Authentication timed out or expired."""


class FrejaRejectedError(FrejaError):
    """User rejected the authentication in the Freja app."""


def emit_error(code, message, exit_code=EXIT_ERROR):
    """Write structured JSON error to stderr and exit."""
    error = {"error": code, "message": message}
    print(json.dumps(error), file=sys.stderr)
    sys.exit(exit_code)
