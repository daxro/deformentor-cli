"""InfoMentor data fetching and normalization."""

import json
import re
import sys
from urllib.parse import parse_qs, urlparse

BASE_URL = "https://hub.infomentor.se"
HTTP_TIMEOUT = 30
AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def get_children(session):
    """Get list of children from hub page.

    Parses IMHome.home.homeData JSON embedded in the hub page HTML.

    Returns list of dicts with keys: name, id, hybridMappingId, selected.
    The id is extracted from switchPupilUrl (the InfoMentor pupil ID used
    for API calls like SwitchPupil).
    """
    resp = session.get(f"{BASE_URL}/", timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = _parse_home_data(resp.text)
    children = []
    for p in data["account"]["pupils"]:
        switch_url = p["switchPupilUrl"]
        pupil_id = switch_url.rstrip("/").rsplit("/", 1)[-1]
        children.append({
            "name": p["name"],
            "id": pupil_id,
            "hybridMappingId": p["hybridMappingId"],
            "selected": p.get("selected", False),
        })
    return children


def switch_child(session, pupil_id):
    """Switch the session's active child context.

    The endpoint returns HTTP 500 but the switch still takes effect.
    Non-500 errors indicate a real failure (e.g., expired session).
    """
    resp = session.get(
        f"{BASE_URL}/Account/PupilSwitcher/SwitchPupil/{pupil_id}",
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 500 and resp.status_code >= 400:
        raise RuntimeError(f"SwitchPupil returned unexpected status {resp.status_code}")


def get_notifications(session):
    """Get notifications for all children in one call.

    Returns the raw notification list. Each notification has a pupilSourceId
    field that identifies which child it belongs to.
    """
    resp = session.post(
        f"{BASE_URL}/notificationApp/notificationApp/getNotifications",
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["notifications"]


def get_messages(session):
    """Get messages for the currently selected child.

    Messages are scoped to the currently selected child. The caller must call
    switch_child() before calling this to set the correct context.

    Only fetches page 1. Warns if more pages exist.
    """
    resp = session.post(
        f"{BASE_URL}/Message/Message/GetMessages",
        json={"page": 1},
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("more"):
        print("Warning: additional message pages exist but were not fetched", file=sys.stderr)
    return data["items"]


def get_attendance_detail(session, request_id):
    """Fetch a single attendance / leave request by ID.

    Returns the raw API response as a dict.
    """
    resp = session.post(
        f"{BASE_URL}/Attendance/Attendance/GetLeaveRequest",
        json={"id": request_id},
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_calendar_event(session, event_id):
    """Fetch a single calendar event by ID.

    Returns the raw API response as a dict.
    """
    resp = session.post(
        f"{BASE_URL}/CalendarV2/CalendarV2/GetEvent",
        json={"eventId": event_id},
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_news_detail(session, news_id):
    """Fetch a single news item by ID.

    Calls GetNewsList (which returns all items with full content) and filters
    by id client-side. Returns the matching item dict, or None if not found.
    """
    resp = session.post(
        f"{BASE_URL}/Communication/News/GetNewsList",
        json={"pageSize": -1, "sortBy": "lastPublishDate___SORT_DESC"},
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return next((item for item in items if item["id"] == int(news_id)), None)


def get_attachment(session, url_path):
    """Fetch an attachment by its URL path and return raw bytes.

    url_path is the value from a news item's attachments[].url field,
    e.g. '/Resources/Resource/Download/18065702?api=IM2&ModuleType=NewsItem&ConnectionId=1942932'
    """
    resp = session.get(
        f"{BASE_URL}{url_path}",
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.content


def get_meeting_availabilities(session):
    """Fetch meeting slot availabilities for the current child context.

    Returns a dict with totalCount, totalPages, and availabilities list.
    Each availability has: availabilityId, date, timeFrom, stringDate,
    timeRange, registeredBy, meetingType, location, meetingId (int if
    booked, None if not), meetingLink, bookingsCloseBefore.
    """
    resp = session.post(
        f"{BASE_URL}/Home/meeting/GetPupilAvailabilities",
        json={},
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _extract_id_from_url(url):
    """Extract ID from notification URL hash route. Returns str or None.

    Handles these patterns:
    - /#/communication/news/{id}        -> last path segment
    - /#/attendance/.../show/{id}       -> last path segment
    - /#/calendarv2/...?eventId={id}    -> eventId query param
    - #/meeting                         -> None

    The `not isalpha()` check accepts any non-empty string containing at least
    one non-letter character (digits, hyphens, etc.), which is broader than the
    specific URL patterns listed above. Purely alphabetic segments like "meeting"
    or "show" still return None.
    """
    if not url:
        return None
    fragment = urlparse(url).fragment
    if not fragment:
        return None
    parsed = urlparse(fragment)
    query = parse_qs(parsed.query)
    if "eventId" in query:
        return query["eventId"][0]
    segments = parsed.path.rstrip("/").split("/")
    last = segments[-1] if segments else ""
    if last and not last.isalpha():
        return last
    return None


def _normalize_notification(notification):
    """Convert a raw InfoMentor notification to output schema format."""
    return {
        "date": notification["orderDate"],
        "type": {
            "name": _normalize_type_name(notification["appType"]),
            "id": _extract_id_from_url(notification.get("url")),
            "action": notification["type"],
            "title": notification["title"],
        },
    }


def _normalize_message(message):
    """Convert a raw InfoMentor message to output schema format."""
    return {
        "date": f"{message['timeSent']}T00:00:00",
        "type": {
            "name": "message",
            "id": str(message["id"]),
            "action": None,
            "title": message["messageSubject"],
        },
    }


def _normalize_message_summary(message):
    """Convert a raw InfoMentor message to the messages-command output format.

    Distinct from _normalize_message which produces the notification-timeline
    schema ({date, type: {name, id, action, title}}). This format is used by
    the standalone messages command where the type wrapper is unnecessary.
    """
    sent_user = message.get("sentUser") or {}
    return {
        "id": str(message["id"]),
        "subject": message["messageSubject"],
        "from": sent_user.get("displayName"),
        "date": message["timeSent"],
    }


def _normalize_type_name(name):
    """Lowercase and strip version suffix. CalendarV2 -> calendar."""
    return re.sub(r"[Vv]\d+$", "", name).lower()


def fetch_all_notifications(session):
    """Get notifications and messages for all children.

    1. Get children list from hub page
    2. Get all notifications in one call (covers all children via pupilSourceId)
    3. Partition notifications by child using pupilSourceId -> hybridMappingId
    4. For each child: switch context, get messages
    5. Merge notifications + messages per child, sort by date descending

    Returns list matching notifications-schema.json.
    """
    children = get_children(session)
    raw_notifications = get_notifications(session)

    # Map hybridMappingId -> child for partitioning
    child_by_mapping = {c["hybridMappingId"]: c for c in children}

    # Partition notifications by child
    notifications_by_child = {c["id"]: [] for c in children}
    dropped_count = 0
    for n in raw_notifications:
        child = child_by_mapping.get(n["pupilSourceId"])
        if child:
            notifications_by_child[child["id"]].append(_normalize_notification(n))
        else:
            dropped_count += 1

    if dropped_count:
        print(f"Warning: {dropped_count} notification(s) with unknown child ID dropped", file=sys.stderr)

    # For each child: switch, get messages, merge
    result = []
    for child in children:
        switch_child(session, child["id"])
        raw_messages = get_messages(session)
        messages = [_normalize_message(m) for m in raw_messages]

        all_items = notifications_by_child[child["id"]] + messages
        all_items.sort(key=lambda x: x["date"], reverse=True)

        result.append({
            "child": child["name"],
            "child_id": child["id"],
            "notifications": all_items,
        })

    return result


def fetch_all_messages(session):
    """Get messages for all children.

    1. Get children list from hub page
    2. For each child: switch context, get messages
    3. Format and sort by date descending

    Note: get_messages only fetches page 1. A warning is emitted if more exist.

    Returns list of dicts with child name, child_id, and messages list.
    """
    children = get_children(session)
    result = []
    for child in children:
        switch_child(session, child["id"])
        raw_messages = get_messages(session)
        messages = [_normalize_message_summary(m) for m in raw_messages]
        messages.sort(key=lambda x: x["date"], reverse=True)
        result.append({
            "child": child["name"],
            "child_id": child["id"],
            "messages": messages,
        })
    return result


def _parse_home_data(html):
    """Extract IMHome.home.homeData JSON object from hub page HTML."""
    match = re.search(r"IMHome\.home\.homeData\s*=\s*", html)
    if not match:
        raise RuntimeError("Could not find homeData in hub page")
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(html, match.end())
    return data
