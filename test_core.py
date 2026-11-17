import os, sqlite3, tempfile, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ical import parse_ics, upcoming, MSK
from app import calculator as c


def test_ical():
    ev = parse_ics(Path(__file__).with_name("sample.ics").read_text(encoding="utf-8"))
    assert len(ev) == 10
    first = ev[0]
    assert first.subject == "Основы проектной деятельности"
    assert first.due.strftime("%d.%m %H:%M") == "05.11 00:00"  # 04.11 21:00 UTC -> Москва
    soon = upcoming(ev, datetime(2026, 11, 9, tzinfo=MSK), 7)
    assert [e.due.day for e in soon] == [15]


def test_calc():
    assert c.to_scale(90, "5") == "5" and c.to_scale(60, "5") == "3" and c.to_scale(40, "5") == "2"
    assert c.to_scale(100, "12") == "12"
    r = c.needed_points(got=40, done_max=60, total_max=100, target_percent=70)
    assert r == {"need": 30, "remaining": 40, "possible": True}
    assert not c.needed_points(10, 90, 100, 85)["possible"]
    assert c.what_if(40, 100, 30) == 70


def test_backup():
    d = tempfile.mkdtemp()
    os.environ["DB_PATH"] = f"{d}/bot.db"; os.environ["BACKUP_DIR"] = f"{d}/b"
    con = sqlite3.connect(os.environ["DB_PATH"])
    con.executescript("CREATE TABLE users(a); CREATE TABLE done(b); INSERT INTO users VALUES(1);"); con.commit(); con.close()
    from app import backup
    p = backup.make_backup()
    assert backup.verify(p)


def test_eljur_helpers():
    try:
        from app.eljur import find_csrf, find_upload_url, parse_cookies
    except ImportError:
        return print("httpx не установлен, тест eljur пропущен")
    assert find_csrf('<input type="hidden" id="csrf" name="csrf" value="abc123">') == "abc123"
    html = 'x "https:\\/\\/storage14.eljur.ru\\/storage\\/upload?token=T.T.T&amp;domain=kmpo" y'
    assert find_upload_url(html) == "https://storage14.eljur.ru/storage/upload?token=T.T.T&domain=kmpo"
    assert parse_cookies("a=1; b=2") == {"a": "1", "b": "2"}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("OK", n)
