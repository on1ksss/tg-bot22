import asyncio
import io
import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from cryptography.fernet import InvalidToken

from . import calculator as calc, db, submit, ui
from .config import BOT_TOKEN, HEARTBEAT, REMIND_HOUR
from .crypto import dec, enc
from .eljur import EljurClient, EljurError, parse_cookies
from .service import NoCalendar, events as _events, pending as _pending
from .ical import MSK, fetch_events, parse_ics, upcoming

dp = Dispatcher()
log = logging.getLogger("bot")

HELP = (
    "Команды:\n"
    "Пришлите файл календаря .ics (экспорт из LMS) — и /deadlines покажет сроки\n"
    "/setcal <ссылка> — вместо файла: ссылка календаря (сообщение удалю)\n"
    "/deadlines — ближайшие сроки, /done N — отметить сданным\n"
    "/scale 5|12|100 — система оценивания\n"
    "/addscore Предмет получено максимум — добавить баллы\n"
    "/scores — мои баллы, /clearscores Предмет — удалить баллы по предмету\n"
    "/calc Предмет макс_за_семестр цель% — сколько ещё нужно\n"
    "/setcookie <cookie> — сессия элжура (сообщение удалю)\n"
    "/teacher Имя id — сохранить преподавателя, /delteacher Имя — удалить, /send — сдать работу в элжур"
)


@dp.message(Command("start"))
async def start(m: Message, state: FSMContext):
    await state.clear()
    db.ensure_user(m.from_user.id)
    await m.answer("Привет! Я помогу не пропустить сроки, сохраню заметки, посчитаю баллы "
                   "и отправлю работу в элжур.\n\n" + ui.MENU_TEXT, reply_markup=ui.main_menu())


@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(HELP + "\n\n/menu — главное меню с кнопками, /notes — заметки")


async def _secret(m: Message, cmd: CommandObject, column: str, label: str):
    if not cmd.args:
        return await m.answer(f"Использование: /{cmd.command} <значение>")
    db.ensure_user(m.from_user.id)
    db.run(f"UPDATE users SET {column}=? WHERE tg_id=?", (enc(cmd.args.strip()), m.from_user.id))
    try:
        await m.delete()
    except Exception:
        pass
    await m.answer(f"✅ Сохранено в зашифрованном виде ({label}). Исходное сообщение удалено.")


@dp.message(Command("setcal"))
async def setcal(m: Message, command: CommandObject):
    await _secret(m, command, "ical_enc", "Ссылка календаря")


@dp.message(Command("setcookie"))
async def setcookie(m: Message, command: CommandObject):
    await _secret(m, command, "eljur_enc", "Сессия элжура")


async def _safe_events(m: Message):
    """Список событий или None, если не вышло (тогда пользователю уже объяснили почему)."""
    try:
        return await _events(m.from_user.id)
    except NoCalendar:
        await m.answer("Сначала пришлите файл календаря .ics документом (Календарь в LMS → Экспорт) или ссылку: /setcal <ссылка>")
    except InvalidToken:
        await m.answer("Не могу расшифровать сохранённую ссылку (изменился SECRET_KEY). Пришлите /setcal заново.")
    except httpx.HTTPStatusError as e:
        await m.answer(f"LMS ответила ошибкой {e.response.status_code}. Возможно, ссылка календаря устарела: создайте новую и пришлите /setcal.")
    except httpx.HTTPError as e:
        await m.answer(f"Не удалось скачать календарь ({type(e).__name__}). Проверьте интернет/VPN на компьютере, где запущен бот.")
    except Exception:
        log.exception("calendar failed")
        await m.answer("Ошибка при разборе календаря, подробности в окне, где запущен бот.")
    return None


@dp.message(Command("deadlines"))
async def deadlines(m: Message):
    text, markup = await ui.deadline_view(m.from_user.id, 0)
    await m.answer(text, reply_markup=markup)


@dp.message(F.document, StateFilter(None))
async def upload_ics(m: Message, bot: Bot):
    if not (m.document.file_name or "").lower().endswith(".ics"):
        return await m.answer("Для календаря нужен файл с расширением .ics (экспорт из LMS). "
                              "Чтобы отправить работу в элжур, используйте /send.")
    buf = io.BytesIO()
    await bot.download(m.document, destination=buf)
    try:
        events = parse_ics(buf.getvalue().decode("utf-8-sig"))
    except Exception:
        log.exception("ics parse failed")
        return await m.answer("Не смог разобрать файл. Экспортируйте календарь из LMS заново и пришлите .ics.")
    if not events:
        return await m.answer("В файле нет событий. В LMS при экспорте выберите «Все события» и период «Ближайшие 60 дней».")
    db.ensure_user(m.from_user.id)
    db.run("INSERT OR REPLACE INTO cal(tg_id, ics) VALUES(?,?)", (m.from_user.id, buf.getvalue().decode("utf-8-sig")))
    await m.answer(f"✅ Календарь загружен: {len(events)} событий. Команда /deadlines покажет ближайшие сроки.")


@dp.message(Command("done"))
async def done(m: Message, command: CommandObject):
    events = await _safe_events(m)
    if events is None:
        return
    items = _pending(m.from_user.id, events, datetime.now(MSK))
    try:
        e = items[int(command.args) - 1]
    except (TypeError, ValueError, IndexError):
        return await m.answer("Использование: /done N (номер из /deadlines)")
    db.run("INSERT OR IGNORE INTO done VALUES(?,?)", (m.from_user.id, e.uid))
    await m.answer(f"Отмечено сданным: {e.title[:60]}")


