"""Интерактивный интерфейс: меню, карточки сроков, заметки, калькулятор «что если»."""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from . import calculator as calc, db, notes, service, view
from .ical import MSK

router = Router()
log = logging.getLogger("ui")

MENU_TEXT = "Что сделать? Выбери кнопку 👇"


def kb(rows: list[list[Btn]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu() -> InlineKeyboardMarkup:
    return kb([
        [Btn(text="📅 Сроки", callback_data="m:dl"), Btn(text="📝 Заметки", callback_data="m:notes")],
        [Btn(text="📤 Сдать работу", callback_data="m:send"), Btn(text="🎯 Баллы и калькулятор", callback_data="m:sc")],
        [Btn(text="⚙️ Настройка", callback_data="m:setup")],
    ])


HOME = [Btn(text="🏠 Меню", callback_data="m:menu")]


async def safe_answer(c: CallbackQuery, *args, **kwargs):
    """Ответ на нажатие кнопки; устаревшие нажатия (бот был выключен) игнорируем."""
    try:
        await c.answer(*args, **kwargs)
    except TelegramBadRequest:
        pass


async def show(c: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None):
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.warning("edit failed: %s", e)
    await safe_answer(c)


class NoteAdd(StatesGroup):
    text = State(); when = State()


class CalcTotal(StatesGroup):
    total = State()


@router.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await safe_answer(c)


@router.message(Command("menu"))
async def menu_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(MENU_TEXT, reply_markup=main_menu())


@router.callback_query(F.data == "m:menu")
async def menu_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, MENU_TEXT, main_menu())


# ---------- Сроки ----------

async def deadline_view(tg_id: int, i: int = 0):
    """(текст, клавиатура) карточки срока №i."""
    evs, err = await service.safe_events(tg_id)
    if evs is None:
        return err, kb([HOME])
    now = datetime.now(MSK)
    items = service.pending(tg_id, evs, now)
    if not items:
        return "Ближайших сроков нет 🎉", kb([HOME])
    i = max(0, min(i, len(items) - 1))
    e = items[i]
    k = service.key(e.uid)
    nav = []
    if i > 0:
        nav.append(Btn(text="◀", callback_data=f"dl:{i - 1}"))
    nav.append(Btn(text=f"{i + 1}/{len(items)}", callback_data="noop"))
    if i < len(items) - 1:
        nav.append(Btn(text="▶", callback_data=f"dl:{i + 1}"))
    rows = [nav, [Btn(text="✅ Сдано", callback_data=f"dd:{k}:{i}"), Btn(text="⏰ Напомнить", callback_data=f"rm:{k}")]]
    if e.url.startswith("http"):
        rows.append([Btn(text="🔗 Открыть задание в LMS", url=e.url)])
    rows.append([Btn(text="📤 Сдать в элжур", callback_data=f"sd:{k}"), *HOME])
    return view.card_text(e, i, len(items), now), kb(rows)


@router.callback_query(F.data == "m:dl")
async def dl_first(c: CallbackQuery):
    text, markup = await deadline_view(c.from_user.id, 0)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("dl:"))
async def dl_nav(c: CallbackQuery):
    text, markup = await deadline_view(c.from_user.id, int(c.data.split(":")[1]))
    await show(c, text, markup)


async def _find(tg_id: int, k: str):
    evs, _ = await service.safe_events(tg_id)
    return next((e for e in (evs or []) if service.key(e.uid) == k), None)


@router.callback_query(F.data.startswith("dd:"))
async def dl_done(c: CallbackQuery):
    _, k, i = c.data.split(":")
    e = await _find(c.from_user.id, k)
    if e:
        db.run("INSERT OR IGNORE INTO done VALUES(?,?)", (c.from_user.id, e.uid))
    text, markup = await deadline_view(c.from_user.id, int(i))
    await show(c, ("✅ Отмечено сданным\n\n" if e else "") + text, markup)


