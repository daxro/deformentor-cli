import json
from unittest.mock import MagicMock

import pytest

from deformentor_cli.api import get_children, switch_child, get_notifications, get_messages, get_attendance_detail, get_calendar_event, get_news_detail, get_meeting_availabilities, fetch_all_notifications, fetch_all_messages, get_attachment
from deformentor_cli.api import _normalize_type_name, _extract_id_from_url, _normalize_notification, _normalize_message, _normalize_message_summary


MOCK_HOME_DATA = {
    "account": {
        "currentUser": {"id": "1234567", "name": "Andersson, Erik"},
        "pupils": [
            {
                "name": "Andersson, Astrid",
                "id": "2000010100",
                "hybridMappingId": "STO-1001|2000010100|NEMANDI_SKOLI",
                "selected": True,
                "switchPupilUrl": "https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/5001001",
            },
            {
                "name": "Andersson, Nils",
                "id": "2100020200",
                "hybridMappingId": "STO-1002|2100020200|NEMANDI_SKOLI",
                "selected": False,
                "switchPupilUrl": "https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/5002002",
            },
        ],
    }
}

MOCK_HUB_HTML = f"""
<html>
<script>
IMHome.home.homeData = {json.dumps(MOCK_HOME_DATA)};
IMHome.home.init();
</script>
</html>
"""


class TestGetChildren:
    def test_parses_children_from_hub_html(self):
        session = MagicMock()
        resp = MagicMock()
        resp.text = MOCK_HUB_HTML
        session.get.return_value = resp

        children = get_children(session)

        assert len(children) == 2
        assert children[0] == {
            "name": "Andersson, Astrid",
            "id": "5001001",
            "hybridMappingId": "STO-1001|2000010100|NEMANDI_SKOLI",
            "selected": True,
        }
        assert children[1] == {
            "name": "Andersson, Nils",
            "id": "5002002",
            "hybridMappingId": "STO-1002|2100020200|NEMANDI_SKOLI",
            "selected": False,
        }

    def test_requests_hub_page(self):
        session = MagicMock()
        resp = MagicMock()
        resp.text = MOCK_HUB_HTML
        session.get.return_value = resp

        get_children(session)

        url = session.get.call_args[0][0]
        assert "hub.infomentor.se" in url

    def test_raises_on_missing_home_data(self):
        session = MagicMock()
        resp = MagicMock()
        resp.text = "<html>no data here</html>"
        session.get.return_value = resp

        with pytest.raises(Exception, match="homeData"):
            get_children(session)


