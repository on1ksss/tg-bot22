import sqlite3
from contextlib import closing
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  tg_id INTEGER PRIMARY KEY, ical_enc TEXT, eljur_enc TEXT, scale TEXT DEFAULT '100');
CREATE TABLE IF NOT EXISTS done(tg_id INTEGER, uid TEXT, PRIMARY KEY(tg_id, uid));
CREATE TABLE IF NOT EXISTS teachers(tg_id INTEGER, name TEXT, eljur_id TEXT, PRIMARY KEY(tg_id, name));
CREATE TABLE IF NOT EXISTS scores(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, subject TEXT, got REAL, max REAL);
CREATE TABLE IF NOT EXISTS cal(tg_id INTEGER PRIMARY KEY, ics TEXT);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, text TEXT,
  remind_at TEXT, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS totals(
  tg_id INTEGER, subject TEXT, total REAL, PRIMARY KEY(tg_id, subject));
"""


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with closing(conn()) as c:
        c.executescript(SCHEMA)
        c.commit()


def run(sql: str, args=()):
    with closing(conn()) as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.fetchall()


def ensure_user(tg_id: int):
    run("INSERT OR IGNORE INTO users(tg_id) VALUES(?)", (tg_id,))
