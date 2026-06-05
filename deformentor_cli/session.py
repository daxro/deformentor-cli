"""InfoMentor session management - login chain, SAML handling, session persistence."""

import http.cookiejar
import json
import logging
import os
import re
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from deformentor_cli.errors import AuthenticationError
from deformentor_cli.freja import freja_login
from deformentor_cli.paths import write_private_text

HTTP_TIMEOUT = 30
REDIRECT_CODES = (301, 302, 303, 307, 308)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTTP_LOGGER = logging.getLogger("deformentor_cli.http")
AUTH_ALLOWED_HOSTS = frozenset({
    "hub.infomentor.se",
    "infomentor.se",
    "sso.infomentor.se",
    "login001.stockholm.se",
    "login003.stockholm.se",
})


def validate_auth_url(url):
    """Return an allowed HTTPS authentication URL or fail closed."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise AuthenticationError("Refused an unexpected authentication URL.") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in AUTH_ALLOWED_HOSTS
        or port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        raise AuthenticationError("Refused an unexpected authentication URL.")
    return url


def follow_redirects(session, resp, max_hops=20, method="GET", data=None):
    """Manually follow allowed HTTP redirects with browser-like semantics."""
    method = method.upper()
    for _ in range(max_hops):
        if resp.status_code not in REDIRECT_CODES:
            break
        location = resp.headers.get("Location", "")
        if not location:
            break
        location = validate_auth_url(urljoin(resp.url, location))
        if resp.status_code in {301, 302, 303}:
            method = "GET"
            data = None
        if method == "POST":
            resp = session.post(
                location,
                data=data,
                allow_redirects=False,
                timeout=HTTP_TIMEOUT,
            )
        else:
            resp = session.get(location, allow_redirects=False, timeout=HTTP_TIMEOUT)
    return resp


def parse_hidden_fields(html):
    """Extract all <input type="hidden"> name/value pairs from HTML."""
    fields = {}
    for match in re.finditer(
        r'<input\b[^>]*\btype="hidden"[^>]*/?>',
        html,
        re.IGNORECASE,
    ):
        tag = match.group()
        name = re.search(r'\bname="([^"]+)"', tag)
        value = re.search(r'\bvalue="([^"]*)"', tag)
        if name and value:
            fields[name.group(1)] = unescape(value.group(1))
    return fields


def parse_form_action(html):
    """Extract the action URL from the first <form> in the HTML."""
    match = re.search(r'<form[^>]*\baction="([^"]*)"', html, re.IGNORECASE)
    if not match:
        return None
    return unescape(match.group(1))


def handle_saml_chain(session, html, page_url, max_hops=10):
    """Follow a chain of SAML auto-submit forms.

    Each hop: parse <form action="...">, extract hidden fields, POST them,
    follow redirects. Repeats until no more forms are found.

    Returns (final_html, final_url).
    """
    for _ in range(max_hops):
        action = parse_form_action(html)
        if not action:
            break

        fields = parse_hidden_fields(html)
        if not fields:
            break

        if re.match(r"https?://", action, re.IGNORECASE):
            post_url = action
        else:
            post_url = urljoin(page_url, action)
        post_url = validate_auth_url(post_url)

        resp = session.post(
            post_url, data=fields, allow_redirects=False, timeout=HTTP_TIMEOUT
        )
        resp = follow_redirects(session, resp, method="POST", data=fields)
        resp.raise_for_status()
        page_url = resp.url
        html = resp.text

    return html, page_url


def login(personnummer, _session=None, session_path=None, quiet=False):
    """Log into InfoMentor via Stockholms stad Freja eID+.

    Creates a requests.Session, navigates the InfoMentor -> Stockholms stad
    -> Freja eID+ login chain, waits for phone approval, follows SAML
    redirects back, and returns the authenticated session.

    Checks session_path for a saved session first. If valid, skips login.
    On successful login, saves the session to session_path.

    Args:
        personnummer: Swedish personal number (10 or 12 digits).
        _session: Inject a session for testing. Created if not provided.
        session_path: Path to session.json for persistence. None to disable.

    Returns:
        Authenticated requests.Session.

    Raises:
        deformentor_cli.errors.FrejaError: If Freja authentication fails.
        Exception: If any step in the login chain fails.
    """
    session = _session or new_session()

    # Try saved session first
    if session_path and load_session(session, session_path):
        try:
            verify_authenticated(session)
            return session
        except AuthenticationError:
            session = _session or new_session()
        except requests.HTTPError as error:
            if getattr(error.response, "status_code", None) in {401, 403}:
                session = _session or new_session()
            else:
                raise

    print("Logging in - approve in Freja on your phone...", file=sys.stderr)

    # Step 1: Get oauth_token from hub
    resp = session.get(validate_auth_url("https://hub.infomentor.se/"), allow_redirects=False, timeout=HTTP_TIMEOUT)
    resp = follow_redirects(session, resp)
    resp.raise_for_status()
    oauth_token = _extract_oauth_token(resp.text)

    # Step 2: POST oauth_token to get IdP selection page
    resp = session.post(
        validate_auth_url("https://infomentor.se/swedish/production/mentor/"),
        data={"oauth_token": oauth_token},
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    resp = follow_redirects(session, resp, method="POST", data={"oauth_token": oauth_token})
    resp.raise_for_status()

    # Step 3: Find Stockholms stad SSO URL and follow to login page
    sso_url = validate_auth_url(_extract_stockholm_sso_url(resp.text))
    resp = session.get(sso_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
    resp = follow_redirects(session, resp)
    resp.raise_for_status()

    # Step 4: Find Freja eID+ link and follow to Freja page
    freja_url = validate_auth_url(_extract_freja_link(resp.text))
    resp = session.get(freja_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
    resp = follow_redirects(session, resp)
    resp.raise_for_status()
    freja_page_url = validate_auth_url(resp.url)

    # Step 5: Freja login (poll until phone approval)
    freja_login(session, freja_page_url, personnummer)

    # Step 6: Reload Freja page to get SAML response
    resp = session.get(freja_page_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
    resp = follow_redirects(session, resp)
    resp.raise_for_status()

    # Step 7: Follow SAML redirect chain back to InfoMentor
    handle_saml_chain(session, resp.text, resp.url)

    # Step 8: Verify authentication
    verify_authenticated(session)

    # Save session for reuse
    if session_path:
        save_session(session, session_path)

    return session


def new_session():
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.hooks["response"].append(_log_response)
    return s


def _log_response(response, *args, **kwargs):
    """Log sanitized response metadata without secrets or query strings."""
    if not HTTP_LOGGER.isEnabledFor(logging.DEBUG):
        return
    request = response.request
    parsed = urlsplit(request.url)
    elapsed_ms = round(response.elapsed.total_seconds() * 1000)
    HTTP_LOGGER.debug(
        "%s %s://%s%s -> %s (%d ms)",
        request.method,
        parsed.scheme,
        parsed.hostname or "",
        parsed.path or "/",
        response.status_code,
        elapsed_ms,
    )


def _extract_oauth_token(html):
    fields = parse_hidden_fields(html)
    token = fields.get("oauth_token")
    if not token:
        raise AuthenticationError("Failed to get the InfoMentor login token.")
    return token


def _extract_stockholm_sso_url(html):
    match = re.search(
        r'value="(https://sso\.infomentor\.se/login\.ashx\?idp=stockholm_par)"',
        html,
    )
    if not match:
        raise AuthenticationError("Could not find the Stockholms stad login endpoint.")
    return match.group(1)


def _extract_freja_link(html):
    match = re.search(
        r'href="(https://login001\.stockholm\.se/NECSadc/freja/b64startpage\.jsp\?startpage=[^"]+)"',
        html,
    )
    if not match:
        raise AuthenticationError("Could not find Freja eID+ on the Stockholms stad login page.")
    return unescape(match.group(1))


def verify_authenticated(session):
    ts = int(time.time() * 1000)
    resp = session.post(
        validate_auth_url(f"https://hub.infomentor.se/authentication/authentication/isauthenticated/?_={ts}"),
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    if resp.text.strip().lower() != "true":
        raise AuthenticationError("InfoMentor did not confirm the authenticated session.")


def save_session(session, path="session.json"):
    """Save session cookies to a JSON file with restricted permissions."""
    cookies = []
    for c in session.cookies:
        cookies.append({
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "secure": c.secure,
            "httponly": "HttpOnly" in c._rest,
        })
    write_private_text(Path(path), json.dumps(cookies, indent=2))


def _valid_cookie_data(cookies):
    """Return whether decoded session JSON has the expected cookie shape."""
    if not isinstance(cookies, list):
        return False
    for cookie in cookies:
        if not isinstance(cookie, dict):
            return False
        if not all(isinstance(cookie.get(key), str) for key in ("name", "value", "domain")):
            return False
        if "path" in cookie and not isinstance(cookie["path"], str):
            return False
        if "secure" in cookie and not isinstance(cookie["secure"], bool):
            return False
        if "httponly" in cookie and not isinstance(cookie["httponly"], bool):
            return False
    return True


def load_session(session, path="session.json"):
    """Load cookies from a JSON file into the session.

    Returns True if cookies were loaded, False if file missing or corrupt.
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            cookies = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False

    if not _valid_cookie_data(cookies):
        return False

    for c in cookies:
        cookie = http.cookiejar.Cookie(
            version=0,
            name=c["name"],
            value=c["value"],
            port=None,
            port_specified=False,
            domain=c["domain"],
            domain_specified=bool(c["domain"]),
            domain_initial_dot=c["domain"].startswith("."),
            path=c.get("path", "/"),
            path_specified=bool(c.get("path")),
            secure=c.get("secure", False),
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": "HttpOnly"} if c.get("httponly") else {},
        )
        session.cookies.set_cookie(cookie)
    return True
