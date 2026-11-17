"""Разбор календаря Moodle (.ics). Время в файле в UTC, переводим в Москву."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


@dataclass
class Event:
    uid: str
    subject: str
    title: str
    due: datetime  # aware, MSK
    url: str = ""  # ссылка на задание, если нашлась в файле


def _unescape(s: str) -> str:
    return s.replace("\\,", ",").replace("\\;", ";").replace("\\n", "\n").replace("\\\\", "\\")


def parse_ics(text: str) -> list[Event]:
    text = text.replace("\r\n", "\n").replace("\n\t", "").replace("\n ", "")
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        def get(key):
            m = re.search(rf"^{key}[^:\n]*:(.*)$", block, re.M)
            return m.group(1).strip() if m else ""
        start = get("DTSTART")
        if not start:
            continue
        fmt = "%Y%m%dT%H%M%SZ" if start.endswith("Z") else "%Y%m%dT%H%M%S"
        due = datetime.strptime(start, fmt).replace(tzinfo=timezone.utc).astimezone(MSK)
        subject = re.sub(r"\s*#\d+$", "", _unescape(get("CATEGORIES")))
        flat = block.replace("\n", "")
        m = re.search(r"https?://[^\s\\]+/mod/assign/view\.php\?id=\d+", flat) or re.search(r"^URL[^:\n]*:(https?://\S+)", block, re.M)
        url = m.group(m.lastindex or 0) if m else ""
        events.append(Event(get("UID"), subject, _unescape(get("SUMMARY")), due, url))
    return sorted(events, key=lambda e: e.due)


def upcoming(events: list[Event], now: datetime, days: int = 14) -> list[Event]:
    return [e for e in events if now <= e.due <= now + timedelta(days=days)]


def normalize_url(url: str) -> str:
    """Moodle/браузеры отдают ссылку то как webcal://, то без https:// — приводим к https://."""
    url = url.strip().strip("<>\"'")
    if url.lower().startswith("webcal://"):
        return "https://" + url[len("webcal://"):]
    if not url.lower().startswith(("http://", "https://")):
        return "https://" + url
    return url


async def fetch_events(url: str) -> list[Event]:
    import httpx
    url = normalize_url(url)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
    return parse_ics(r.text)