@router.callback_query(F.data.startswith("rm:"))
async def dl_remind_menu(c: CallbackQuery):
    k = c.data.split(":")[1]
    await show(c, "Когда напомнить об этом задании?", kb([
        [Btn(text="Через 1 час", callback_data=f"rt:{k}:h1"), Btn(text="Сегодня 20:00", callback_data=f"rt:{k}:ev")],
        [Btn(text="Завтра 9:00", callback_data=f"rt:{k}:tm")],
        [Btn(text="◀ К срокам", callback_data="m:dl")],
    ]))


@router.callback_query(F.data.startswith("rt:"))
async def dl_remind_set(c: CallbackQuery):
    _, k, code = c.data.split(":")
    e = await _find(c.from_user.id, k)
    when = notes.preset(code, datetime.now(MSK))
    if not e or not when:
        return await safe_answer(c, "Не получилось, открой «Сроки» заново", show_alert=True)
    db.run("INSERT INTO notes(tg_id,text,remind_at) VALUES(?,?,?)",
           (c.from_user.id, f"Сдать: {e.subject} — {e.title[:80]}", notes.fmt(when)))
    await show(c, f"⏰ Напомню {when:%d.%m в %H:%M}.", kb([[Btn(text="◀ К срокам", callback_data="m:dl"), *HOME]]))


# ---------- Заметки ----------

def _notes_view(tg_id: int):
    rows = db.run("SELECT id, text, remind_at FROM notes WHERE tg_id=? ORDER BY remind_at IS NULL, remind_at LIMIT 15",
                  (tg_id,))
    if not rows:
        text = "Заметок пока нет. Добавь первую — я напомню в нужное время."
    else:
        lines = []
        for n, r in enumerate(rows, 1):
            t = datetime.strptime(r["remind_at"], "%Y-%m-%d %H:%M").strftime("%d.%m %H:%M") if r["remind_at"] else "без напоминания"
            lines.append(f"{n}. {r['text'][:80]} — {t}")
        text = "📝 Мои заметки:\n" + "\n".join(lines) + "\n\nНажми 🗑 с номером, чтобы удалить."
    buttons = [Btn(text=f"🗑 {n}", callback_data=f"nd:{r['id']}") for n, r in enumerate(rows, 1)]
    markup = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    markup.append([Btn(text="➕ Добавить", callback_data="na"), *HOME])
    return text, kb(markup)


@router.callback_query(F.data == "m:notes")
async def notes_cb(c: CallbackQuery):
    text, markup = _notes_view(c.from_user.id)
    await show(c, text, markup)


@router.message(Command("notes"))
async def notes_cmd(m: Message):
    text, markup = _notes_view(m.from_user.id)
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("nd:"))
async def note_delete(c: CallbackQuery):
    db.run("DELETE FROM notes WHERE id=? AND tg_id=?", (int(c.data.split(":")[1]), c.from_user.id))
    text, markup = _notes_view(c.from_user.id)
    await show(c, text, markup)


@router.callback_query(F.data == "na")
async def note_add_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(NoteAdd.text)
    await show(c, "Напиши заметку одним сообщением (например: «купить ватман»).", kb([HOME]))


@router.message(NoteAdd.text, F.text)
async def note_add_text(m: Message, state: FSMContext):
    await state.update_data(text=m.text[:500])
    await state.set_state(NoteAdd.when)
    await m.answer(
        "Когда напомнить? Нажми кнопку или напиши время, например:\n"
        "25.11 18:00 · завтра 10:00 · через 2 ч",
        reply_markup=kb([
            [Btn(text="Через 1 час", callback_data="nw:h1"), Btn(text="Сегодня 20:00", callback_data="nw:ev")],
            [Btn(text="Завтра 9:00", callback_data="nw:tm"), Btn(text="Без напоминания", callback_data="nw:no")],
        ]))


async def _save_note(tg_id: int, state: FSMContext, when):
    data = await state.get_data()
    await state.clear()
    db.run("INSERT INTO notes(tg_id,text,remind_at) VALUES(?,?,?)",
           (tg_id, data.get("text", ""), notes.fmt(when) if when else None))
    return f"✅ Заметка сохранена. Напомню {when:%d.%m в %H:%M}." if when else "✅ Заметка сохранена."


