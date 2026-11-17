"""Тексты карточек (без Telegram, чтобы можно было тестировать)."""
from datetime import datetime


def urgency(due: datetime, now: datetime) -> str:
    h = (due - now).total_seconds() / 3600
    return "🔴" if h <= 24 else "🟡" if h <= 72 else "🟢"


def left(due: datetime, now: datetime) -> str:
    sec = (due - now).total_seconds()
    if sec <= 0:
        return "срок вышел"
    if sec < 3600:
        return f"через {int(sec // 60)} мин"
    if sec < 48 * 3600:
        return f"через {int(sec // 3600)} ч"
    return f"через {int(sec // 86400)} дн."


def card_text(e, i: int, n: int, now: datetime) -> str:
    return (f"{urgency(e.due, now)} {left(e.due, now).capitalize()}\n\n"
            f"📚 {e.subject}\n"
            f"📝 {e.title}\n"
            f"⏰ Срок: {e.due:%d.%m %H:%M}\n\n"
            f"Задание {i + 1} из {n}")
