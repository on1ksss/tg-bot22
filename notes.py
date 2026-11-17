"""Разбор времени напоминания из текста пользователя."""
import re
from datetime import datetime, timedelta


def parse_when(text: str, now: datetime):
    """'через 2 ч', '25.11 18:00', '25.11', 'завтра 10:00', '18:30' -> datetime или None."""
    t = text.strip().lower()
    m = re.fullmatch(r"через\s+(\d+)\s*(мин\w*|ч\w*|д\w*)", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = timedelta(minutes=n) if unit.startswith("м") else timedelta(hours=n) if unit.startswith("ч") else timedelta(days=n)
        return now + delta
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        h, mi = (int(m.group(3)), int(m.group(4))) if m.group(3) else (9, 0)
        try:
            r = now.replace(month=mo, day=d, hour=h, minute=mi, second=0, microsecond=0)
        except ValueError:
            return None
        return r if r > now else r.replace(year=r.year + 1)
    m = re.fullmatch(r"(?:(сегодня|завтра)\s+)?(\d{1,2}):(\d{2})", t)
    if m:
        h, mi = int(m.group(2)), int(m.group(3))
        if h > 23 or mi > 59:
            return None
        r = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if m.group(1) == "завтра" or (not m.group(1) and r <= now):
            r += timedelta(days=1)
        return r
    return None


def preset(code: str, now: datetime):
    """Быстрые варианты: h1 — через час, ev — сегодня 20:00 (или завтра), tm — завтра 9:00."""
    if code == "h1":
        return now + timedelta(hours=1)
    if code == "ev":
        r = now.replace(hour=20, minute=0, second=0, microsecond=0)
        return r if r > now else r + timedelta(days=1)
    if code == "tm":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return None


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")
