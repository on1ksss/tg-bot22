"""Сдача работы в элжур: задание → преподаватель → файл → подтверждение → отправка."""
import logging
import re
import tempfile
import time
from pathlib import Path

import httpx
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, Message
from cryptography.fernet import InvalidToken
from datetime import datetime

from . import db, service
from .crypto import dec
from .eljur import EljurClient, EljurError, parse_cookies
from .ical import MSK
from .ui import HOME, kb, safe_answer, show

router = Router()
log = logging.getLogger("submit")

_CACHE: dict[int, tuple[float, list]] = {}  # список преподавателей на 10 минут


class Submit(StatesGroup):
    subject = State(); teacher = State(); file = State(); confirm = State()


CANCEL = [Btn(text="❌ Отмена", callback_data="sx:no")]


def suggest(subject: str, teachers: list[dict]) -> set[str]:
    """id преподавателей, у которых в списке предметов есть название предмета из календаря."""
    s = subject.strip().lower()
    return {t["id"] for t in teachers if s and s in t["info"].lower()}


async def _teachers(tg_id: int):
    """(список, None) или (None, текст ошибки)."""
    cached = _CACHE.get(tg_id)
    if cached and time.time() - cached[0] < 600:
        return cached[1], None
    row = db.run("SELECT eljur_enc FROM users WHERE tg_id=?", (tg_id,))
    if not row or not row[0]["eljur_enc"]:
        return None, "Сначала подключи элжур: открой «⚙️ Настройка» в меню."
    try:
        lst = await EljurClient(parse_cookies(dec(row[0]["eljur_enc"]))).recipients()
    except InvalidToken:
        return None, "Не могу расшифровать сессию элжура (изменился SECRET_KEY). Отправь /setcookie заново."
    except EljurError as e:
        return None, f"❌ {e}"
    except httpx.HTTPError as e:
        return None, f"Не удалось связаться с элжуром ({type(e).__name__}). Попробуй позже."
    except Exception:
        log.exception("recipients failed")
        return None, "Не получилось получить список преподавателей, подробности в окне, где запущен бот."
    if not lst:
        return None, "Элжур не вернул ни одного преподавателя."
    _CACHE[tg_id] = (time.time(), lst)
    return lst, None


async def _teacher_screen(tg_id: int, subj: str):
    lst, err = await _teachers(tg_id)
    if lst is None:
        return err, kb([HOME]), {}
    hint = suggest(subj, lst)
    ordered = sorted(lst, key=lambda t: (t["id"] not in hint, t["name"]))
    rows = []
    for t in ordered[:30]:
        label = ("⭐ " if t["id"] in hint else "") + t["short"] + (f" · {t['info'][:26]}" if t["info"] else "")
        rows.append([Btn(text=label[:60], callback_data=f"st:{t['id']}")])
    rows.append(CANCEL)
    text = "Кому отправить?" + ("\n⭐ — преподаватель этого предмета." if hint else "")
    return text, kb(rows), {t["id"]: t["short"] for t in lst}


async def _task_screen(tg_id: int):
    evs, err = await service.safe_events(tg_id)
    rows = []
    text = "Какую работу сдаёшь?"
    if evs is None:
        text = f"{err}\n\nМожно сдать и без календаря: нажми «Другая работа»."
    else:
        for e in service.pending(tg_id, evs, datetime.now(MSK))[:6]:
            rows.append([Btn(text=f"{e.due:%d.%m} {e.subject[:16]} — {e.title[:22]}", callback_data=f"sd:{service.key(e.uid)}")])
    rows.append([Btn(text="✏️ Другая работа", callback_data="sd:free")])
    rows.append(HOME)
    return text, kb(rows)


@router.message(Command("send"))
async def send_cmd(m: Message, state: FSMContext):
    await state.clear()
    text, markup = await _task_screen(m.from_user.id)
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data == "m:send")
async def send_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = await _task_screen(c.from_user.id)
    await show(c, text, markup)


@router.callback_query(F.data == "sd:free")
async def task_free(c: CallbackQuery, state: FSMContext):
    await state.set_state(Submit.subject)
    await show(c, "Как называется работа? Это станет темой сообщения. Напиши одним сообщением.", kb([CANCEL]))


