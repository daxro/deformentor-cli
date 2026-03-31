import io
import json
from datetime import date, timedelta

import pytest
from unittest.mock import patch, MagicMock


class TestGetSession:
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_returns_authenticated_session(self, mock_dotenv, mock_login):
        from deformentor_cli.cli import _get_session, SESSION_FILE

        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_session = MagicMock()
        mock_login.return_value = mock_session

        result = _get_session()

        assert result is mock_session
        mock_login.assert_called_once_with("200001011234", session_path=str(SESSION_FILE), quiet=False)

    @patch("deformentor_cli.cli.dotenv_values")
    def test_exits_on_missing_personnummer(self, mock_dotenv, capsys):
        from deformentor_cli.cli import _get_session

        mock_dotenv.return_value = {}

        with pytest.raises(SystemExit) as exc_info:
            _get_session()
        assert exc_info.value.code == 3


class TestGetSessionQuiet:
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_passes_quiet_to_login(self, mock_dotenv, mock_login):
        from deformentor_cli.cli import _get_session, SESSION_FILE
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_session = MagicMock()
        mock_login.return_value = mock_session
        _get_session(quiet=True)
        mock_login.assert_called_once_with("200001011234", session_path=str(SESSION_FILE), quiet=True)


class TestValidateDateFlag:
    def test_valid_date_passes(self):
        from deformentor_cli.cli import _validate_date_flag
        assert _validate_date_flag("2026-03-28", "--since") == "2026-03-28"

    def test_none_passes(self):
        from deformentor_cli.cli import _validate_date_flag
        assert _validate_date_flag(None, "--since") is None

    def test_all_returns_none(self):
        from deformentor_cli.cli import _validate_date_flag
        assert _validate_date_flag("all", "--since") is None

    def test_all_case_insensitive(self):
        from deformentor_cli.cli import _validate_date_flag
        assert _validate_date_flag("ALL", "--until") is None

    def test_invalid_format_exits(self):
        from deformentor_cli.cli import _validate_date_flag
        with pytest.raises(SystemExit):
            _validate_date_flag("28-03-2026", "--since")

    def test_partial_date_exits(self):
        from deformentor_cli.cli import _validate_date_flag
        with pytest.raises(SystemExit):
            _validate_date_flag("2026-03", "--since")

    def test_with_time_exits(self):
        from deformentor_cli.cli import _validate_date_flag
        with pytest.raises(SystemExit):
            _validate_date_flag("2026-03-28T12:00:00", "--since")

    def test_error_message_includes_flag_name(self, capsys):
        from deformentor_cli.cli import _validate_date_flag
        with pytest.raises(SystemExit):
            _validate_date_flag("bad", "--until")
        err = json.loads(capsys.readouterr().err)
        assert "--until" in err["message"]


class TestResolveSince:
    @patch("deformentor_cli.cli.date")
    def test_default_30_days_ago(self, mock_date):
        from deformentor_cli.cli import _resolve_since
        mock_date.today.return_value = date(2026, 3, 30)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        result = _resolve_since(None, {})
        assert result == "2026-02-28"

    @patch("deformentor_cli.cli.date")
    def test_env_var_overrides_default(self, mock_date):
        from deformentor_cli.cli import _resolve_since
        mock_date.today.return_value = date(2026, 3, 30)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        result = _resolve_since(None, {"DEFAULT_SINCE_DAYS": "7"})
        assert result == "2026-03-23"

    def test_explicit_date_overrides_all(self):
        from deformentor_cli.cli import _resolve_since
        result = _resolve_since("2026-01-15", {"DEFAULT_SINCE_DAYS": "7"})
        assert result == "2026-01-15"

    def test_all_returns_none(self):
        from deformentor_cli.cli import _resolve_since
        assert _resolve_since("all", {}) is None

    def test_all_case_insensitive(self):
        from deformentor_cli.cli import _resolve_since
        assert _resolve_since("ALL", {}) is None

    def test_non_integer_env_var_exits(self):
        from deformentor_cli.cli import _resolve_since
        with pytest.raises(SystemExit):
            _resolve_since(None, {"DEFAULT_SINCE_DAYS": "foo"})

    def test_zero_env_var_exits(self):
        from deformentor_cli.cli import _resolve_since
        with pytest.raises(SystemExit):
            _resolve_since(None, {"DEFAULT_SINCE_DAYS": "0"})

    def test_negative_env_var_exits(self):
        from deformentor_cli.cli import _resolve_since
        with pytest.raises(SystemExit):
            _resolve_since(None, {"DEFAULT_SINCE_DAYS": "-5"})


class TestResolveUntil:
    def test_none_returns_none(self):
        from deformentor_cli.cli import _resolve_until
        assert _resolve_until(None) is None

    def test_explicit_date(self):
        from deformentor_cli.cli import _resolve_until
        assert _resolve_until("2026-04-15") == "2026-04-15"

    def test_all_returns_none(self):
        from deformentor_cli.cli import _resolve_until
        assert _resolve_until("all") is None


class TestFilterChildren:
    def test_matches_firstname_case_insensitive(self):
        from deformentor_cli.cli import _filter_children
        data = [
            {"child": "Andersson, Astrid", "child_id": "1"},
            {"child": "Andersson, Nils", "child_id": "2"},
        ]
        result = _filter_children(data, "astrid")
        assert len(result) == 1
        assert result[0]["child_id"] == "1"

    def test_no_match_returns_empty(self):
        from deformentor_cli.cli import _filter_children
        data = [{"child": "Andersson, Astrid", "child_id": "1"}]
        result = _filter_children(data, "unknown")
        assert result == []

    def test_none_returns_all(self):
        from deformentor_cli.cli import _filter_children
        data = [
            {"child": "Andersson, Astrid", "child_id": "1"},
            {"child": "Andersson, Nils", "child_id": "2"},
        ]
        result = _filter_children(data, None)
        assert len(result) == 2


