"""Freja eID+ remote authentication via Stockholms stad."""

import datetime
import json
import time
from urllib.parse import urlparse, urlunparse

from deformentor_cli.errors import FrejaError, FrejaRejectedError, FrejaTimeoutError

HTTP_TIMEOUT = 30


def freja_login(session, freja_url, personnummer, poll_interval=2.0, timeout=60.0):
    """Authenticate via Freja eID+ 'pa annan enhet' on Stockholms stad.

    Submits the personnummer to Stockholms stad's Freja endpoint, then polls
    until the user approves on their phone. On success, the session has
    SiteMinder auth cookies set by Stockholms stad.

    Args:
        session: requests.Session navigated to the Stockholms stad Freja page.
        freja_url: Full URL of the Freja eID page (login003.stockholm.se/NECSadcfreja/...).
        personnummer: Swedish personal number (10 or 12 digits).
        poll_interval: Seconds between status checks.
        timeout: Max seconds to wait for approval.

    Raises:
        FrejaRejectedError: User rejected in the Freja app.
        FrejaTimeoutError: Timed out waiting for approval.
        FrejaError: Other authentication failure.
    """
    _init_auth(session, freja_url, personnummer)
    _poll_until_done(session, freja_url, poll_interval, timeout)


def _init_auth(session, freja_url, personnummer):
    """POST to start Freja authentication.

    Stockholms stad's JS uses origin+pathname (no query string) for the init request.
    The Freja API requires a 12-digit personnummer (YYYYMMDDXXXX).
    """
    pn = _ensure_12_digits(personnummer)
    parsed = urlparse(freja_url)
    base_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    init_url = f"{base_url}?action=init&userInput={pn}"

    resp = session.post(init_url, timeout=HTTP_TIMEOUT)
    if not resp.ok:
        raise FrejaError(f"Failed to initiate Freja auth: HTTP {resp.status_code}")


def _poll_until_done(session, freja_url, poll_interval, timeout):
    """Poll checkstatus until terminal state.

    Stockholms stad's JS uses the full URL (with query params) for polling.
    """
    separator = "&" if "?" in freja_url else "?"
    poll_url = f"{freja_url}{separator}action=checkstatus"

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        time.sleep(poll_interval)
        resp = session.get(poll_url, timeout=HTTP_TIMEOUT)
        status = _parse_status(resp.text)

        if status == "APPROVED":
            return
        if status == "CANCELED":
            raise FrejaRejectedError("Authentication was rejected in the Freja app")
        if status in ("EXPIRED", "TIMEOUT"):
            raise FrejaTimeoutError(f"Authentication expired (status: {status})")
        if status in ("ERROR", "RP_CANCELED"):
            raise FrejaError(f"Authentication failed (status: {status})")

    raise FrejaTimeoutError(f"Authentication timed out after {timeout}s")


def _ensure_12_digits(personnummer):
    """Convert a 10-digit personnummer to 12 digits by adding century prefix."""
    if len(personnummer) == 12:
        return personnummer
    year = int(personnummer[:2])
    cutoff = datetime.date.today().year % 100
    century = "20" if year <= cutoff else "19"
    return century + personnummer


def _parse_status(text):
    """Parse status from checkstatus response (JSON or plain text)."""
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data.get("status", text)
    except (json.JSONDecodeError, ValueError):
        pass
    return text
