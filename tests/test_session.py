import json
import stat
import time
import http.cookiejar
import os
import tempfile
from unittest.mock import MagicMock, patch, call

import requests
import requests.cookies

from deformentor_cli.session import follow_redirects, parse_form_action, parse_hidden_fields, handle_saml_chain, login, save_session, load_session


class TestFollowRedirects:
    def test_follows_302_chain(self):
        session = MagicMock()
        r1 = MagicMock(status_code=302, headers={"Location": "https://a.com/step2"}, url="https://a.com/step1")
        r2 = MagicMock(status_code=302, headers={"Location": "https://b.com/step3"}, url="https://a.com/step2")
        r3 = MagicMock(status_code=200, headers={}, url="https://b.com/step3")
        session.get.side_effect = [r2, r3]

        result = follow_redirects(session, r1)
        assert result.url == "https://b.com/step3"
        assert session.get.call_count == 2

    def test_stops_on_200(self):
        session = MagicMock()
        r1 = MagicMock(status_code=200, headers={}, url="https://a.com")
        result = follow_redirects(session, r1)
        assert result.url == "https://a.com"
        assert session.get.call_count == 0

    def test_resolves_relative_location(self):
        session = MagicMock()
        r1 = MagicMock(status_code=302, headers={"Location": "/next"}, url="https://a.com/page")
        r2 = MagicMock(status_code=200, headers={}, url="https://a.com/next")
        session.get.return_value = r2
        result = follow_redirects(session, r1)
        session.get.assert_called_with("https://a.com/next", allow_redirects=False, timeout=30)

    def test_stops_after_max_hops(self):
        session = MagicMock()
        redirect = MagicMock(status_code=302, headers={"Location": "https://a.com/loop"}, url="https://a.com/loop")
        session.get.return_value = redirect
        result = follow_redirects(session, redirect, max_hops=5)
        assert session.get.call_count == 5
        assert result.status_code == 302


class TestParseHiddenFields:
    def test_parses_multiple_fields(self):
        html = '''
        <form action="/post">
            <input type="hidden" name="SAMLResponse" value="abc123==">
            <input type="hidden" name="RelayState" value="xyz">
        </form>
        '''
        fields = parse_hidden_fields(html)
        assert fields == {"SAMLResponse": "abc123==", "RelayState": "xyz"}

    def test_handles_html_entities(self):
        html = '<input type="hidden" name="token" value="a&amp;b">'
        fields = parse_hidden_fields(html)
        assert fields == {"token": "a&b"}

    def test_empty_value(self):
        html = '<input type="hidden" name="empty" value="">'
        fields = parse_hidden_fields(html)
        assert fields == {"empty": ""}

    def test_no_hidden_fields(self):
        html = '<input type="text" name="user" value="foo">'
        fields = parse_hidden_fields(html)
        assert fields == {}


class TestParseFormAction:
    def test_extracts_action(self):
        html = '<form method="post" action="https://example.com/saml">'
        assert parse_form_action(html) == "https://example.com/saml"

    def test_no_form(self):
        html = "<div>no form here</div>"
        assert parse_form_action(html) is None

    def test_html_entity_in_action(self):
        html = '<form action="https://example.com?a=1&amp;b=2">'
        assert parse_form_action(html) == "https://example.com?a=1&b=2"