@router.message(Submit.subject, F.text)
async def task_free_text(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip()[:150], subj="", key="")
    text, markup, rec = await _teacher_screen(m.from_user.id, "")
    if not rec:
        await state.clear()
        return await m.answer(text, reply_markup=markup)
    await state.update_data(rec=rec)
    await state.set_state(Submit.teacher)
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("sd:"))
async def task_pick(c: CallbackQuery, state: FSMContext):
    k = c.data.split(":")[1]
    evs, _ = await service.safe_events(c.from_user.id)
    e = next((x for x in (evs or []) if service.key(x.uid) == k), None)
    if not e:
        return await safe_answer(c, "Не нашёл задание, открой «Сдать работу» заново", show_alert=True)
    clean = re.sub(r"\s*[-–]\s*срок сдачи\s*$", "", e.title)
    await state.update_data(title=f"{e.subject}: {clean}"[:150], subj=e.subject, key=k)
    text, markup, rec = await _teacher_screen(c.from_user.id, e.subject)
    if not rec:
        await state.clear()
        return await show(c, text, markup)
    await state.update_data(rec=rec)
    await state.set_state(Submit.teacher)
    await show(c, text, markup)


@router.callback_query(Submit.teacher, F.data.startswith("st:"))
async def teacher_pick(c: CallbackQuery, state: FSMContext):
    tid = c.data.split(":")[1]
    data = await state.get_data()
    name = data.get("rec", {}).get(tid)
    if not name:
        return await safe_answer(c, "Не нашёл преподавателя, начни заново", show_alert=True)
    await state.update_data(to=tid, to_name=name)
    await state.set_state(Submit.file)
    await show(c, f"📎 Пришли файл с работой документом (скрепка → Файл, не как фото).\n\n"
                  f"Работа: {data['title']}\nКому: {name}", kb([CANCEL]))


@router.message(Submit.file, F.document)
async def got_file(m: Message, state: FSMContext):
    data = await state.get_data()
    fname = m.document.file_name or "work"
    await state.update_data(file_id=m.document.file_id, fname=fname)
    await state.set_state(Submit.confirm)
    await m.answer(
        f"Проверь перед отправкой:\n\n👤 Кому: {data['to_name']}\n📝 Тема: {data['title']}\n📎 Файл: {fname}\n\n"
        f"Сообщение уйдёт настоящему преподавателю в элжур.",
        reply_markup=kb([[Btn(text="✅ Отправить", callback_data="sx:go"), *CANCEL]]))


@router.message(Submit.file)
async def not_a_file(m: Message):
    await m.answer("Нужен именно файл: нажми скрепку → «Файл» (не «Галерея»), чтобы он ушёл документом.",
                   reply_markup=kb([CANCEL]))


@router.callback_query(F.data == "sx:no")
async def cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "Отменено.", kb([HOME]))


@router.callback_query(Submit.confirm, F.data == "sx:go")
async def go(c: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await show(c, "⏳ Отправляю…")
    row = db.run("SELECT eljur_enc FROM users WHERE tg_id=?", (c.from_user.id,))
    back = [HOME]
    if not row or not row[0]["eljur_enc"]:
        return await c.message.edit_text("Нет сессии элжура: открой «⚙️ Настройка».", reply_markup=kb(back))
    try:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / data["fname"]
            await bot.download(data["file_id"], destination=path)
            await EljurClient(parse_cookies(dec(row[0]["eljur_enc"]))).send(
                [data["to"]], data["title"], f"Добрый день! Отправляю работу: {data['title']}.", path)
        done = [[Btn(text="✅ Отметить сданным", callback_data=f"dd:{data['key']}:0")]] if data.get("key") else []
        await c.message.edit_text(
            f"✅ Отправлено: {data['to_name']}.\nНе забудь сдать работу и в LMS: там сдача пока вручную.",
            reply_markup=kb(done + back))
    except InvalidToken:
        await c.message.edit_text("❌ Не могу расшифровать сессию элжура. Отправь /setcookie заново.", reply_markup=kb(back))
    except EljurError as e:
        await c.message.edit_text(f"❌ Не отправилось: {e}", reply_markup=kb(back))
    except httpx.HTTPError as e:
        await c.message.edit_text(f"❌ Нет связи с элжуром ({type(e).__name__}). Попробуй позже.", reply_markup=kb(back))
    except Exception:
        log.exception("send failed")
        await c.message.edit_text("❌ Ошибка при отправке, подробности в окне, где запущен бот.", reply_markup=kb(back))