@router.callback_query(NoteAdd.when, F.data.startswith("nw:"))
async def note_when_button(c: CallbackQuery, state: FSMContext):
    when = notes.preset(c.data.split(":")[1], datetime.now(MSK))
    text = await _save_note(c.from_user.id, state, when)
    await show(c, text, kb([[Btn(text="📝 Заметки", callback_data="m:notes"), *HOME]]))


@router.message(NoteAdd.when, F.text)
async def note_when_text(m: Message, state: FSMContext):
    when = notes.parse_when(m.text, datetime.now(MSK))
    if not when:
        return await m.answer("Не понял время. Примеры: 25.11 18:00, завтра 10:00, через 2 ч")
    await m.answer(await _save_note(m.from_user.id, state, when),
                   reply_markup=kb([[Btn(text="📝 Заметки", callback_data="m:notes"), *HOME]]))


@router.callback_query(F.data.startswith("nr:"))
async def note_reminder_done(c: CallbackQuery):
    db.run("DELETE FROM notes WHERE id=? AND tg_id=?", (int(c.data.split(":")[1]), c.from_user.id))
    await show(c, "✅ Готово, заметка удалена.")


@router.callback_query(F.data.startswith("ns:"))
async def note_snooze(c: CallbackQuery):
    when = datetime.now(MSK) + timedelta(hours=1)
    db.run("UPDATE notes SET remind_at=?, sent=0 WHERE id=? AND tg_id=?",
           (notes.fmt(when), int(c.data.split(":")[1]), c.from_user.id))
    await show(c, f"⏰ Напомню ещё раз в {when:%H:%M}.")


async def note_reminders(bot: Bot):
    """Каждые 30 секунд проверяет заметки, у которых наступило время."""
    while True:
        try:
            now = notes.fmt(datetime.now(MSK))
            for r in db.run("SELECT id, tg_id, text FROM notes WHERE sent=0 AND remind_at IS NOT NULL AND remind_at<=?", (now,)):
                db.run("UPDATE notes SET sent=1 WHERE id=?", (r["id"],))
                try:
                    await bot.send_message(r["tg_id"], f"⏰ Напоминание:\n{r['text']}", reply_markup=kb([[
                        Btn(text="✅ Готово", callback_data=f"nr:{r['id']}"),
                        Btn(text="⏰ +1 час", callback_data=f"ns:{r['id']}")]]))
                except Exception:
                    log.exception("note reminder failed for %s", r["tg_id"])
        except Exception:
            log.exception("note loop failed")
        await asyncio.sleep(30)


# ---------- Баллы и калькулятор «что если» ----------

def _subjects(tg_id: int) -> list[str]:
    return [r["subject"] for r in db.run(
        "SELECT DISTINCT subject FROM scores WHERE tg_id=? ORDER BY subject", (tg_id,))]


def _scale(tg_id: int) -> str:
    r = db.run("SELECT scale FROM users WHERE tg_id=?", (tg_id,))
    return r[0]["scale"] if r else "100"


def _sums(tg_id: int, subject: str):
    r = db.run("SELECT SUM(got) g, SUM(max) mx FROM scores WHERE tg_id=? AND subject=?", (tg_id, subject))[0]
    return r["g"] or 0, r["mx"] or 0


def _total(tg_id: int, subject: str):
    r = db.run("SELECT total FROM totals WHERE tg_id=? AND subject=?", (tg_id, subject))
    return r[0]["total"] if r else None


def _chunks(buttons: list[Btn], n: int) -> list[list[Btn]]:
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