class TestFilterSince:
    def test_filters_items_before_date(self):
        from deformentor_cli.cli import _filter_items_since
        items = [
            {"date": "2026-03-30T06:07:04", "title": "new"},
            {"date": "2026-03-25T10:00:00", "title": "old"},
        ]
        result = _filter_items_since(items, "2026-03-28")
        assert len(result) == 1
        assert result[0]["title"] == "new"

    def test_since_is_inclusive(self):
        from deformentor_cli.cli import _filter_items_since
        items = [{"date": "2026-03-28T00:00:00", "title": "exact"}]
        result = _filter_items_since(items, "2026-03-28")
        assert len(result) == 1

    def test_day_only_date_included(self):
        from deformentor_cli.cli import _filter_items_since
        items = [{"date": "2026-03-28", "title": "day only"}]
        result = _filter_items_since(items, "2026-03-28")
        assert len(result) == 1

    def test_none_returns_all(self):
        from deformentor_cli.cli import _filter_items_since
        items = [{"date": "2026-01-01"}, {"date": "2026-12-31"}]
        result = _filter_items_since(items, None)
        assert len(result) == 2


class TestFilterUntil:
    def test_filters_items_after_date(self):
        from deformentor_cli.cli import _filter_items_until
        items = [
            {"date": "2026-03-30T06:07:04", "title": "future"},
            {"date": "2026-03-25T10:00:00", "title": "past"},
        ]
        result = _filter_items_until(items, "2026-03-28")
        assert len(result) == 1
        assert result[0]["title"] == "past"

    def test_until_is_inclusive(self):
        from deformentor_cli.cli import _filter_items_until
        items = [{"date": "2026-03-28T23:59:59", "title": "same day"}]
        result = _filter_items_until(items, "2026-03-28")
        assert len(result) == 1

    def test_day_only_date_included(self):
        from deformentor_cli.cli import _filter_items_until
        items = [{"date": "2026-03-28", "title": "day only"}]
        result = _filter_items_until(items, "2026-03-28")
        assert len(result) == 1

    def test_none_returns_all(self):
        from deformentor_cli.cli import _filter_items_until
        items = [{"date": "2026-01-01"}, {"date": "2026-12-31"}]
        result = _filter_items_until(items, None)
        assert len(result) == 2

    def test_contradictory_range_returns_empty(self):
        from deformentor_cli.cli import _filter_items_since, _filter_items_until
        items = [
            {"date": "2026-03-15"},
            {"date": "2026-03-20"},
            {"date": "2026-03-25"},
        ]
        filtered = _filter_items_since(items, "2026-04-01")
        filtered = _filter_items_until(filtered, "2026-03-01")
        assert filtered == []


class TestFilterType:
    def test_filters_by_type_name(self):
        from deformentor_cli.cli import _filter_items_by_type
        items = [
            {"date": "2026-03-30", "type": {"name": "attendance"}},
            {"date": "2026-03-29", "type": {"name": "news"}},
            {"date": "2026-03-28", "type": {"name": "attendance"}},
        ]
        result = _filter_items_by_type(items, "attendance")
        assert len(result) == 2
        assert all(i["type"]["name"] == "attendance" for i in result)

    def test_case_insensitive(self):
        from deformentor_cli.cli import _filter_items_by_type
        items = [
            {"date": "2026-03-30", "type": {"name": "attendance"}},
            {"date": "2026-03-29", "type": {"name": "news"}},
        ]
        result = _filter_items_by_type(items, "Attendance")
        assert len(result) == 1
        assert result[0]["type"]["name"] == "attendance"

    def test_none_returns_all(self):
        from deformentor_cli.cli import _filter_items_by_type
        items = [
            {"date": "2026-03-30", "type": {"name": "attendance"}},
            {"date": "2026-03-29", "type": {"name": "news"}},
        ]
        result = _filter_items_by_type(items, None)
        assert len(result) == 2