class TestSwitchChild:
    def test_calls_correct_url(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 500
        session.get.return_value = resp

        switch_child(session, "5001001")

        url = session.get.call_args[0][0]
        assert "/Account/PupilSwitcher/SwitchPupil/5001001" in url

    def test_does_not_raise_on_500(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 500
        session.get.return_value = resp

        switch_child(session, "5001001")  # should not raise

    def test_sends_ajax_header(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 500
        session.get.return_value = resp

        switch_child(session, "5001001")

        headers = session.get.call_args[1].get("headers", {})
        assert headers.get("X-Requested-With") == "XMLHttpRequest"

    def test_raises_on_non_500_error(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 401
        session.get.return_value = resp

        with pytest.raises(Exception, match="unexpected status 401"):
            switch_child(session, "5001001")


class TestGetNotifications:
    def test_posts_to_correct_url(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"notifications": []}
        session.post.return_value = resp

        get_notifications(session)

        url = session.post.call_args[0][0]
        assert "/notificationApp/notificationApp/getNotifications" in url

    def test_returns_notifications_list(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"notifications": [{"id": 1}]}
        session.post.return_value = resp

        result = get_notifications(session)
        assert result == [{"id": 1}]

    def test_sends_ajax_header(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"notifications": []}
        session.post.return_value = resp

        get_notifications(session)

        headers = session.post.call_args[1].get("headers", {})
        assert headers.get("X-Requested-With") == "XMLHttpRequest"

    def test_raises_on_http_error(self):
        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("401 Unauthorized")
        session.post.return_value = resp

        with pytest.raises(Exception, match="401"):
            get_notifications(session)


class TestGetMessagesAllPages:
    def test_fetches_all_pages_when_requested(self):
        session = MagicMock()
        page1_resp = MagicMock()
        page1_resp.json.return_value = {"items": [{"id": 1, "messageSubject": "A", "timeSent": "2026-03-28"}], "page": 0, "more": True}
        page1_resp.raise_for_status = MagicMock()
        page2_resp = MagicMock()
        page2_resp.json.return_value = {"items": [{"id": 2, "messageSubject": "B", "timeSent": "2026-03-27"}], "page": 1, "more": False}
        page2_resp.raise_for_status = MagicMock()
        session.post.side_effect = [page1_resp, page2_resp]

        result = get_messages(session, fetch_all_pages=True)
        assert len(result) == 2
        assert session.post.call_count == 2

    def test_respects_page_limit(self):
        session = MagicMock()
        page1_resp = MagicMock()
        page1_resp.json.return_value = {"items": [{"id": 1, "messageSubject": "A", "timeSent": "2026-03-28"}], "page": 0, "more": True}
        page1_resp.raise_for_status = MagicMock()
        page2_resp = MagicMock()
        page2_resp.json.return_value = {"items": [{"id": 2, "messageSubject": "B", "timeSent": "2026-03-27"}], "page": 1, "more": True}
        page2_resp.raise_for_status = MagicMock()
        session.post.side_effect = [page1_resp, page2_resp]

        result = get_messages(session, fetch_all_pages=True, max_pages=2)
        assert len(result) == 2
        assert session.post.call_count == 2

    def test_default_fetches_single_page(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": [{"id": 1, "messageSubject": "A", "timeSent": "2026-03-28"}], "page": 0, "more": True}
        resp.raise_for_status = MagicMock()
        session.post.return_value = resp

        result = get_messages(session)
        assert len(result) == 1
        assert session.post.call_count == 1


class TestGetMessages:
    def test_posts_with_page_param(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": [], "page": 0, "more": False}
        session.post.return_value = resp

        get_messages(session)

        kwargs = session.post.call_args[1]
        assert kwargs["json"] == {"page": 1}

    def test_posts_to_correct_url(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": [], "page": 0, "more": False}
        session.post.return_value = resp

        get_messages(session)

        url = session.post.call_args[0][0]
        assert "/Message/Message/GetMessages" in url

    def test_returns_items_list(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": [{"id": 123, "messageSubject": "Hello"}], "page": 0, "more": False}
        session.post.return_value = resp

        result = get_messages(session)
        assert result == [{"id": 123, "messageSubject": "Hello"}]

    def test_raises_on_http_error(self):
        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("401 Unauthorized")
        session.post.return_value = resp

        with pytest.raises(Exception, match="401"):
            get_messages(session)

    def test_warns_when_more_pages_exist(self, capsys):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": [{"id": 1, "messageSubject": "Test", "timeSent": "2026-03-28"}], "page": 0, "more": True}
        resp.raise_for_status = MagicMock()
        session.post.return_value = resp

        get_messages(session)

        captured = capsys.readouterr()
        assert "additional message pages" in captured.err.lower()

    def test_no_warning_when_no_more_pages(self, capsys):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": [], "page": 0, "more": False}
        resp.raise_for_status = MagicMock()
        session.post.return_value = resp

        get_messages(session)

        captured = capsys.readouterr()
        assert "additional" not in captured.err.lower()


class TestNormalizeTypeName:
    def test_lowercases(self):
        assert _normalize_type_name("Attendance") == "attendance"

    def test_strips_version_suffix(self):
        assert _normalize_type_name("CalendarV2") == "calendar"

    def test_already_lowercase(self):
        assert _normalize_type_name("news") == "news"

    def test_higher_version(self):
        assert _normalize_type_name("SomethingV12") == "something"


class TestExtractIdFromUrl:
    def test_news_url(self):
        assert _extract_id_from_url("/#/communication/news/1000001") == "1000001"

    def test_attendance_url(self):
        assert _extract_id_from_url("/#/attendance/tab/leaveRequests/show/197608") == "197608"

    def test_calendar_url_with_event_id(self):
        assert _extract_id_from_url("/#/calendarv2/tab/month?eventId=12345") == "12345"

    def test_meeting_url_returns_none(self):
        assert _extract_id_from_url("#/meeting") is None

    def test_empty_url_returns_none(self):
        assert _extract_id_from_url("") is None

    def test_none_url_returns_none(self):
        assert _extract_id_from_url(None) is None

    def test_alphanumeric_id(self):
        assert _extract_id_from_url("/#/communication/news/abc123") == "abc123"

    def test_uuid_id(self):
        assert _extract_id_from_url("/#/attendance/tab/leaveRequests/show/550e8400-e29b") == "550e8400-e29b"


class TestNormalizeNotification:
    def test_maps_fields_correctly(self):
        raw = {
            "orderDate": "2026-03-30T06:07:04",
            "appType": "Attendance",
            "type": "LeaveRequestUpdated",
            "title": "Ledighetsansökan har uppdaterats",
            "url": "/#/attendance/tab/leaveRequests/show/197608",
        }
        result = _normalize_notification(raw)
        assert result == {
            "date": "2026-03-30T06:07:04",
            "type": {
                "name": "attendance",
                "id": "197608",
                "action": "LeaveRequestUpdated",
                "title": "Ledighetsansökan har uppdaterats",
            },
        }

    def test_calendar_v2_normalized(self):
        raw = {
            "orderDate": "2026-03-28T10:00:00",
            "appType": "CalendarV2",
            "type": "CalendarV2EventCreated",
            "title": "Nytt kalenderevent",
            "url": "/#/calendarv2/tab/month?eventId=55555",
        }
        result = _normalize_notification(raw)
        assert result["type"]["name"] == "calendar"
        assert result["type"]["id"] == "55555"
        assert result["type"]["action"] == "CalendarV2EventCreated"

    def test_meeting_has_null_id(self):
        raw = {
            "orderDate": "2026-03-25T12:00:00",
            "appType": "Meeting",
            "type": "MeetingCreated",
            "title": "Nytt möte",
            "url": "#/meeting",
        }
        result = _normalize_notification(raw)
        assert result["type"]["id"] is None


class TestNormalizeMessage:
    def test_maps_fields_correctly(self):
        raw = {
            "id": 11880746,
            "messageSubject": "Viktig information",
            "timeSent": "2025-10-20",
        }
        result = _normalize_message(raw)
        assert result == {
            "date": "2025-10-20T00:00:00",
            "type": {
                "name": "message",
                "id": "11880746",
                "action": None,
                "title": "Viktig information",
            },
        }

    def test_date_anchored_to_midnight(self):
        raw = {"id": 1, "messageSubject": "Test", "timeSent": "2026-01-15"}
        result = _normalize_message(raw)
        assert result["date"] == "2026-01-15T00:00:00"


class TestFetchAllNotifications:
    def _mock_session(self):
        """Build a mock session simulating the full fetch flow."""
        session = MagicMock()

        home_data = {
            "account": {
                "currentUser": {"id": "1234567", "name": "Andersson, Erik"},
                "pupils": [
                    {
                        "name": "Andersson, Astrid",
                        "id": "2000010100",
                        "hybridMappingId": "map-111",
                        "selected": True,
                        "switchPupilUrl": "https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/5001001",
                    },
                    {
                        "name": "Andersson, Nils",
                        "id": "2100020200",
                        "hybridMappingId": "map-222",
                        "selected": False,
                        "switchPupilUrl": "https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/5002002",
                    },
                ],
            }
        }

        hub_html = f"<script>IMHome.home.homeData = {json.dumps(home_data)};</script>"

        raw_notifications = [
            {
                "pupilSourceId": "map-111",
                "appType": "Attendance",
                "type": "LeaveRequestUpdated",
                "title": "Ledighetsansökan",
                "orderDate": "2026-03-30T06:07:04",
                "url": "/#/attendance/tab/leaveRequests/show/197608",
            },
            {
                "pupilSourceId": "map-222",
                "appType": "News",
                "type": "NewsItem",
                "title": "Skolnyhet",
                "orderDate": "2026-03-29T10:00:00",
                "url": "/#/communication/news/1000001",
            },
        ]

        felix_messages = [
            {"id": 100, "messageSubject": "Hej Astrid", "timeSent": "2026-03-28"},
        ]
        viggo_messages = [
            {"id": 200, "messageSubject": "Hej Nils", "timeSent": "2026-03-27"},
        ]

        hub_resp = MagicMock()
        hub_resp.text = hub_html

        switch_resp = MagicMock()
        switch_resp.status_code = 500

        session.get.side_effect = [hub_resp, switch_resp, switch_resp]

        notif_resp = MagicMock()
        notif_resp.json.return_value = {"notifications": raw_notifications}

        felix_msg_resp = MagicMock()
        felix_msg_resp.json.return_value = {"items": felix_messages, "page": 0, "more": False}

        viggo_msg_resp = MagicMock()
        viggo_msg_resp.json.return_value = {"items": viggo_messages, "page": 0, "more": False}

        session.post.side_effect = [notif_resp, felix_msg_resp, viggo_msg_resp]

        return session

    def test_returns_all_children(self):
        session = self._mock_session()
        result = fetch_all_notifications(session)
        assert len(result) == 2
        assert result[0]["child"] == "Andersson, Astrid"
        assert result[0]["child_id"] == "5001001"
        assert result[1]["child"] == "Andersson, Nils"
        assert result[1]["child_id"] == "5002002"

    def test_partitions_notifications_by_child(self):
        session = self._mock_session()
        result = fetch_all_notifications(session)
        felix_types = [n["type"]["name"] for n in result[0]["notifications"]]
        viggo_types = [n["type"]["name"] for n in result[1]["notifications"]]
        assert "attendance" in felix_types
        assert "news" in viggo_types
        assert "news" not in felix_types
        assert "attendance" not in viggo_types

    def test_merges_messages_with_notifications(self):
        session = self._mock_session()
        result = fetch_all_notifications(session)
        felix_types = [n["type"]["name"] for n in result[0]["notifications"]]
        assert "message" in felix_types
        assert "attendance" in felix_types

    def test_sorted_by_date_descending(self):
        session = self._mock_session()
        result = fetch_all_notifications(session)
        for child_data in result:
            dates = [n["date"] for n in child_data["notifications"]]
            assert dates == sorted(dates, reverse=True)

    def test_switches_child_for_messages(self):
        session = self._mock_session()
        fetch_all_notifications(session)
        assert session.get.call_count == 3
        switch_urls = [c[0][0] for c in session.get.call_args_list[1:]]
        assert any("5001001" in u for u in switch_urls)
        assert any("5002002" in u for u in switch_urls)

    def test_unknown_pupil_source_id_excluded_from_results(self):
        session = MagicMock()
        home_data = {
            "account": {
                "currentUser": {"id": "1", "name": "Test"},
                "pupils": [
                    {
                        "name": "Andersson, Astrid",
                        "id": "2000010100",
                        "hybridMappingId": "map-111",
                        "selected": True,
                        "switchPupilUrl": "https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/5001001",
                    },
                ],
            }
        }
        hub_resp = MagicMock()
        hub_resp.text = f"<script>IMHome.home.homeData = {json.dumps(home_data)};</script>"
        switch_resp = MagicMock()
        switch_resp.status_code = 500
        session.get.side_effect = [hub_resp, switch_resp]

        notif_resp = MagicMock()
        notif_resp.json.return_value = {
            "notifications": [
                {
                    "pupilSourceId": "unknown-999",
                    "appType": "News",
                    "type": "NewsItem",
                    "title": "Orphaned",
                    "orderDate": "2026-03-30T00:00:00",
                    "url": "/#/communication/news/999",
                },
            ]
        }
        msg_resp = MagicMock()
        msg_resp.json.return_value = {"items": [], "page": 0, "more": False}
        session.post.side_effect = [notif_resp, msg_resp]

        result = fetch_all_notifications(session)
        assert len(result) == 1
        assert len(result[0]["notifications"]) == 0

    def test_warns_on_unknown_pupil_source_id(self, capsys):
        session = MagicMock()
        home_data = {
            "account": {
                "currentUser": {"id": "1", "name": "Test"},
                "pupils": [
                    {
                        "name": "Andersson, Astrid",
                        "id": "2000010100",
                        "hybridMappingId": "map-111",
                        "selected": True,
                        "switchPupilUrl": "https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/5001001",
                    },
                ],
            }
        }
        hub_resp = MagicMock()
        hub_resp.text = f"<script>IMHome.home.homeData = {json.dumps(home_data)};</script>"
        switch_resp = MagicMock()
        switch_resp.status_code = 500
        session.get.side_effect = [hub_resp, switch_resp]

        notif_resp = MagicMock()
        notif_resp.raise_for_status = MagicMock()
        notif_resp.json.return_value = {
            "notifications": [
                {
                    "pupilSourceId": "unknown-999",
                    "appType": "News",
                    "type": "NewsItem",
                    "title": "Orphaned",
                    "orderDate": "2026-03-30T00:00:00",
                    "url": "/#/communication/news/999",
                },
            ]
        }
        msg_resp = MagicMock()
        msg_resp.raise_for_status = MagicMock()
        msg_resp.json.return_value = {"items": [], "page": 0, "more": False}
        session.post.side_effect = [notif_resp, msg_resp]

        fetch_all_notifications(session)
        captured = capsys.readouterr()
        assert "1 notification" in captured.err.lower()


class TestNormalizeMessageSummary:
    def test_maps_fields(self):
        raw = {
            "id": 11880746,
            "messageSubject": "Viktig info",
            "timeSent": "2026-03-28",
            "sentUser": {"displayName": "Larsson, Emelie", "id": 123, "type": "Teacher"},
            "isNew": False,
        }
        result = _normalize_message_summary(raw)
        assert result == {
            "id": "11880746",
            "subject": "Viktig info",
            "from": "Larsson, Emelie",
            "date": "2026-03-28",
        }

    def test_id_converted_to_string(self):
        raw = {"id": 42, "messageSubject": "Test", "timeSent": "2026-01-01"}
        result = _normalize_message_summary(raw)
        assert isinstance(result["id"], str)

    def test_missing_sent_user(self):
        raw = {"id": 1, "messageSubject": "Test", "timeSent": "2026-01-01"}
        result = _normalize_message_summary(raw)
        assert result["from"] is None


class TestFetchAllMessages:
    def _mock_session(self):
        session = MagicMock()
        home_data = {
            "account": {
                "currentUser": {"id": "1234567", "name": "Andersson, Erik"},
                "pupils": [
                    {
                        "name": "Andersson, Astrid",
                        "id": "2000010100",
                        "hybridMappingId": "map-111",
                        "selected": True,
                        "switchPupilUrl": "https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/5001001",
                    },
                    {
                        "name": "Andersson, Nils",
                        "id": "2100020200",
                        "hybridMappingId": "map-222",
                        "selected": False,
                        "switchPupilUrl": "https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/5002002",
                    },
                ],
            }
        }
        hub_html = f"<script>IMHome.home.homeData = {json.dumps(home_data)};</script>"
        hub_resp = MagicMock()
        hub_resp.text = hub_html

        switch_resp = MagicMock()
        switch_resp.status_code = 500

        session.get.side_effect = [hub_resp, switch_resp, switch_resp]

        felix_msg_resp = MagicMock()
        felix_msg_resp.json.return_value = {
            "items": [
                {"id": 100, "messageSubject": "Hej Astrid", "timeSent": "2026-03-28"},
                {"id": 101, "messageSubject": "Gamla", "timeSent": "2026-03-20"},
            ],
            "page": 0,
            "more": False,
        }
        felix_msg_resp.raise_for_status = MagicMock()

        viggo_msg_resp = MagicMock()
        viggo_msg_resp.json.return_value = {
            "items": [
                {"id": 200, "messageSubject": "Hej Nils", "timeSent": "2026-03-27"},
            ],
            "page": 0,
            "more": False,
        }
        viggo_msg_resp.raise_for_status = MagicMock()

        session.post.side_effect = [felix_msg_resp, viggo_msg_resp]
        return session

    def test_returns_all_children(self):
        session = self._mock_session()
        result = fetch_all_messages(session)
        assert len(result) == 2
        assert result[0]["child"] == "Andersson, Astrid"
        assert result[0]["child_id"] == "5001001"
        assert result[1]["child"] == "Andersson, Nils"
        assert result[1]["child_id"] == "5002002"

    def test_returns_formatted_messages(self):
        session = self._mock_session()
        result = fetch_all_messages(session)
        msg = result[0]["messages"][0]
        assert msg["id"] == "100"
        assert msg["subject"] == "Hej Astrid"
        assert msg["date"] == "2026-03-28"

    def test_sorted_by_date_descending(self):
        session = self._mock_session()
        result = fetch_all_messages(session)
        for child_data in result:
            dates = [m["date"] for m in child_data["messages"]]
            assert dates == sorted(dates, reverse=True)

    def test_switches_child_for_each(self):
        session = self._mock_session()
        fetch_all_messages(session)
        switch_urls = [c[0][0] for c in session.get.call_args_list[1:]]
        assert any("5001001" in u for u in switch_urls)
        assert any("5002002" in u for u in switch_urls)


class TestGetMeetingAvailabilities:
    def test_posts_to_correct_url(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"totalCount": 0, "totalPages": 0, "availabilities": []}
        session.post.return_value = resp
        get_meeting_availabilities(session)
        url = session.post.call_args[0][0]
        assert "/Home/meeting/GetPupilAvailabilities" in url

    def test_sends_empty_body(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"totalCount": 0, "totalPages": 0, "availabilities": []}
        session.post.return_value = resp
        get_meeting_availabilities(session)
        kwargs = session.post.call_args[1]
        assert kwargs["json"] == {}

    def test_returns_json_response(self):
        session = MagicMock()
        resp = MagicMock()
        payload = {
            "totalCount": 1,
            "totalPages": 1,
            "availabilities": [{"availabilityId": 3000001, "meetingId": 4000001, "meetingType": "Utvecklingssamtal"}],
        }
        resp.json.return_value = payload
        session.post.return_value = resp
        result = get_meeting_availabilities(session)
        assert result == payload

    def test_sends_ajax_header(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"totalCount": 0, "totalPages": 0, "availabilities": []}
        session.post.return_value = resp
        get_meeting_availabilities(session)
        headers = session.post.call_args[1].get("headers", {})
        assert headers.get("X-Requested-With") == "XMLHttpRequest"

    def test_raises_on_http_error(self):
        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500 Error")
        session.post.return_value = resp
        with pytest.raises(Exception, match="500"):
            get_meeting_availabilities(session)


class TestGetAttachment:
    def test_gets_correct_url(self):
        session = MagicMock()
        resp = MagicMock()
        resp.content = b"%PDF-1.4 test"
        session.get.return_value = resp
        url_path = "/Resources/Resource/Download/2000001?api=IM2&ModuleType=NewsItem&ConnectionId=1000001"
        get_attachment(session, url_path)
        called_url = session.get.call_args[0][0]
        assert called_url == f"https://hub.infomentor.se{url_path}"

    def test_returns_bytes(self):
        session = MagicMock()
        resp = MagicMock()
        resp.content = b"%PDF-1.4 fake"
        session.get.return_value = resp
        result = get_attachment(session, "/Resources/Resource/Download/123?api=IM2")
        assert isinstance(result, bytes)
        assert result == b"%PDF-1.4 fake"

    def test_sends_ajax_header(self):
        session = MagicMock()
        resp = MagicMock()
        resp.content = b""
        session.get.return_value = resp
        get_attachment(session, "/Resources/Resource/Download/123?api=IM2")
        headers = session.get.call_args[1].get("headers", {})
        assert headers.get("X-Requested-With") == "XMLHttpRequest"

    def test_raises_on_http_error(self):
        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("404 Not Found")
        session.get.return_value = resp
        with pytest.raises(Exception, match="404"):
            get_attachment(session, "/Resources/Resource/Download/bad")


class TestGetAttendanceDetail:
    def test_posts_to_correct_url(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"id": "197608"}
        session.post.return_value = resp
        get_attendance_detail(session, "197608")
        url = session.post.call_args[0][0]
        assert "/Attendance/Attendance/GetLeaveRequest" in url

    def test_sends_id_in_body(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"id": "197608"}
        session.post.return_value = resp
        get_attendance_detail(session, "197608")
        kwargs = session.post.call_args[1]
        assert kwargs["json"] == {"id": "197608"}

    def test_returns_json_response(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"id": "197608", "status": "Approved", "dates": "2026-04-01 - 2026-04-03"}
        session.post.return_value = resp
        result = get_attendance_detail(session, "197608")
        assert result == {"id": "197608", "status": "Approved", "dates": "2026-04-01 - 2026-04-03"}

    def test_sends_ajax_header(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {}
        session.post.return_value = resp
        get_attendance_detail(session, "197608")
        headers = session.post.call_args[1].get("headers", {})
        assert headers.get("X-Requested-With") == "XMLHttpRequest"

    def test_raises_on_http_error(self):
        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("404 Not Found")
        session.post.return_value = resp
        with pytest.raises(Exception, match="404"):
            get_attendance_detail(session, "99999")


class TestGetCalendarEvent:
    def test_posts_to_correct_url(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"eventId": "12345", "title": "Studiedag"}
        session.post.return_value = resp
        get_calendar_event(session, "12345")
        url = session.post.call_args[0][0]
        assert "/CalendarV2/CalendarV2/GetEvent" in url

    def test_sends_event_id_in_body(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"eventId": "12345"}
        session.post.return_value = resp
        get_calendar_event(session, "12345")
        kwargs = session.post.call_args[1]
        assert kwargs["json"] == {"eventId": "12345"}

    def test_returns_json_response(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"eventId": "12345", "title": "Studiedag", "startDate": "2026-04-01"}
        session.post.return_value = resp
        result = get_calendar_event(session, "12345")
        assert result == {"eventId": "12345", "title": "Studiedag", "startDate": "2026-04-01"}

    def test_sends_ajax_header(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {}
        session.post.return_value = resp
        get_calendar_event(session, "12345")
        headers = session.post.call_args[1].get("headers", {})
        assert headers.get("X-Requested-With") == "XMLHttpRequest"

    def test_raises_on_http_error(self):
        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("404 Not Found")
        session.post.return_value = resp
        with pytest.raises(Exception, match="404"):
            get_calendar_event(session, "99999")


class TestGetNewsDetail:
    def test_posts_to_get_news_list_url(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": [{"id": 1000001, "title": "Test", "content": "<p>Body</p>", "attachments": []}]}
        session.post.return_value = resp
        get_news_detail(session, 1000001)
        url = session.post.call_args[0][0]
        assert "/Communication/News/GetNewsList" in url

    def test_sends_page_size_minus_one(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": [{"id": 1000001, "title": "Test", "content": "", "attachments": []}]}
        session.post.return_value = resp
        get_news_detail(session, 1000001)
        kwargs = session.post.call_args[1]
        assert kwargs["json"]["pageSize"] == -1

    def test_returns_matching_item(self):
        session = MagicMock()
        resp = MagicMock()
        items = [
            {"id": 111, "title": "Other", "content": "x", "attachments": []},
            {"id": 1000001, "title": "Veckobrev", "content": "<p>Text</p>", "attachments": [{"url": "/Resources/Resource/Download/123", "title": "doc.pdf", "fileType": None}]},
        ]
        resp.json.return_value = {"items": items}
        session.post.return_value = resp
        result = get_news_detail(session, 1000001)
        assert result["id"] == 1000001
        assert result["title"] == "Veckobrev"
        assert len(result["attachments"]) == 1

    def test_returns_none_when_not_found(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": [{"id": 111, "title": "Other", "content": "", "attachments": []}]}
        session.post.return_value = resp
        result = get_news_detail(session, 9999)
        assert result is None

    def test_sends_ajax_header(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"items": []}
        session.post.return_value = resp
        get_news_detail(session, 1)
        headers = session.post.call_args[1].get("headers", {})
        assert headers.get("X-Requested-With") == "XMLHttpRequest"

    def test_raises_on_http_error(self):
        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500 Error")
        session.post.return_value = resp
        with pytest.raises(Exception, match="500"):
            get_news_detail(session, 1)
