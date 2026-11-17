"""Общие функции: получение событий календаря и список несданного."""
import hashlib
import logging

from . import db
from .crypto import dec
from .ical import fetch_events, parse_ics

log = logging.getLogger("service")


class NoCalendar(Exception):
    pass


async def events(tg_id: int):
    cal = db.run("SELECT ics FROM cal WHERE tg_id=?", (tg_id,))
    if cal:
        return parse_ics(cal[0]["ics"])
    row = db.run("SELECT ical_enc FROM users WHERE tg_id=?", (tg_id,))
    if not row or not row[0]["ical_enc"]:
        raise NoCalendar
    return await fetch_events(dec(row[0]["ical_enc"]))


def pending(tg_id: int, evs, now):
    done = {r["uid"] for r in db.run("SELECT uid FROM done WHERE tg_id=?", (tg_id,))}
    return [e for e in evs if e.due >= now and e.uid not in done]


def key(uid: str) -> str:
    """Короткий ключ события для callback_data (лимит Telegram 64 байта)."""
    return hashlib.sha1(uid.encode()).hexdigest()[:10]


async def safe_events(tg_id: int):
    """(события, None) или (None, текст_ошибки_для_пользователя)."""
    import httpx
    from cryptography.fernet import InvalidToken
    try:
        return await events(tg_id), None
    except NoCalendar:
        return None, "Сначала пришлите файл календаря .ics документом (Календарь в LMS → Экспорт) или ссылку: /setcal <ссылка>"
    except InvalidToken:
        return None, "Не могу расшифровать сохранённую ссылку (изменился SECRET_KEY). Пришлите /setcal заново."
    except httpx.HTTPStatusError as e:
        return None, f"LMS ответила ошибкой {e.response.status_code}. Возможно, ссылка календаря устарела: пришлите файл .ics или новую ссылку."
    except httpx.HTTPError as e:
        return None, f"Не удалось скачать календарь ({type(e).__name__}). Проверьте интернет/VPN или пришлите файл .ics."
    except Exception:
        log.exception("calendar failed")
        return None, "Ошибка при разборе календаря, подробности в окне, где запущен бот."
