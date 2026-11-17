"""Отправка сообщения с файлом в элжур (kmpo.eljur.ru).

ВАЖНО: схема восстановлена по скриншотам DevTools и НЕ проверена на живом журнале.
Места, помеченные CHECK, — предположения. Первая пробная отправка — только себе.
Вход (логин) здесь не реализован: клиент использует cookie уже открытой сессии.
"""
import logging
import re
from pathlib import Path

import httpx

from .config import ELJUR_BASE, ELJUR_UA


UPLOAD_FIELD = "userfile"  # подтверждено на живом элжуре


log = logging.getLogger("eljur")


class EljurError(Exception):
    pass


def parse_cookies(raw: str) -> dict:
    """'a=1; b=2' -> {'a': '1', 'b': '2'}"""
    return dict(p.strip().split("=", 1) for p in raw.split(";") if "=" in p)


def find_csrf(html: str) -> str:
    # CHECK: порядок атрибутов в теге может отличаться
    for pat in (r'name="csrf"[^>]*value="([^"]+)"', r'value="([^"]+)"[^>]*name="csrf"',
                r'id="csrf"[^>]*value="([^"]+)"'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise EljurError("не найден csrf на странице (сессия устарела?)")


def find_upload_url(html: str) -> str:
    html = html.replace("\\/", "/")
    m = re.search(r'https://storage\d+\.eljur\.ru/storage/upload\?token=[^"\'\s<>]+', html)
    if not m:
        raise EljurError("не найден адрес загрузки файла")
    return m.group(0).replace("&amp;", "&")


class EljurClient:
    def __init__(self, cookies: dict):
        self.cookies = cookies

    async def recipients(self) -> list[dict]:
        """Преподаватели, которым студент может писать: [{id, name, short, info}]."""
        api = f"{ELJUR_BASE}/journal-api-messages-action"
        headers = {"User-Agent": ELJUR_UA, "X-Requested-With": "XMLHttpRequest",
                   "Referer": f"{ELJUR_BASE}/journal-messages-compose-action"}

        async def call(c, params):
            r = await c.get(api, params=params)
            try:
                data = r.json()
            except ValueError:
                raise EljurError("сессия элжура истекла, отправьте /setcookie заново")
            if not data.get("result"):
                raise EljurError("элжур не отдал список получателей")
            return data

        found: dict[str, dict] = {}
        async with httpx.AsyncClient(cookies=self.cookies, timeout=30, follow_redirects=True, headers=headers) as c:
            struct = await call(c, {"method": "messages.get_recipient_structure", "isParentFeedBack": "undefined"})
            for top in struct.get("structure", []):
                if top.get("key") != "school":
                    continue
                for sub in top.get("data", []):
                    for entry in sub.get("data", []):
                        lst = await call(c, {"method": "messages.get_recipients_list", "key1": "school",
                                             "key2": sub["key"], "key3": entry["key"], "dep": "null"})
                        for u in lst.get("user_list", []):
                            uid = str(u.get("id", ""))
                            last = (u.get("lastname") or "").strip()
                            if not uid.isdigit() or last.lower() == "вакансия":
                                continue
                            f, m = (u.get("firstname") or "").strip(), (u.get("middlename") or "").strip()
                            found[uid] = {"id": uid, "name": " ".join(x for x in (last, f, m) if x),
                                          "short": f"{last} {f[:1]}." + (f"{m[:1]}." if m else ""),
                                          "info": (u.get("info") or "").strip()}
        return sorted(found.values(), key=lambda t: t["name"])

    async def send(self, receivers: list[str], subject: str, message: str, file: Path | None):
        async with httpx.AsyncClient(cookies=self.cookies, timeout=60, follow_redirects=True,
                                     headers={"User-Agent": ELJUR_UA}) as c:
            page = await c.get(f"{ELJUR_BASE}/journal-messages-compose-action")
            if "login" in str(page.url).lower():
                raise EljurError("сессия элжура истекла, обновите cookie")
            csrf = find_csrf(page.text)
            vfiles = []
            if file:
                up = find_upload_url(page.text)
                headers = {"Origin": ELJUR_BASE, "Referer": f"{ELJUR_BASE}/journal-messages-compose-action"}
                with open(file, "rb") as f:
                    r = await c.post(up, files={UPLOAD_FIELD: (file.name, f)}, headers=headers)
                try:
                    data = r.json()
                except ValueError:
                    raise EljurError(f"хранилище ответило {r.status_code}: {r.text[:200]!r}")
                if not data.get("success"):
                    raise EljurError(f"загрузка файла не удалась: {data}")
                vfiles = [{"fid": data["file"], "filename": file.name}]  # CHECK: другие поля
            payload = {
                "csrf": csrf, "subject": subject, "message": message,
                "receivers": receivers, "individually": False,
                "resources": [], "response_id": "0", "vfiles": vfiles,
            }
            r = await c.post(f"{ELJUR_BASE}/journal-messages-send-action/", json=payload)
            res = r.json()
            if not res.get("success"):
                raise EljurError(res.get("error") or "элжур вернул ошибку")
