"""InfoMentor session management - login chain, SAML handling, session persistence."""

import http.cookiejar
import json
import logging
import os
import re
import secrets
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import portalocker
import requests
from deformentor_cli.errors import AuthenticationError, OAuthSetupRequired, UpstreamStateError
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
PAIR_URL = "https://hub.infomentor.se/account/pair/GetAuthenticationData"
TOKEN_URL = "https://im.infomentor.se/Authentication/OAuth2/Token"
SSO_API_URL = "https://api-im.infomentor.se/NA1/Authentication/sso"
MENTOR_URL = "https://infomentor.se/swedish/production/mentor/"
OAUTH_CLIENT_ID = "notificationapp"
OAUTH_CLIENT_SECRET = "NONE"
OAUTH_SCOPE = "IM2-API-NOTIFICATION"
OAUTH_REDIRECT_URI = "InfomentorNotification://oauth2Callback"


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


def oauth_state(path):
    """Return the locally observable OAuth credential state."""
    credential_path = Path(path)
    if not credential_path.exists():
        return "missing"
    try:
        data = json.loads(credential_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid"
    token = data.get("refresh_token") if isinstance(data, dict) else None
    return "configured" if isinstance(token, str) and token.strip() else "invalid"


def load_oauth_credential(path):
    """Load a private OAuth refresh token, accepting harmless legacy fields."""
    state = oauth_state(path)
    if state == "missing":
        return None
    if state == "invalid":
        raise OAuthSetupRequired("Stored OAuth authentication is invalid.")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OAuthSetupRequired("Stored OAuth authentication is invalid.") from error
    return {"refresh_token": data["refresh_token"]}


def save_oauth_credential(credential, path):
    """Atomically save only the rotating refresh token with private permissions."""
    token = credential.get("refresh_token") if isinstance(credential, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise AuthenticationError("InfoMentor returned an invalid OAuth credential.")
    write_private_text(
        Path(path),
        json.dumps({"refresh_token": token}, separators=(",", ":")) + "\n",
    )


def _json_object(response, message):
    try:
        data = response.json()
    except (ValueError, requests.JSONDecodeError) as error:
        raise AuthenticationError(message) from error
    if not isinstance(data, dict):
        raise AuthenticationError(message)
    return data


def _validate_pairing_template(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise AuthenticationError("Refused an unexpected OAuth authorization endpoint.") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "im.infomentor.se"
        or parsed.path != "/Authentication/Authentication/LoginOAuth2"
        or port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise AuthenticationError("Refused an unexpected OAuth authorization endpoint.")
    return url


def _build_pairing_url(template):
    template = _validate_pairing_template(template)
    url = (
        template.replace("{DeviceIdentifier}", "deformentor-" + secrets.token_hex(12))
        .replace("{DeviceFriendlyName}", "Deformentor")
        .replace("{DeviceType}", "Android")
    )
    parsed = urlsplit(url)
    replacements = {
        "scope": OAUTH_SCOPE,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "client_id": OAUTH_CLIENT_ID,
        "response_type": "code",
    }
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key not in replacements]
    query.extend(replacements.items())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _oauth_callback_code(location):
    try:
        parsed = urlsplit(location)
        port = parsed.port
    except (TypeError, ValueError):
        raise AuthenticationError("Refused an unexpected OAuth callback URL.") from None
    if parsed.scheme.lower() != "infomentornotification":
        return None
    if (
        parsed.netloc.lower() != "oauth2callback"
        or parsed.path not in ("", "/")
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.fragment
    ):
        raise AuthenticationError("Refused an unexpected OAuth callback URL.")
    values = parse_qs(parsed.query).get("code", [])
    if len(values) != 1 or not values[0]:
        raise AuthenticationError("InfoMentor did not return an OAuth authorization code.")
    return values[0]


def _capture_oauth_code(session, authorize_url, max_hops=10):
    next_url = authorize_url
    for _ in range(max_hops):
        response = session.get(next_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
        location = response.headers.get("Location", "")
        if not location:
            response.raise_for_status()
            break
        location = urljoin(next_url, location)
        code = _oauth_callback_code(location)
        if code:
            return code
        try:
            parsed = urlsplit(location)
            port = parsed.port
        except (TypeError, ValueError):
            raise AuthenticationError("Refused an unexpected OAuth authorization redirect.") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != "im.infomentor.se"
            or port not in (None, 443)
            or parsed.username
            or parsed.password
            or not parsed.path.startswith("/Authentication/")
            or parsed.fragment
        ):
            raise AuthenticationError("Refused an unexpected OAuth authorization redirect.")
        next_url = location
    raise AuthenticationError("InfoMentor did not return an OAuth authorization code.")


def _exchange_oauth_code(session, token_url, code):
    if token_url != TOKEN_URL:
        raise AuthenticationError("Refused an unexpected OAuth token endpoint.")
    response = session.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    tokens = _json_object(response, "InfoMentor returned an invalid OAuth token response.")
    if not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise AuthenticationError("InfoMentor returned an incomplete OAuth token response.")
    return {"refresh_token": tokens["refresh_token"]}


def pair_oauth(session):
    """Pair the public InfoMentor mobile OAuth client from a verified web session."""
    response = session.post(PAIR_URL, allow_redirects=False, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    metadata = _json_object(response, "InfoMentor returned invalid OAuth pairing metadata.")
    template = metadata.get("authenticationUrl")
    token_url = metadata.get("tokenUrl")
    if not isinstance(template, str) or not isinstance(token_url, str):
        raise AuthenticationError("InfoMentor returned incomplete OAuth pairing metadata.")
    code = _capture_oauth_code(session, _build_pairing_url(template))
    return _exchange_oauth_code(session, token_url, code)


def refresh_oauth(path):
    """Rotate a stored refresh token and persist its replacement immediately."""
    credential = load_oauth_credential(path)
    if credential is None:
        return None
    response = requests.post(
        TOKEN_URL,
        data={
            "refresh_token": credential["refresh_token"],
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "scope": OAUTH_SCOPE,
        },
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code in {400, 401, 403}:
        raise OAuthSetupRequired("Stored OAuth authentication was rejected.")
    response.raise_for_status()
    tokens = _json_object(response, "InfoMentor returned an invalid OAuth refresh response.")
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not isinstance(access_token, str) or not access_token or not isinstance(refresh_token, str) or not refresh_token:
        raise AuthenticationError("InfoMentor returned an incomplete OAuth refresh response.")
    save_oauth_credential({"refresh_token": refresh_token}, path)
    return access_token


def oauth_web_session(access_token):
    """Exchange a mobile access token for a verified InfoMentor web session."""
    response = requests.get(
        SSO_API_URL,
        headers={"Authorization": "Bearer " + access_token},
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code in {401, 403}:
        raise OAuthSetupRequired("Stored OAuth authentication was rejected.")
    response.raise_for_status()
    try:
        sso_url = response.json()
    except (ValueError, requests.JSONDecodeError) as error:
        raise AuthenticationError("InfoMentor returned an invalid OAuth SSO response.") from error
    if not isinstance(sso_url, str):
        raise AuthenticationError("InfoMentor returned an invalid OAuth SSO response.")
    parsed = urlsplit(validate_auth_url(sso_url))
    if parsed.hostname != "infomentor.se" or parsed.path.lower() != "/swedish/production/mentor/":
        raise AuthenticationError("Refused an unexpected OAuth SSO URL.")

    session = new_session()
    first = session.get(sso_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
    location = first.headers.get("Location", "")
    if not location:
        first.raise_for_status()
        raise AuthenticationError("InfoMentor OAuth SSO did not redirect to the web login.")
    login_url = validate_auth_url(urljoin(sso_url, location))
    login_page = session.get(login_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
    login_page.raise_for_status()
    oauth_token = _extract_oauth_token(login_page.text)
    mentor_response = session.post(
        MENTOR_URL,
        data={"oauth_token": oauth_token},
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    final = follow_redirects(
        session,
        mentor_response,
        method="POST",
        data={"oauth_token": oauth_token},
    )
    final.raise_for_status()
    verify_authenticated(session)
    return session


def _try_saved_session(session, session_path):
    if not session_path or not load_session(session, session_path):
        return False
    try:
        verify_authenticated(session)
        return True
    except AuthenticationError:
        return False
    except requests.HTTPError as error:
        if getattr(error.response, "status_code", None) in {401, 403}:
            return False
        raise


def _freja_web_login(personnummer, session, quiet=False):
    """Create a verified web session through Stockholm and Freja eID+."""
    response = session.get(
        validate_auth_url("https://hub.infomentor.se/"),
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    response = follow_redirects(session, response)
    response.raise_for_status()
    oauth_token = _extract_oauth_token(response.text)

    response = session.post(
        MENTOR_URL,
        data={"oauth_token": oauth_token},
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    response = follow_redirects(session, response, method="POST", data={"oauth_token": oauth_token})
    response.raise_for_status()

    sso_url = validate_auth_url(_extract_stockholm_sso_url(response.text))
    response = session.get(sso_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
    response = follow_redirects(session, response)
    response.raise_for_status()

    freja_url = validate_auth_url(_extract_freja_link(response.text))
    response = session.get(freja_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
    response = follow_redirects(session, response)
    response.raise_for_status()
    freja_page_url = validate_auth_url(response.url)

    freja_login(
        session,
        freja_page_url,
        personnummer,
        on_started=lambda: print("Approve the login in Freja eID+.", file=sys.stderr, flush=True),
    )

    response = session.get(freja_page_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
    response = follow_redirects(session, response)
    response.raise_for_status()
    handle_saml_chain(session, response.text, response.url)
    verify_authenticated(session)
    return session


def setup_login(personnummer, _session=None, quiet=False):
    """Perform explicit Freja setup and return a web session plus OAuth credential."""
    session = _freja_web_login(personnummer, _session or new_session(), quiet=quiet)
    credential = pair_oauth(session)
    verify_authenticated(session)
    return session, credential


def login(personnummer, _session=None, session_path=None, oauth_path=None, lock_path=None, quiet=False):
    """Return an authenticated session using cookies, OAuth, or legacy Freja."""
    session = _session or new_session()
    if _try_saved_session(session, session_path):
        return session

    if oauth_path and oauth_state(oauth_path) != "missing":
        lock_file = Path(lock_path) if lock_path else Path(oauth_path).with_suffix(".lock")
        lock_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with portalocker.Lock(str(lock_file), mode="a", timeout=HTTP_TIMEOUT):
                refreshed_session = new_session()
                if _try_saved_session(refreshed_session, session_path):
                    return refreshed_session
                access_token = refresh_oauth(oauth_path)
                refreshed_session = oauth_web_session(access_token)
                if session_path:
                    save_session(refreshed_session, session_path)
                return refreshed_session
        except portalocker.exceptions.LockException as error:
            raise UpstreamStateError(
                "Another Deformentor process is renewing authentication. Try again."
            ) from error

    session = _session or new_session()
    session = _freja_web_login(personnummer, session, quiet=quiet)
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