class TestNotificationsCommand:
    @patch("deformentor_cli.cli.fetch_all_notifications")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_applies_all_filters(self, mock_dotenv, mock_login, mock_fetch, capsys):
        from deformentor_cli.cli import _notifications
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = [
            {
                "child": "Andersson, Astrid",
                "child_id": "1",
                "notifications": [
                    {"date": "2026-03-30T06:07:04", "type": {"name": "attendance", "id": "1", "action": "X", "title": "Y"}},
                    {"date": "2026-03-30T05:00:00", "type": {"name": "news", "id": "2", "action": "X", "title": "Y"}},
                    {"date": "2026-03-01T00:00:00", "type": {"name": "attendance", "id": "3", "action": "X", "title": "Y"}},
                ],
            },
            {
                "child": "Andersson, Nils",
                "child_id": "2",
                "notifications": [
                    {"date": "2026-03-30T06:07:04", "type": {"name": "attendance", "id": "4", "action": "X", "title": "Y"}},
                ],
            },
        ]
        args = MagicMock()
        args.child = "Astrid"
        args.type = "attendance"
        args.since = "2026-03-15"
        args.until = None
        _notifications(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert len(output) == 1
        assert output[0]["child"] == "Andersson, Astrid"
        assert len(output[0]["notifications"]) == 1
        assert output[0]["notifications"][0]["type"]["id"] == "1"

    @patch("deformentor_cli.cli.date")
    @patch("deformentor_cli.cli.fetch_all_notifications")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_warns_on_unknown_type(self, mock_dotenv, mock_login, mock_fetch, mock_date, capsys):
        from deformentor_cli.cli import _notifications
        mock_date.today.return_value = date(2026, 3, 30)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = [
            {"child": "Andersson, Astrid", "child_id": "1", "notifications": []},
        ]
        args = MagicMock()
        args.child = None
        args.type = "bogus"
        args.since = None
        args.until = None
        _notifications(args)
        captured = capsys.readouterr()
        assert "not a known type" in captured.err.lower()

    @patch("deformentor_cli.cli._get_session")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_warns_on_unknown_type_before_auth(self, mock_dotenv, mock_session, capsys):
        from deformentor_cli.cli import _notifications
        mock_dotenv.return_value = {}
        mock_session.side_effect = SystemExit(3)
        args = MagicMock()
        args.child = None
        args.type = "bogus"
        args.since = None
        args.until = None
        with pytest.raises(SystemExit):
            _notifications(args)
        captured = capsys.readouterr()
        assert "not a known type" in captured.err.lower()

    @patch("deformentor_cli.cli.dotenv_values")
    def test_exits_when_since_after_until(self, mock_dotenv):
        from deformentor_cli.cli import _notifications
        mock_dotenv.return_value = {}
        args = MagicMock()
        args.since = "2026-04-01"
        args.until = "2026-03-01"
        args.type = None
        with pytest.raises(SystemExit) as exc_info:
            _notifications(args)
        assert exc_info.value.code == 2


class TestMessagesCommand:
    @patch("deformentor_cli.cli.date")
    @patch("deformentor_cli.cli.fetch_all_messages")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_outputs_json_to_stdout(self, mock_dotenv, mock_login, mock_fetch, mock_date, capsys):
        from deformentor_cli.cli import _messages

        mock_date.today.return_value = date(2026, 3, 30)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = [
            {
                "child": "Andersson, Astrid",
                "child_id": "5001001",
                "messages": [
                    {"id": "100", "subject": "Hej", "date": "2026-03-28"},
                ],
            },
        ]

        args = MagicMock()
        args.child = None
        args.since = None
        args.until = None
        _messages(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert len(output) == 1
        assert output[0]["child"] == "Andersson, Astrid"
        assert len(output[0]["messages"]) == 1

    @patch("deformentor_cli.cli.fetch_all_messages")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_applies_child_and_since_filters(self, mock_dotenv, mock_login, mock_fetch, capsys):
        from deformentor_cli.cli import _messages

        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = [
            {
                "child": "Andersson, Astrid",
                "child_id": "1",
                "messages": [
                    {"id": "1", "subject": "New", "date": "2026-03-30"},
                    {"id": "2", "subject": "Old", "date": "2026-03-01"},
                ],
            },
            {
                "child": "Andersson, Nils",
                "child_id": "2",
                "messages": [
                    {"id": "3", "subject": "Nils msg", "date": "2026-03-30"},
                ],
            },
        ]

        args = MagicMock()
        args.child = "Astrid"
        args.since = "2026-03-15"
        args.until = None
        _messages(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert len(output) == 1
        assert output[0]["child"] == "Andersson, Astrid"
        assert len(output[0]["messages"]) == 1
        assert output[0]["messages"][0]["subject"] == "New"

    @patch("deformentor_cli.cli.date")
    @patch("deformentor_cli.cli.fetch_all_messages")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_warns_when_child_filter_matches_nothing(self, mock_dotenv, mock_login, mock_fetch, mock_date, capsys):
        from deformentor_cli.cli import _messages

        mock_date.today.return_value = date(2026, 3, 30)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = [
            {"child": "Andersson, Astrid", "child_id": "1", "messages": []},
        ]

        args = MagicMock()
        args.child = "Unknown"
        args.since = None
        args.until = None
        _messages(args)

        captured = capsys.readouterr()
        assert "no child matching" in captured.err.lower()

    @patch("deformentor_cli.cli.dotenv_values")
    def test_exits_when_since_after_until(self, mock_dotenv):
        from deformentor_cli.cli import _messages
        mock_dotenv.return_value = {}
        args = MagicMock()
        args.since = "2026-04-01"
        args.until = "2026-03-01"
        with pytest.raises(SystemExit) as exc_info:
            _messages(args)
        assert exc_info.value.code == 2

    @patch("deformentor_cli.cli.date")
    @patch("deformentor_cli.cli.fetch_all_messages")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_warns_when_max_pages_without_all_pages(self, mock_dotenv, mock_login, mock_fetch, mock_date, capsys):
        from deformentor_cli.cli import _messages
        mock_date.today.return_value = date(2026, 3, 30)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = [
            {"child": "Andersson, Astrid", "child_id": "1", "messages": []},
        ]
        args = MagicMock()
        args.child = None
        args.since = None
        args.until = None
        args.all_pages = False
        args.max_pages = 10
        _messages(args)
        captured = capsys.readouterr()
        assert "--max-pages has no effect without --all-pages" in captured.err


class TestResolveAndSwitchChild:
    @patch("deformentor_cli.cli.switch_child")
    @patch("deformentor_cli.cli.get_children")
    def test_switches_to_matching_child(self, mock_children, mock_switch):
        from deformentor_cli.cli import _resolve_and_switch_child
        mock_children.return_value = [
            {"name": "Andersson, Astrid", "id": "5001001", "hybridMappingId": "m1", "selected": True},
            {"name": "Andersson, Nils", "id": "5002002", "hybridMappingId": "m2", "selected": False},
        ]
        session = MagicMock()
        _resolve_and_switch_child(session, "Astrid")
        mock_switch.assert_called_once_with(session, "5001001")

    @patch("deformentor_cli.cli.get_children")
    def test_exits_on_no_match(self, mock_children, capsys):
        from deformentor_cli.cli import _resolve_and_switch_child
        mock_children.return_value = [
            {"name": "Andersson, Astrid", "id": "5001001", "hybridMappingId": "m1", "selected": True},
        ]
        session = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            _resolve_and_switch_child(session, "Unknown")
        assert exc_info.value.code == 4

    @patch("deformentor_cli.cli.switch_child")
    @patch("deformentor_cli.cli.get_children")
    def test_case_insensitive(self, mock_children, mock_switch):
        from deformentor_cli.cli import _resolve_and_switch_child
        mock_children.return_value = [
            {"name": "Andersson, Astrid", "id": "5001001", "hybridMappingId": "m1", "selected": True},
        ]
        session = MagicMock()
        _resolve_and_switch_child(session, "astrid")
        mock_switch.assert_called_once_with(session, "5001001")

    @patch("deformentor_cli.cli.switch_child")
    @patch("deformentor_cli.cli.get_children")
    def test_warns_on_multiple_matches(self, mock_children, mock_switch, capsys):
        from deformentor_cli.cli import _resolve_and_switch_child
        mock_children.return_value = [
            {"name": "Andersson, Astrid", "id": "5001001", "hybridMappingId": "m1", "selected": True},
            {"name": "Andersson, Astrid Jr", "id": "9999", "hybridMappingId": "m3", "selected": False},
        ]
        session = MagicMock()
        _resolve_and_switch_child(session, "Astrid")
        captured = capsys.readouterr()
        assert "multiple children match" in captured.err.lower()
        mock_switch.assert_called_once_with(session, "5001001")


class TestAttendanceCommand:
    @patch("deformentor_cli.cli.get_attendance_detail")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_outputs_json_to_stdout(self, mock_dotenv, mock_login, mock_fetch, capsys):
        from deformentor_cli.cli import _attendance
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = {"id": "197608", "status": "Approved"}
        args = MagicMock()
        args.id = "197608"
        args.child = None
        _attendance(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["status"] == "Approved"

    @patch("deformentor_cli.cli._resolve_and_switch_child")
    @patch("deformentor_cli.cli.get_attendance_detail")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_switches_child_when_provided(self, mock_dotenv, mock_login, mock_fetch, mock_switch, capsys):
        from deformentor_cli.cli import _attendance
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = {"id": "197608"}
        args = MagicMock()
        args.id = "197608"
        args.child = "Astrid"
        _attendance(args)
        mock_switch.assert_called_once_with(mock_login.return_value, "Astrid")


class TestMaskPersonnummer:
    def test_masks_12_digit(self):
        from deformentor_cli.cli import _mask_personnummer
        assert _mask_personnummer("200001011234") == "0001****1234"

    def test_short_input_returns_as_is(self):
        from deformentor_cli.cli import _mask_personnummer
        assert _mask_personnummer("87032") == "87032"


class TestStatus:
    @patch("deformentor_cli.cli.get_children")
    @patch("deformentor_cli.cli.verify_authenticated")
    @patch("deformentor_cli.cli.new_session")
    @patch("deformentor_cli.cli.load_session")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_shows_valid_session_with_children(self, mock_dotenv, mock_load, mock_new_session, mock_verify, mock_children, capsys):
        from deformentor_cli.cli import _status
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_session = MagicMock()
        mock_new_session.return_value = mock_session
        mock_load.return_value = True
        mock_children.return_value = [
            {"name": "Andersson, Astrid", "id": "5001001", "hybridMappingId": "m1", "selected": True},
            {"name": "Andersson, Nils", "id": "5002002", "hybridMappingId": "m2", "selected": False},
        ]
        args = MagicMock()
        args.json_output = False
        _status(args)
        captured = capsys.readouterr()
        assert "0001****1234" in captured.out
        assert "valid" in captured.out.lower()
        assert "Astrid" in captured.out
        assert "Nils" in captured.out

    @patch("deformentor_cli.cli.dotenv_values")
    def test_shows_not_configured(self, mock_dotenv, capsys):
        from deformentor_cli.cli import _status
        mock_dotenv.return_value = {}
        args = MagicMock()
        args.json_output = False
        _status(args)
        captured = capsys.readouterr()
        assert "Not configured" in captured.out
        assert "deformentor setup" in captured.out

    @patch("deformentor_cli.cli.verify_authenticated")
    @patch("deformentor_cli.cli.new_session")
    @patch("deformentor_cli.cli.load_session")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_shows_expired_session(self, mock_dotenv, mock_load, mock_new_session, mock_verify, capsys):
        from deformentor_cli.cli import _status
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_new_session.return_value = MagicMock()
        mock_load.return_value = True
        mock_verify.side_effect = RuntimeError("not authenticated")
        args = MagicMock()
        args.json_output = False
        _status(args)
        captured = capsys.readouterr()
        assert "expired" in captured.out.lower()
        assert "re-authenticate" in captured.out.lower()

    @patch("deformentor_cli.cli.new_session")
    @patch("deformentor_cli.cli.load_session")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_shows_no_saved_session(self, mock_dotenv, mock_load, mock_new_session, capsys):
        from deformentor_cli.cli import _status
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_new_session.return_value = MagicMock()
        mock_load.return_value = False
        args = MagicMock()
        args.json_output = False
        _status(args)
        captured = capsys.readouterr()
        assert "none" in captured.out.lower()
        assert "start a session" in captured.out.lower()


class TestCalendarCommand:
    @patch("deformentor_cli.cli.get_calendar_event")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_outputs_json_to_stdout(self, mock_dotenv, mock_login, mock_fetch, capsys):
        from deformentor_cli.cli import _calendar
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = {"eventId": "12345", "title": "Studiedag"}
        args = MagicMock()
        args.id = "12345"
        args.child = None
        _calendar(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["title"] == "Studiedag"

    @patch("deformentor_cli.cli._resolve_and_switch_child")
    @patch("deformentor_cli.cli.get_calendar_event")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_switches_child_when_provided(self, mock_dotenv, mock_login, mock_fetch, mock_switch, capsys):
        from deformentor_cli.cli import _calendar
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = {"eventId": "12345"}
        args = MagicMock()
        args.id = "12345"
        args.child = "Astrid"
        _calendar(args)
        mock_switch.assert_called_once_with(mock_login.return_value, "Astrid")


class TestNewsCommand:
    @patch("deformentor_cli.cli.get_news_detail")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_outputs_json_to_stdout(self, mock_dotenv, mock_login, mock_fetch, capsys):
        from deformentor_cli.cli import _news
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = {"id": 1000001, "title": "Veckobrev", "content": "<p>Text</p>", "attachments": []}
        args = MagicMock()
        args.id = "1000001"
        args.child = None
        _news(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["title"] == "Veckobrev"

    @patch("deformentor_cli.cli.get_news_detail")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_exits_when_not_found(self, mock_dotenv, mock_login, mock_fetch, capsys):
        from deformentor_cli.cli import _news
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = None
        args = MagicMock()
        args.id = "9999"
        args.child = None
        with pytest.raises(SystemExit) as exc_info:
            _news(args)
        assert exc_info.value.code == 4
        captured = capsys.readouterr()
        err = json.loads(captured.err)
        assert err["error"] == "not_found"
        assert "--child" in err["message"]

    @patch("deformentor_cli.cli._resolve_and_switch_child")
    @patch("deformentor_cli.cli.get_news_detail")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_exits_with_child_context_message_when_child_provided(self, mock_dotenv, mock_login, mock_fetch, mock_switch, capsys):
        from deformentor_cli.cli import _news
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = None
        args = MagicMock()
        args.id = "9999"
        args.child = "felix"
        with pytest.raises(SystemExit) as exc_info:
            _news(args)
        assert exc_info.value.code == 4
        captured = capsys.readouterr()
        err = json.loads(captured.err)
        assert err["error"] == "not_found"
        assert "felix" in err["message"]
        assert "--child" not in err["message"]

    @patch("deformentor_cli.cli._resolve_and_switch_child")
    @patch("deformentor_cli.cli.get_news_detail")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_switches_child_when_provided(self, mock_dotenv, mock_login, mock_fetch, mock_switch, capsys):
        from deformentor_cli.cli import _news
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = {"id": 1000001, "title": "Test", "content": "", "attachments": []}
        args = MagicMock()
        args.id = "1000001"
        args.child = "Astrid"
        _news(args)
        mock_switch.assert_called_once_with(mock_login.return_value, "Astrid")


class TestMeetingCommand:
    @patch("deformentor_cli.cli.get_meeting_availabilities")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_outputs_json_to_stdout(self, mock_dotenv, mock_login, mock_fetch, capsys):
        from deformentor_cli.cli import _meeting
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = {"totalCount": 1, "totalPages": 1, "availabilities": [{"availabilityId": 3000001, "meetingType": "Utvecklingssamtal"}]}
        args = MagicMock()
        args.child = None
        _meeting(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["totalCount"] == 1
        assert output["availabilities"][0]["availabilityId"] == 3000001

    @patch("deformentor_cli.cli._resolve_and_switch_child")
    @patch("deformentor_cli.cli.get_meeting_availabilities")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_switches_child_when_provided(self, mock_dotenv, mock_login, mock_fetch, mock_switch, capsys):
        from deformentor_cli.cli import _meeting
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = {"totalCount": 0, "totalPages": 0, "availabilities": []}
        args = MagicMock()
        args.child = "Felix"
        _meeting(args)
        mock_switch.assert_called_once_with(mock_login.return_value, "Felix")


class TestAttachmentCommand:
    @patch("deformentor_cli.cli.get_attachment")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_writes_bytes_to_stdout(self, mock_dotenv, mock_login, mock_fetch):
        from io import BytesIO
        from unittest.mock import patch as mpatch
        from deformentor_cli.cli import _attachment
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = b"%PDF-1.4 test"
        args = MagicMock()
        args.url = "/Resources/Resource/Download/123?api=IM2"
        args.child = None
        buf = BytesIO()
        fake_stdout = MagicMock()
        fake_stdout.buffer = buf
        fake_stdout.isatty = lambda: False
        with mpatch("sys.stdout", fake_stdout):
            _attachment(args)
        assert buf.getvalue() == b"%PDF-1.4 test"

    @patch("deformentor_cli.cli._resolve_and_switch_child")
    @patch("deformentor_cli.cli.get_attachment")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_switches_child_when_provided(self, mock_dotenv, mock_login, mock_fetch, mock_switch):
        from io import BytesIO
        from unittest.mock import patch as mpatch
        from deformentor_cli.cli import _attachment
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = b"%PDF-1.4 test"
        args = MagicMock()
        args.url = "/Resources/Resource/Download/123?api=IM2"
        args.child = "Astrid"
        buf = BytesIO()
        fake_stdout = MagicMock()
        fake_stdout.buffer = buf
        fake_stdout.isatty = lambda: False
        with mpatch("sys.stdout", fake_stdout):
            _attachment(args)
        mock_switch.assert_called_once_with(mock_login.return_value, "Astrid")

    @patch("deformentor_cli.cli.get_attachment")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_exits_when_attachment_is_empty(self, mock_dotenv, mock_login, mock_fetch):
        from deformentor_cli.cli import _attachment
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = b""
        args = MagicMock()
        args.url = "/Resources/Resource/Download/123?api=IM2"
        args.child = None
        with pytest.raises(SystemExit) as exc_info:
            _attachment(args)
        assert exc_info.value.code == 4


class TestQuietMode:
    @patch("deformentor_cli.cli.date")
    @patch("deformentor_cli.cli.fetch_all_notifications")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_quiet_suppresses_progress(self, mock_dotenv, mock_login, mock_fetch, mock_date, capsys):
        from deformentor_cli.cli import _notifications
        mock_date.today.return_value = date(2026, 3, 30)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = []
        args = MagicMock()
        args.child = None
        args.type = None
        args.since = None
        args.until = None
        args.quiet = True
        _notifications(args)
        captured = capsys.readouterr()
        assert "Fetching" not in captured.err

    @patch("deformentor_cli.cli.date")
    @patch("deformentor_cli.cli.fetch_all_notifications")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_no_quiet_shows_progress(self, mock_dotenv, mock_login, mock_fetch, mock_date, capsys):
        from deformentor_cli.cli import _notifications
        mock_date.today.return_value = date(2026, 3, 30)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = []
        args = MagicMock()
        args.child = None
        args.type = None
        args.since = None
        args.until = None
        args.quiet = False
        _notifications(args)
        captured = capsys.readouterr()
        assert "Fetching" in captured.err


class TestNonInteractiveSetup:
    @patch("deformentor_cli.cli._print_status")
    @patch("deformentor_cli.cli.login")
    def test_reads_env_var_when_no_tty(self, mock_login, mock_print_status, monkeypatch, capsys, tmp_path):
        from deformentor_cli.cli import _setup
        config_file = tmp_path / "config.env"
        monkeypatch.setattr("deformentor_cli.cli.CONFIG_FILE", config_file)
        monkeypatch.setattr("deformentor_cli.cli.CONFIG_DIR", tmp_path)
        monkeypatch.setenv("PERSONNUMMER", "200001011234")
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        _setup()
        mock_login.assert_called_once()
        assert "200001011234" in config_file.read_text()

    def test_fails_without_env_var_when_no_tty(self, monkeypatch, capsys):
        from deformentor_cli.cli import _setup
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        monkeypatch.delenv("PERSONNUMMER", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            _setup()
        assert exc_info.value.code == 2
        err = json.loads(capsys.readouterr().err)
        assert err["error"] == "setup_required"


class TestStatusJson:
    @patch("deformentor_cli.cli.get_children")
    @patch("deformentor_cli.cli.verify_authenticated")
    @patch("deformentor_cli.cli.new_session")
    @patch("deformentor_cli.cli.load_session")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_json_output_with_valid_session(self, mock_dotenv, mock_load, mock_new_session, mock_verify, mock_children, capsys):
        from deformentor_cli.cli import _status
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_new_session.return_value = MagicMock()
        mock_load.return_value = True
        mock_children.return_value = [
            {"name": "Doe, Jane", "id": "123", "hybridMappingId": "h1", "selected": True}
        ]
        args = MagicMock()
        args.json_output = True
        _status(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["configured"] is True
        assert data["session"] == "valid"
        assert len(data["children"]) == 1
        assert data["children"][0]["id"] == "123"

    @patch("deformentor_cli.cli.verify_authenticated")
    @patch("deformentor_cli.cli.new_session")
    @patch("deformentor_cli.cli.load_session")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_json_output_expired_session(self, mock_dotenv, mock_load, mock_new_session, mock_verify, capsys):
        from deformentor_cli.cli import _status
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_new_session.return_value = MagicMock()
        mock_load.return_value = True
        mock_verify.side_effect = RuntimeError("expired")
        args = MagicMock()
        args.json_output = True
        _status(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["session"] == "expired"

    @patch("deformentor_cli.cli.dotenv_values")
    def test_json_output_not_configured(self, mock_dotenv, capsys):
        from deformentor_cli.cli import _status
        mock_dotenv.return_value = {}
        args = MagicMock()
        args.json_output = True
        _status(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["configured"] is False
        assert data["session"] is None



class TestFieldsFilter:
    def test_filter_fields_flat_dict(self):
        from deformentor_cli.cli import _filter_fields
        data = {"id": "123", "title": "Hello", "content": "<p>Long</p>", "extra": "data"}
        result = _filter_fields(data, ["id", "title"])
        assert result == {"id": "123", "title": "Hello"}

    def test_filter_fields_list_of_dicts(self):
        from deformentor_cli.cli import _filter_fields
        data = [
            {"id": "1", "name": "Alice", "extra": "x"},
            {"id": "2", "name": "Bob", "extra": "y"},
        ]
        result = _filter_fields(data, ["id", "name"])
        assert result == [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}]

    def test_filter_fields_nested_structure(self):
        from deformentor_cli.cli import _filter_fields
        data = [
            {"child": "A", "notifications": [{"date": "2026-03-30", "type": {"name": "news", "id": "1"}}]}
        ]
        result = _filter_fields(data, ["child", "notifications.date", "notifications.type.name"])
        assert result[0]["child"] == "A"
        assert result[0]["notifications"][0]["date"] == "2026-03-30"
        assert result[0]["notifications"][0]["type"]["name"] == "news"
        assert "id" not in result[0]["notifications"][0]["type"]

    def test_filter_fields_none_returns_original(self):
        from deformentor_cli.cli import _filter_fields
        data = {"id": "123", "title": "Hello"}
        result = _filter_fields(data, None)
        assert result == data


class TestHelpExamples:
    def test_main_help_has_examples(self, capsys):
        from deformentor_cli.cli import main
        import sys
        with pytest.raises(SystemExit):
            sys.argv = ["deformentor", "--help"]
            main()
        captured = capsys.readouterr()
        assert "examples:" in captured.out.lower()

    def test_notifications_help_has_examples(self, capsys):
        from deformentor_cli.cli import main
        import sys
        with pytest.raises(SystemExit):
            sys.argv = ["deformentor", "notifications", "--help"]
            main()
        captured = capsys.readouterr()
        assert "examples:" in captured.out.lower()


class TestEmitError:
    def test_writes_json_to_stderr(self, capsys):
        from deformentor_cli.errors import emit_error
        with pytest.raises(SystemExit) as exc_info:
            emit_error("auth_expired", "Session expired", exit_code=3)
        assert exc_info.value.code == 3
        err = json.loads(capsys.readouterr().err)
        assert err["error"] == "auth_expired"
        assert err["message"] == "Session expired"

    def test_default_exit_code_is_1(self, capsys):
        from deformentor_cli.errors import emit_error
        with pytest.raises(SystemExit) as exc_info:
            emit_error("unknown", "Something broke")
        assert exc_info.value.code == 1


class TestColorSafety:
    def test_print_logo_no_ansi_when_no_color_env(self, capsys, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        from deformentor_cli import cli as _cli
        import importlib
        importlib.reload(_cli)
        _cli.print_logo()
        captured = capsys.readouterr()
        assert "\033[" not in captured.err

    def test_print_logo_no_ansi_when_term_dumb(self, capsys, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        monkeypatch.delenv("NO_COLOR", raising=False)
        from deformentor_cli.cli import print_logo
        print_logo()
        captured = capsys.readouterr()
        assert "\033[" not in captured.err

    def test_should_use_color_false_when_no_color_set(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "")
        from deformentor_cli.cli import _should_use_color
        assert _should_use_color() is False

    def test_should_use_color_false_when_term_dumb(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        from deformentor_cli.cli import _should_use_color
        assert _should_use_color() is False



class TestGetStatusExceptionHandling:
    @patch("deformentor_cli.cli.get_children")
    @patch("deformentor_cli.cli.verify_authenticated")
    @patch("deformentor_cli.cli.new_session")
    @patch("deformentor_cli.cli.load_session")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_children_fetch_failure_returns_empty_children(self, mock_dotenv, mock_load, mock_new_session, mock_verify, mock_children):
        import requests
        from deformentor_cli.cli import _get_status
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_new_session.return_value = MagicMock()
        mock_load.return_value = True
        mock_children.side_effect = requests.ConnectionError("network error")
        status = _get_status()
        assert status["session"] == "valid"
        assert status["children"] == []

    @patch("deformentor_cli.cli.get_children")
    @patch("deformentor_cli.cli.verify_authenticated")
    @patch("deformentor_cli.cli.new_session")
    @patch("deformentor_cli.cli.load_session")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_unexpected_error_propagates(self, mock_dotenv, mock_load, mock_new_session, mock_verify, mock_children):
        from deformentor_cli.cli import _get_status
        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_new_session.return_value = MagicMock()
        mock_load.return_value = True
        mock_children.side_effect = KeyError("unexpected")
        with pytest.raises(KeyError):
            _get_status()


class TestArgparseErrorFormat:
    def test_invalid_subcommand_emits_json_error(self, capsys):
        import sys
        from deformentor_cli.cli import main
        sys.argv = ["deformentor", "nonexistent"]
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        err_lines = [l for l in captured.err.strip().splitlines() if l.startswith("{")]
        assert len(err_lines) >= 1
        err = json.loads(err_lines[-1])
        assert err["error"] == "usage_error"
        assert "message" in err

    def test_missing_required_arg_emits_json_error(self, capsys):
        import sys
        from deformentor_cli.cli import main
        sys.argv = ["deformentor", "calendar"]
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        err_lines = [l for l in captured.err.strip().splitlines() if l.startswith("{")]
        assert len(err_lines) >= 1
        err = json.loads(err_lines[-1])
        assert err["error"] == "usage_error"


class TestVersion:
    def test_version_matches_pyproject(self):
        from importlib.metadata import version
        from deformentor_cli.cli import _get_version
        assert _get_version() == version("deformentor-cli")



class TestHelpOutput:
    def test_no_args_prints_help_to_stdout(self, capsys):
        import sys as _sys
        from deformentor_cli.cli import main
        with pytest.raises(SystemExit) as exc_info:
            _sys.argv = ["deformentor"]
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "usage:" in captured.out.lower() or "commands:" in captured.out.lower()

    def test_logo_still_goes_to_stderr(self, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        from deformentor_cli.cli import print_logo, _LOGO_LINES
        print_logo(use_color=False)
        captured = capsys.readouterr()
        # logo content (ASCII art) goes to stderr, not stdout
        assert len(captured.err.strip()) > 0
        assert captured.out == ""


class TestDebugFlag:
    def test_debug_enables_http_logging(self, capsys, monkeypatch):
        import logging
        from deformentor_cli.cli import _configure_debug
        _configure_debug()
        logger = logging.getLogger("urllib3")
        assert logger.level == logging.DEBUG


class TestNoInputFlag:
    @patch("deformentor_cli.cli.login")
    def test_setup_no_input_uses_env_var(self, mock_login, monkeypatch, capsys):
        from deformentor_cli.cli import _setup
        monkeypatch.setenv("PERSONNUMMER", "200001011234")
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
        mock_login.return_value = MagicMock()
        with patch("deformentor_cli.cli._get_status") as mock_status, \
             patch("deformentor_cli.cli._print_status"):
            mock_status.return_value = {"configured": True, "personnummer": "0001****1234", "session": "valid", "children": []}
            _setup(quiet=True, no_input=True)
        mock_login.assert_called_once()


class TestGlobalFlagPosition:
    """Global flags (--debug, --no-input, etc.) must work both before and after the subcommand."""

    @pytest.mark.parametrize("flag,attr,value", [
        ("--debug", "debug", True),
        ("--no-input", "no_input", True),
    ])
    @pytest.mark.parametrize("position", ["before", "after"])
    @patch("deformentor_cli.cli.fetch_all_notifications")
    @patch("deformentor_cli.cli.login")
    @patch("deformentor_cli.cli.dotenv_values")
    def test_flag_propagated(self, mock_dotenv, mock_login, mock_fetch,
                             position, flag, attr, value, monkeypatch):
        import sys as _sys
        from deformentor_cli.cli import main

        mock_dotenv.return_value = {"PERSONNUMMER": "200001011234"}
        mock_login.return_value = MagicMock()
        mock_fetch.return_value = []

        if position == "before":
            argv = ["deformentor", flag, "notifications"]
        else:
            argv = ["deformentor", "notifications", flag]
        monkeypatch.setattr(_sys, "argv", argv)

        captured_args = {}

        original_notifications = None
        import deformentor_cli.cli as _cli_mod
        original_notifications = _cli_mod._notifications

        def spy_notifications(args):
            captured_args["args"] = args
            return original_notifications(args)

        monkeypatch.setattr(_cli_mod, "_notifications", spy_notifications)
        main()
        assert getattr(captured_args["args"], attr) == value


class TestResetCommand:
    def test_reset_deletes_files(self, tmp_path, capsys):
        from deformentor_cli.cli import _reset

        config = tmp_path / "config.env"
        session = tmp_path / "session.json"
        config.write_text("PERSONNUMMER=200001011234\n")
        session.write_text("{}")

        args = MagicMock()
        args.quiet = False

        with patch("deformentor_cli.cli.CONFIG_FILE", config), \
             patch("deformentor_cli.cli.SESSION_FILE", session):
            _reset(args)

        assert not config.exists()
        assert not session.exists()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["reset"] is True
        assert len(data["deleted"]) == 2
        assert data["failed"] == []
        assert "Deleted" in captured.err

    def test_reset_already_clean(self, tmp_path, capsys):
        from deformentor_cli.cli import _reset

        config = tmp_path / "config.env"
        session = tmp_path / "session.json"

        args = MagicMock()
        args.quiet = False

        with patch("deformentor_cli.cli.CONFIG_FILE", config), \
             patch("deformentor_cli.cli.SESSION_FILE", session):
            _reset(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["reset"] is True
        assert data["deleted"] == []
        assert data["failed"] == []
        assert "Nothing to reset" in captured.err

    def test_reset_quiet_suppresses_stderr(self, tmp_path, capsys):
        from deformentor_cli.cli import _reset

        config = tmp_path / "config.env"
        session = tmp_path / "session.json"
        config.write_text("PERSONNUMMER=200001011234\n")

        args = MagicMock()
        args.quiet = True

        with patch("deformentor_cli.cli.CONFIG_FILE", config), \
             patch("deformentor_cli.cli.SESSION_FILE", session):
            _reset(args)

        captured = capsys.readouterr()
        assert captured.err == ""
        data = json.loads(captured.out)
        assert data["reset"] is True

    def test_reset_permission_error_exits_nonzero(self, tmp_path, capsys):
        from deformentor_cli.cli import _reset

        config = tmp_path / "config.env"
        session = tmp_path / "session.json"
        config.write_text("PERSONNUMMER=200001011234\n")

        args = MagicMock()
        args.quiet = False

        def unlink_raises():
            raise OSError("Permission denied")

        with patch("deformentor_cli.cli.CONFIG_FILE", config), \
             patch("deformentor_cli.cli.SESSION_FILE", session), \
             patch.object(type(config), "unlink", side_effect=OSError("Permission denied")):
            with pytest.raises(SystemExit) as exc:
                _reset(args)
            assert exc.value.code == 1

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data["failed"]) == 1
        assert "Failed to delete" in captured.err