@router.callback_query(F.data == "m:sc")
async def scores_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    subs = _subjects(c.from_user.id)
    if not subs:
        return await show(c, "Баллов пока нет. Добавь их командой:\n/addscore Предмет получено максимум\n"
                             "Например: /addscore Графика 8 10", kb([HOME]))
    sc = _scale(c.from_user.id)
    lines = []
    for s in subs:
        g, mx = _sums(c.from_user.id, s)
        p = calc.percent_of(g, mx)
        lines.append(f"• {s}: {g:g}/{mx:g} ({p:.0f}%) → {calc.to_scale(p, sc)}")
    text = "🎯 Мои баллы:\n" + "\n".join(lines) + "\n\nВыбери предмет для калькулятора «что если»:"
    buttons = [Btn(text=s[:24], callback_data=f"sub:{i}") for i, s in enumerate(subs)]
    await show(c, text, kb(_chunks(buttons, 2) + [HOME]))


def _wm_screen(tg_id: int, si: int):
    subs = _subjects(tg_id)
    if si >= len(subs):
        return None
    s = subs[si]
    g, mx = _sums(tg_id, s)
    t = _total(tg_id, s)
    text = (f"📘 {s}\nСейчас {g:g} из {mx:g} за сданные работы. Максимум за семестр: {t:g}.\n\n"
            f"Сколько баллов максимум в следующей работе?")
    buttons = [Btn(text=str(n), callback_data=f"wp:{si}:{n}") for n in (5, 10, 20, 30, 50, 100)]
    return text, kb(_chunks(buttons, 3) + [
        [Btn(text="✏️ Изменить максимум за семестр", callback_data=f"tt:{si}")],
        [Btn(text="◀ Предметы", callback_data="m:sc")]])


@router.callback_query(F.data.startswith("sub:"))
async def pick_subject(c: CallbackQuery, state: FSMContext):
    si = int(c.data.split(":")[1])
    subs = _subjects(c.from_user.id)
    if si >= len(subs):
        return await scores_cb(c, state)
    if _total(c.from_user.id, subs[si]) is None:
        await state.set_state(CalcTotal.total)
        await state.update_data(si=si)
        return await show(c, f"Сколько всего баллов можно набрать за семестр по предмету «{subs[si]}»? "
                             f"Напиши число, например 100.")
    text, markup = _wm_screen(c.from_user.id, si)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("tt:"))
async def change_total(c: CallbackQuery, state: FSMContext):
    si = int(c.data.split(":")[1])
    subs = _subjects(c.from_user.id)
    if si >= len(subs):
        return await scores_cb(c, state)
    await state.set_state(CalcTotal.total)
    await state.update_data(si=si)
    await show(c, f"Сколько всего баллов можно набрать за семестр по предмету «{subs[si]}»? Напиши число.")


@router.message(CalcTotal.total, F.text)
async def total_input(m: Message, state: FSMContext):
    try:
        total = float(m.text.replace(",", ".").strip())
        assert total > 0
    except (ValueError, AssertionError):
        return await m.answer("Нужно положительное число, например 100.")
    si = (await state.get_data())["si"]
    await state.clear()
    subs = _subjects(m.from_user.id)
    if si >= len(subs):
        return await m.answer("Предмет не найден, откройте «Баллы» заново: /menu")
    db.run("INSERT OR REPLACE INTO totals VALUES(?,?,?)", (m.from_user.id, subs[si], total))
    text, markup = _wm_screen(m.from_user.id, si)
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("wm:"))
async def work_max_cb(c: CallbackQuery, state: FSMContext):
    r = _wm_screen(c.from_user.id, int(c.data.split(":")[1]))
    if r is None:
        return await scores_cb(c, state)
    await show(c, *r)


def _pct_buttons(si: int, m: int) -> list[Btn]:
    return [Btn(text=f"{p}%", callback_data=f"wi:{si}:{m}:{p}") for p in (40, 50, 60, 70, 80, 90, 100)]


@router.callback_query(F.data.startswith("wp:"))
async def pick_percent(c: CallbackQuery):
    _, si, m = c.data.split(":")
    si, m = int(si), int(m)
    await show(c, f"Работа на {m} б. На сколько процентов сдашь?",
               kb(_chunks(_pct_buttons(si, m), 4) + [[Btn(text="◀ Другая работа", callback_data=f"wm:{si}")]]))


