import json
from unittest.mock import MagicMock, patch

import pytest

from deformentor_cli.errors import FrejaError, FrejaRejectedError, FrejaTimeoutError
from deformentor_cli.freja import freja_login, _ensure_12_digits

FREJA_URL = (
    "https://login003.stockholm.se/NECSadcfreja/authenticate/NECSadcfreja"
    "?TYPE=33554433&REALMOID=06-abc&TARGET=-SM-https%3a%2f%2fexample.com"
)
PERSONNUMMER = "0001011234"


def _mock_session(poll_responses):
    """Create a mock session that returns given statuses on successive GETs.

    poll_responses: list of status strings. Each GET returns the next one.
    The POST (init) always returns 200 with empty body.
    """
    session = MagicMock()
    init_resp = MagicMock()
    init_resp.ok = True
    init_resp.text = ""
    session.post.return_value = init_resp

    get_responses = []
    for status in poll_responses:
        resp = MagicMock()
        resp.ok = True
        resp.text = json.dumps({"status": status})
        get_responses.append(resp)
    session.get.side_effect = get_responses
    return session


class TestFrejaLogin:
    def test_approved_after_polling(self):
        session = _mock_session(["STARTED", "DELIVERED_TO_MOBILE", "APPROVED"])
        freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)
        assert session.post.call_count == 1
        assert session.get.call_count == 3

    def test_init_url_strips_query_params_and_adds_century(self):
        session = _mock_session(["APPROVED"])
        freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)
        init_url = session.post.call_args[0][0]
        assert "TYPE=" not in init_url
        assert "action=init" in init_url
        assert "userInput=200001011234" in init_url

    def test_12_digit_personnummer_passed_through(self):
        session = _mock_session(["APPROVED"])
        freja_login(session, FREJA_URL, "200001011234", poll_interval=0)
        init_url = session.post.call_args[0][0]
        assert "userInput=200001011234" in init_url

    def test_poll_url_keeps_query_params(self):
        session = _mock_session(["APPROVED"])
        freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)
        poll_url = session.get.call_args[0][0]
        assert "TYPE=33554433" in poll_url
        assert "action=checkstatus" in poll_url

    def test_canceled_raises_rejected(self):
        session = _mock_session(["STARTED", "CANCELED"])
        with pytest.raises(FrejaRejectedError):
            freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)

    def test_expired_raises_timeout(self):
        session = _mock_session(["STARTED", "EXPIRED"])
        with pytest.raises(FrejaTimeoutError):
            freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)

    def test_error_raises_freja_error(self):
        session = _mock_session(["ERROR"])
        with pytest.raises(FrejaError):
            freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)

    def test_rp_canceled_raises_freja_error(self):
        session = _mock_session(["RP_CANCELED"])
        with pytest.raises(FrejaError):
            freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)

    @patch("deformentor_cli.freja.time")
    def test_timeout_raises_after_max_wait(self, mock_time):
        elapsed = [0.0]

        def fake_monotonic():
            val = elapsed[0]
            elapsed[0] += 3.0
            return val

        mock_time.monotonic = fake_monotonic
        mock_time.sleep = MagicMock()

        session = MagicMock()
        init_resp = MagicMock()
        init_resp.ok = True
        init_resp.text = ""
        session.post.return_value = init_resp

        poll_resp = MagicMock()
        poll_resp.ok = True
        poll_resp.text = json.dumps({"status": "STARTED"})
        session.get.return_value = poll_resp

        with pytest.raises(FrejaTimeoutError, match="timed out"):
            freja_login(session, FREJA_URL, PERSONNUMMER, timeout=5.0)

    def test_plain_text_status_response(self):
        session = MagicMock()
        init_resp = MagicMock()
        init_resp.ok = True
        init_resp.text = ""
        session.post.return_value = init_resp

        poll_resp = MagicMock()
        poll_resp.ok = True
        poll_resp.text = "APPROVED"
        session.get.return_value = poll_resp

        freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)

    def test_init_failure_raises(self):
        session = MagicMock()
        init_resp = MagicMock()
        init_resp.ok = False
        init_resp.status_code = 500
        session.post.return_value = init_resp

        with pytest.raises(FrejaError, match="initiate"):
            freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)

    def test_unknown_status_keeps_polling(self):
        session = _mock_session(["UNKNOWN", "STARTED", "APPROVED"])
        freja_login(session, FREJA_URL, PERSONNUMMER, poll_interval=0)
        assert session.get.call_count == 3


class TestEnsure12Digits:
    def test_12_digit_passed_through(self):
        assert _ensure_12_digits("200001011234") == "200001011234"

    def test_10_digit_young_gets_20_prefix(self):
        assert _ensure_12_digits("0001011234") == "200001011234"

    @patch("deformentor_cli.freja.datetime")
    def test_10_digit_old_gets_19_prefix(self, mock_datetime):
        mock_datetime.date.today.return_value = MagicMock(year=2026)
        assert _ensure_12_digits("9903041234") == "199903041234"

    @patch("deformentor_cli.freja.datetime")
    def test_cutoff_year_gets_20_prefix(self, mock_datetime):
        mock_datetime.date.today.return_value = MagicMock(year=2026)
        assert _ensure_12_digits("2601011234") == "202601011234"