@dp.message(Command("scale"))
async def scale(m: Message, command: CommandObject):
    if command.args not in ("5", "12", "100"):
        return await m.answer("Использование: /scale 5, /scale 12 или /scale 100")
    db.ensure_user(m.from_user.id)
    db.run("UPDATE users SET scale=? WHERE tg_id=?", (command.args, m.from_user.id))
    await m.answer(f"Система оценивания: {command.args}")


@dp.message(Command("addscore"))
async def addscore(m: Message, command: CommandObject):
    try:
        *name, got, mx = (command.args or "").split()
        db.run("INSERT INTO scores(tg_id,subject,got,max) VALUES(?,?,?,?)",
               (m.from_user.id, " ".join(name), float(got), float(mx)))
        await m.answer("Добавлено")
    except ValueError:
        await m.answer("Использование: /addscore Предмет получено максимум")


def _user_scale(tg_id: int) -> str:
    r = db.run("SELECT scale FROM users WHERE tg_id=?", (tg_id,))
    return r[0]["scale"] if r else "100"


@dp.message(Command("scores"))
async def scores(m: Message):
    rows = db.run("SELECT subject, SUM(got) g, SUM(max) mx FROM scores WHERE tg_id=? GROUP BY subject",
                  (m.from_user.id,))
    if not rows:
        return await m.answer("Баллов пока нет: /addscore Предмет получено максимум")
    sc = _user_scale(m.from_user.id)
    await m.answer("\n".join(
        f"{r['subject']}: {r['g']:g}/{r['mx']:g} ({calc.percent_of(r['g'], r['mx']):.0f}%) → "
        f"{calc.to_scale(calc.percent_of(r['g'], r['mx']), sc)}" for r in rows))


@dp.message(Command("calc"))
async def calc_cmd(m: Message, command: CommandObject):
    try:
        *name, total, target = (command.args or "").split()
        name = " ".join(name); total = float(total); target = float(target)
    except ValueError:
        return await m.answer("Использование: /calc Предмет макс_за_семестр цель%")
    r = db.run("SELECT SUM(got) g, SUM(max) mx FROM scores WHERE tg_id=? AND subject=?",
               (m.from_user.id, name))[0]
    got, done_max = r["g"] or 0, r["mx"] or 0
    res = calc.needed_points(got, done_max, total, target)
    if res["status"] == "invalid":
        return await m.answer(
            f"Введено {done_max:g} баллов за работы, а максимум за семестр {total:g}. "
            f"Проверьте данные: /scores. Лишнюю запись можно убрать: /clearscores {name}")
    cur = f"Сейчас {got:g} из {done_max:g} за сданные работы."
    if res["status"] == "reached":
        text = f"{cur} Цель {target:g}% уже достигнута 🎉"
    elif res["status"] == "ok":
        text = f"{cur} До {target:g}% нужно ещё {res['need']} б. из {res['remaining']:g} оставшихся."
    else:
        text = (f"{cur} Нужно ещё {res['need']} б., а осталось только {res['remaining']:g}: "
                f"на эту цель баллов уже не хватает.")
    await m.answer(text)


@dp.message(Command("clearscores"))
async def clearscores(m: Message, command: CommandObject):
    if not command.args:
        return await m.answer("Использование: /clearscores Предмет (удалит все баллы по предмету)")
    db.run("DELETE FROM scores WHERE tg_id=? AND subject=?", (m.from_user.id, command.args.strip()))
    await m.answer("Баллы по предмету удалены")


@dp.message(Command("teacher"))
async def teacher(m: Message, command: CommandObject):
    parts = (command.args or "").split()
    if len(parts) < 2 or not parts[-1].isdigit():
        return await m.answer("Нужно имя и число-id получателя из элжура, например:\n/teacher Киселев 17797\n"
                              "Слово «id» писать не нужно: подставьте само число.")
    db.ensure_user(m.from_user.id)
    db.run("INSERT OR REPLACE INTO teachers VALUES(?,?,?)", (m.from_user.id, " ".join(parts[:-1]), parts[-1]))
    await m.answer(f"Сохранено: {' '.join(parts[:-1])} → {parts[-1]}")


@dp.message(Command("delteacher"))
async def delteacher(m: Message, command: CommandObject):
    if not command.args:
        return await m.answer("Использование: /delteacher Фамилия")
    db.run("DELETE FROM teachers WHERE tg_id=? AND name=?", (m.from_user.id, command.args.strip()))
    await m.answer("Удалено")


async def heartbeat():
    while True:
        Path(HEARTBEAT).touch()
        await asyncio.sleep(30)


async def reminders(bot: Bot):
    """Раз в сутки в REMIND_HOUR по Москве: сроки на ближайшие 3 дня."""
    while True:
        now = datetime.now(MSK)
        nxt = now.replace(hour=REMIND_HOUR, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        users = db.run("SELECT tg_id FROM users WHERE ical_enc IS NOT NULL UNION SELECT tg_id FROM cal")
        for u in users:
            try:
                events = await _events(u["tg_id"])
                soon = upcoming(_pending(u["tg_id"], events, datetime.now(MSK)), datetime.now(MSK), 3)
                if soon:
                    await bot.send_message(u["tg_id"], "⏰ Скоро сроки:\n" + "\n".join(
                        f"• {e.due:%d.%m %H:%M} — {e.subject}: {e.title[:60]}" for e in soon),
                        reply_markup=ui.main_menu())
            except Exception:
                log.exception("reminder failed for %s", u["tg_id"])


async def main():
    logging.basicConfig(level=logging.INFO)
    db.init()
    dp.include_router(ui.router)
    dp.include_router(submit.router)
    bot = Bot(BOT_TOKEN)
    asyncio.create_task(heartbeat())
    asyncio.create_task(reminders(bot))
    asyncio.create_task(ui.note_reminders(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