@router.callback_query(F.data.startswith("wi:"))
async def what_if_cb(c: CallbackQuery, state: FSMContext):
    _, si, m, p = c.data.split(":")
    si, m, p = int(si), int(m), int(p)
    uid = c.from_user.id
    subs = _subjects(uid)
    if si >= len(subs):
        return await scores_cb(c, state)
    s = subs[si]
    g, mx = _sums(uid, s)
    total = _total(uid, s)
    if total is None:
        return await pick_subject(c, state)
    r = calc.scenario(g, mx, total, m, p)
    back = [[Btn(text="◀ Другая работа", callback_data=f"wm:{si}"), Btn(text="Предметы", callback_data="m:sc")]]
    if r["status"] == "invalid":
        return await show(c, f"Эта работа не помещается: до максимума за семестр ({total:g}) осталось "
                             f"только {r['left']:g} б. Выбери работу поменьше или измени максимум.",
                          kb(back))
    pd = r["percent_done"]
    lines = [f"📘 {s}",
             f"Если следующую работу ({m} б.) сдать на {p}%:",
             f"Итого {r['got']:g} из {r['done']:g} за сданные ({pd:.0f}%) → оценка {calc.to_scale(pd, _scale(uid))}",
             f"Осталось набрать из {r['remaining']:g} б. за остальные работы:", ""]
    for grade, pct, t in r["targets"]:
        if t["status"] == "reached":
            lines.append(f"🎉 На {grade} (от {pct}%) уже хватает")
        elif t["status"] == "ok":
            lines.append(f"✅ На {grade} (от {pct}%): нужно ещё {t['need']} б.")
        else:
            lines.append(f"⛔ На {grade} (от {pct}%): нужно {t['need']} б., а осталось {t['remaining']:g}")
    await show(c, "\n".join(lines), kb(_chunks(_pct_buttons(si, m), 4) + back))


# ---------- Настройка ----------

COOKIE_HELP = (
    "Как подключить элжур (один раз в сутки, пока живёт сессия):\n\n"
    "1. Открой kmpo.eljur.ru в браузере и войди.\n"
    "2. Нажми F12, открой вкладку Network, включи фильтр Doc и обнови страницу (F5).\n"
    "3. Нажми на первую строку списка → Headers → Request Headers → найди строку Cookie "
    "и скопируй всё, что идёт после двоеточия.\n"
    "4. Отправь боту: /setcookie и через пробел вставь скопированное. Бот сразу удалит сообщение.\n\n"
    "Никому не пересылай эту строку: по ней можно войти в твой аккаунт.")


@router.callback_query(F.data == "m:setup")
async def setup_cb(c: CallbackQuery):
    uid = c.from_user.id
    has_cal = bool(db.run("SELECT 1 FROM cal WHERE tg_id=?", (uid,))
                   or db.run("SELECT 1 FROM users WHERE tg_id=? AND ical_enc IS NOT NULL", (uid,)))
    has_cookie = bool(db.run("SELECT 1 FROM users WHERE tg_id=? AND eljur_enc IS NOT NULL", (uid,)))
    text = ("⚙️ Настройка\n\n"
            + ("✅ Календарь LMS подключён\n" if has_cal else
               "❌ Календарь LMS: пришли мне файл .ics (LMS → Календарь → Экспорт календаря) как документ\n")
            + ("✅ Элжур подключён (если сессия устарела, отправь /setcookie заново)" if has_cookie else
               "❌ Элжур не подключён: нужна сессия, нажми «Как подключить элжур»"))
    await show(c, text, kb([[Btn(text="ℹ️ Как подключить элжур", callback_data="m:cookiehelp")], HOME]))


@router.callback_query(F.data == "m:cookiehelp")
async def cookiehelp_cb(c: CallbackQuery):
    await show(c, COOKIE_HELP, kb([[Btn(text="◀ Настройка", callback_data="m:setup"), *HOME]]))