class TestHandleSamlChain:
    def test_follows_saml_form_chain(self):
        session = MagicMock()

        # First hop: SAML form with hidden fields
        html1 = '''
        <form method="post" action="https://idp.example.com/saml">
            <input type="hidden" name="SAMLResponse" value="response1">
            <input type="hidden" name="RelayState" value="state1">
        </form>
        '''

        # Second hop: final page, no form
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.url = "https://app.example.com/home"
        resp2.text = "<html>Welcome</html>"
        resp2.headers = {}

        post_resp = MagicMock()
        post_resp.status_code = 200
        post_resp.url = "https://app.example.com/home"
        post_resp.text = "<html>Welcome</html>"
        post_resp.headers = {}
        session.post.return_value = post_resp

        result_html, result_url = handle_saml_chain(session, html1, "https://login.example.com/sso")
        session.post.assert_called_once_with(
            "https://idp.example.com/saml",
            data={"SAMLResponse": "response1", "RelayState": "state1"},
            allow_redirects=False,
            timeout=30,
        )

    def test_handles_redirect_after_post(self):
        session = MagicMock()

        html1 = '''
        <form action="https://a.com/post">
            <input type="hidden" name="token" value="abc">
        </form>
        '''

        # POST returns 302
        post_resp = MagicMock()
        post_resp.status_code = 302
        post_resp.headers = {"Location": "https://a.com/next"}
        post_resp.url = "https://a.com/post"

        # Redirect resolves to final page
        final_resp = MagicMock()
        final_resp.status_code = 200
        final_resp.headers = {}
        final_resp.url = "https://a.com/next"
        final_resp.text = "<html>done</html>"

        session.post.return_value = post_resp
        session.get.return_value = final_resp

        result_html, result_url = handle_saml_chain(session, html1, "https://start.com")
        assert result_url == "https://a.com/next"

    def test_no_form_returns_immediately(self):
        session = MagicMock()
        html = "<html>No form here</html>"
        result_html, result_url = handle_saml_chain(session, html, "https://page.com")
        assert result_html == html
        assert result_url == "https://page.com"
        assert session.post.call_count == 0


class TestLogin:
    """Test the full login chain with mocked HTTP responses."""

    def _build_mock_session(self):
        """Build a mock session that simulates the full redirect chain.

        The login flow makes these requests in order:
        1. GET hub.infomentor.se -> 200, HTML with oauth_token
        2. POST infomentor.se/swedish/production/mentor/ -> 200, HTML with SSO URL
        3. GET sso.infomentor.se/login.ashx?idp=stockholm_par -> 302 chain -> 200, HTML with Freja link
        4. GET login001.stockholm.se/NECSadc/freja/... -> 302 chain -> 200, Freja page
        5. (freja_login is mocked separately)
        6. GET freja_url (reload) -> 200, SAML form HTML
        7. POST saml endpoint -> 200, final page (no form)
        8. POST isauthenticated -> 200, "true"
        """
        session = MagicMock()
        session.headers = {}

        # Step 1: hub.infomentor.se
        hub_resp = MagicMock()
        hub_resp.status_code = 200
        hub_resp.headers = {}
        hub_resp.url = "https://hub.infomentor.se/"
        hub_resp.text = '<input type="hidden" name="oauth_token" value="token123">'

        # Step 2: POST to infomentor.se -> IdP page with SSO URL
        idp_resp = MagicMock()
        idp_resp.status_code = 200
        idp_resp.headers = {}
        idp_resp.url = "https://infomentor.se/swedish/production/mentor/"
        idp_resp.text = 'value="https://sso.infomentor.se/login.ashx?idp=stockholm_par"'

        # Step 3: GET SSO URL -> Stockholm login page with Freja link
        stockholm_resp = MagicMock()
        stockholm_resp.status_code = 200
        stockholm_resp.headers = {}
        stockholm_resp.url = "https://login001.stockholm.se/siteminderagent/forms/amedborgare.jsp"
        stockholm_resp.text = 'href="https://login001.stockholm.se/NECSadc/freja/b64startpage.jsp?startpage=abc"'

        # Step 4: GET Freja link -> Freja page
        freja_page_resp = MagicMock()
        freja_page_resp.status_code = 200
        freja_page_resp.headers = {}
        freja_page_resp.url = "https://login003.stockholm.se/NECSadcfreja/authenticate/NECSadcfreja?TYPE=1&TARGET=x"
        freja_page_resp.text = "<html>Freja page</html>"

        # Step 6: GET reload after APPROVED -> SAML form
        saml_resp = MagicMock()
        saml_resp.status_code = 200
        saml_resp.headers = {}
        saml_resp.url = "https://login003.stockholm.se/NECSadcfreja/authenticate/NECSadcfreja?TYPE=1&TARGET=x"
        saml_resp.text = '''
        <form action="https://sso.infomentor.se/saml">
            <input type="hidden" name="SAMLResponse" value="resp123">
        </form>
        '''

        # Step 7: POST SAML -> final page (no form)
        final_resp = MagicMock()
        final_resp.status_code = 200
        final_resp.headers = {}
        final_resp.url = "https://hub.infomentor.se/home"
        final_resp.text = "<html>Home</html>"

        # Step 8: POST isauthenticated -> true
        auth_check_resp = MagicMock()
        auth_check_resp.ok = True
        auth_check_resp.text = "true"

        # Wire up: GETs return different responses based on call order
        session.get.side_effect = [
            hub_resp,       # 1. GET hub.infomentor.se
            stockholm_resp, # 3. GET SSO URL (follow_redirects)
            freja_page_resp,# 4. GET Freja link (follow_redirects)
            saml_resp,      # 6. GET reload
        ]
        session.post.side_effect = [
            idp_resp,       # 2. POST oauth_token
            final_resp,     # 7. POST SAML form
            auth_check_resp,# 8. POST isauthenticated
        ]

        return session

    @patch("deformentor_cli.session.freja_login")
    def test_login_returns_session(self, mock_freja):
        session = self._build_mock_session()
        result = login("0001011234", _session=session)
        assert result is session

    @patch("deformentor_cli.session.freja_login")
    def test_login_calls_freja_with_correct_args(self, mock_freja):
        session = self._build_mock_session()
        login("0001011234", _session=session)
        mock_freja.assert_called_once_with(
            session,
            "https://login003.stockholm.se/NECSadcfreja/authenticate/NECSadcfreja?TYPE=1&TARGET=x",
            "0001011234",
        )

    @patch("deformentor_cli.session.freja_login")
    def test_login_verifies_authentication(self, mock_freja):
        session = self._build_mock_session()
        login("0001011234", _session=session)
        # Last POST should be the isauthenticated check
        last_post = session.post.call_args_list[-1]
        assert "isauthenticated" in last_post[0][0]


