"""Резервная копия SQLite с проверкой восстановления и ротацией."""
import sqlite3
import sys
import time
from pathlib import Path

from .config import BACKUP_DIR, DB_PATH, ADMIN_ID, BOT_TOKEN

KEEP = 7


def make_backup() -> Path:
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    dst = Path(BACKUP_DIR) / f"bot-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(DB_PATH)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)  # корректная копия даже при работающем боте
    src.close(); out.close()
    return dst


def verify(path: Path) -> bool:
    """Пробное восстановление: открыть копию и проверить целостность и таблицы."""
    c = sqlite3.connect(path)
    ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c.close()
    return ok and {"users", "done"} <= tables


def rotate():
    files = sorted(Path(BACKUP_DIR).glob("bot-*.db"))
    for f in files[:-KEEP]:
        f.unlink()


def notify(text: str):
    if not (ADMIN_ID and BOT_TOKEN):
        print(text); return
    import httpx
    httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
               json={"chat_id": ADMIN_ID, "text": text}, timeout=15)


if __name__ == "__main__":
    try:
        p = make_backup()
        if verify(p):
            rotate(); notify(f"✅ Бэкап выполнен: {p.name}, {p.stat().st_size // 1024} КБ")
        else:
            notify(f"❌ Бэкап {p.name} не прошёл проверку восстановления"); sys.exit(1)
    except Exception as e:
        notify(f"❌ Бэкап упал: {e}"); sys.exit(1)
