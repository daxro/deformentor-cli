"""InfoMentor data fetching and normalization."""

import json
import re
import sys
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from deformentor_cli.errors import UpstreamStateError

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
    if resp.status_code != 500:
        resp.raise_for_status()
    try:
        selected = [
            child for child in get_children(session)
            if str(child["id"]) == str(pupil_id) and child.get("selected")
        ]
    except (KeyError, TypeError, RuntimeError) as error:
        raise UpstreamStateError("InfoMentor did not confirm the requested child context.") from error
    if len(selected) != 1:
        raise UpstreamStateError("InfoMentor did not confirm the requested child context.")


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


def get_messages(session, fetch_all_pages=False, max_pages=50):
    """Get messages for the currently selected child.

    Messages are scoped to the currently selected child. The caller must call
    switch_child() before calling this to set the correct context.

    Args:
        session: Authenticated requests.Session.
        fetch_all_pages: If True, paginate through all pages.
        max_pages: Maximum number of pages to fetch (safety limit).

    Returns:
        List of raw message dicts.
    """
    if max_pages <= 0:
        raise ValueError("max_pages must be a positive integer")

    all_items = []
    page = 1
    more = False
    for _ in range(max_pages):
        resp = session.post(
            f"{BASE_URL}/Message/Message/GetMessages",
            json={"page": page},
            headers=AJAX_HEADERS,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        all_items.extend(data["items"])
        more = bool(data.get("more"))
        if not fetch_all_pages or not more:
            if not fetch_all_pages and more:
                print("Warning: additional message pages exist but were not fetched", file=sys.stderr)
            break
        page += 1
    else:
        if fetch_all_pages and more:
            print(f"Warning: --max-pages limit of {max_pages} reached; more messages remain", file=sys.stderr)
    return all_items


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


def validate_attachment_url(url_path):
    """Validate an InfoMentor attachment path without making a request."""
    if not isinstance(url_path, str) or not url_path.startswith("/"):
        raise ValueError("Attachment URL must start with '/' and be a relative InfoMentor path.")
    decoded_url = url_path
    for _ in range(4):
        next_value = unquote(decoded_url)
        if next_value == decoded_url:
            break
        decoded_url = next_value
    else:
        raise ValueError("Attachment URL has too many encoding layers.")
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded_url):
        raise ValueError("Attachment URL contains control characters.")

    parsed = urlsplit(decoded_url)
    if parsed.scheme or parsed.netloc:
        raise ValueError("Attachment URL must not include a scheme or host.")
    if parsed.fragment:
        raise ValueError("Attachment URL must not include a fragment.")

    decoded_path = parsed.path
    if "\\" in decoded_path or ".." in decoded_path.split("/"):
        raise ValueError("Attachment URL contains path traversal.")
    if not re.fullmatch(r"/Resources/Resource/Download/[^/]+", decoded_path):
        raise ValueError("Attachment URL is not an expected InfoMentor download path.")
    return url_path


def get_attachment(session, url_path):
    """Fetch an attachment by its validated URL path and return raw bytes.

    url_path is the value from a news item's attachments[].url field,
    e.g. '/Resources/Resource/Download/2000001?api=IM2&ModuleType=NewsItem&ConnectionId=1000001'

    Raises:
        ValueError: If url_path is not an expected relative attachment path.
    """
    validate_attachment_url(url_path)

    resp = session.get(
        f"{BASE_URL}{url_path}",
        headers=AJAX_HEADERS,
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    if isinstance(resp.status_code, int) and 300 <= resp.status_code < 400:
        raise UpstreamStateError("InfoMentor attachment endpoint returned an unexpected redirect.")
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


def _format_local_date_payload(date_iso):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_iso):
        return f"{date_iso}T00:00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00", date_iso):
        return date_iso
    raise ValueError(f"Invalid local date format: {date_iso}")


def get_time_registrations(session, date_iso):
    """Fetch time registrations for a local calendar date."""
    resp = session.post(
        f"{BASE_URL}/TimeRegistration/TimeRegistration/GetTimeRegistrations/",
        json={
            "date": _format_local_date_payload(date_iso),
            "showNextWeekIfNoMoreSchoolDays": False,
        },
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return data.get("items") or data.get("timeRegistrations") or []
    if isinstance(data, list):
        return data
    return []


def get_time_registration_for_date(session, date_iso):
    """Return the matching time registration row for a local calendar date."""
    target_date = date_iso[:10]
    for row in get_time_registrations(session, date_iso):
        row_date = str(row.get("date") or row.get("startDate") or row.get("registrationDate") or "")[:10]
        if row_date == target_date:
            return row
    raise RuntimeError(f"No time registration found for {target_date}")


def get_time_registration_comments(session, date_iso):
    """Fetch raw time registration comments for a local calendar date."""
    resp = session.post(
        f"{BASE_URL}/TimeRegistration/TimeRegistration/GetComments/",
        json={"date": _format_local_date_payload(date_iso)},
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return data.get("comments") or data.get("items") or []
    if isinstance(data, list):
        return data
    return []


def save_time_registration_comment(session, comment_id, comment_text, time_registration_id):
    """Save a time registration comment and return the raw response JSON."""
    resp = session.post(
        f"{BASE_URL}/TimeRegistration/TimeRegistration/SaveComment/",
        json={
            "commentId": comment_id,
            "commentText": comment_text,
            "timeRegistrationId": time_registration_id,
        },
        headers=AJAX_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(data.get("message") or "SaveComment returned success: false")
    return data


def normalize_time_registration_comment(raw_comments):
    """Normalize the first non-empty time registration comment for CLI output."""
    if isinstance(raw_comments, dict):
        raw_comments = raw_comments.get("comments") or raw_comments.get("items") or []

    for raw in raw_comments or []:
        user_comment = raw.get("userComment") or ""
        if not user_comment.strip():
            continue
        owner = (
            raw.get("owner")
            or raw.get("ownerName")
            or raw.get("sender")
            or raw.get("senderName")
            or raw.get("createdBy")
            or raw.get("userName")
        )
        return {
            "found": True,
            "parentCommentId": raw.get("parentCommentId", 0),
            "userComment": user_comment,
            "owner": owner,
            "canEditComment": raw.get("canEditComment"),
            "raw": raw,
        }
    return {"found": False}


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


def fetch_all_messages(session, fetch_all_pages=False, max_pages=50):
    """Get messages for all children.

    1. Get children list from hub page
    2. For each child: switch context, get messages
    3. Format and sort by date descending

    Args:
        session: Authenticated requests.Session.
        fetch_all_pages: If True, paginate through all pages per child.
        max_pages: Maximum number of pages to fetch per child (safety limit).

    Returns list of dicts with child name, child_id, and messages list.
    """
    children = get_children(session)
    result = []
    for child in children:
        switch_child(session, child["id"])
        raw_messages = get_messages(session, fetch_all_pages=fetch_all_pages, max_pages=max_pages)
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