class TestLoginQuiet:
    @patch("deformentor_cli.session.freja_login")
    def test_login_prints_progress_by_default(self, mock_freja, capsys):
        session = TestLogin()._build_mock_session()
        login("0001011234", _session=session)
        captured = capsys.readouterr()
        assert "logging in" in captured.err.lower()

    @patch("deformentor_cli.session.freja_login")
    def test_login_suppresses_progress_when_quiet(self, mock_freja, capsys):
        session = TestLogin()._build_mock_session()
        login("0001011234", _session=session, quiet=True)
        captured = capsys.readouterr()
        assert "logging in" not in captured.err.lower()


class TestSessionPersistence:
    def test_save_and_load_roundtrip(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            session = MagicMock()
            cookie = http.cookiejar.Cookie(
                version=0, name="SMSESSION", value="abc123",
                port=None, port_specified=False,
                domain=".stockholm.se", domain_specified=True,
                domain_initial_dot=True,
                path="/", path_specified=True,
                secure=True, expires=None, discard=True,
                comment=None, comment_url=None,
                rest={"HttpOnly": "HttpOnly"},
            )
            jar = requests.cookies.RequestsCookieJar()
            jar.set_cookie(cookie)
            session.cookies = jar

            save_session(session, path)
            assert os.path.exists(path)

            new_sess = MagicMock()
            new_sess.cookies = requests.cookies.RequestsCookieJar()
            load_session(new_sess, path)
            assert any(c.name == "SMSESSION" for c in new_sess.cookies)
        finally:
            os.unlink(path)

    def test_load_nonexistent_returns_false(self):
        session = MagicMock()
        result = load_session(session, "/nonexistent/path.json")
        assert result is False

    def test_load_corrupt_json_returns_false(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            path = f.name
        try:
            session = MagicMock()
            result = load_session(session, path)
            assert result is False
        finally:
            os.unlink(path)


class TestSessionFilePermissions:
    def test_save_session_creates_file_with_0600(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            os.unlink(path)  # Remove so save_session creates it
            session = MagicMock()
            session.cookies = requests.cookies.RequestsCookieJar()
            save_session(session, path)
            mode = os.stat(path).st_mode & 0o777
            assert mode == 0o600
        finally:
            if os.path.exists(path):
                os.unlink(path)
