import sqlite3
import os
import hashlib
import secrets
from contextlib import contextmanager

DB_PATH = os.environ.get("HUB_DB", os.path.join(os.path.dirname(__file__), "..", "hub.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT 'campus',          -- campus | elite
    gateway_key TEXT,                            -- personal New API key (server side only)
    compute_minutes INTEGER NOT NULL DEFAULT 120 -- remaining compute minutes
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL DEFAULT '新对话',
    model TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS instances (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    spec TEXT NOT NULL,
    image TEXT NOT NULL,
    status TEXT NOT NULL,              -- pending | running | stopped | rejected | failed
    simulated INTEGER NOT NULL DEFAULT 0,
    requested_minutes INTEGER NOT NULL,
    reserved_minutes INTEGER NOT NULL DEFAULT 0,
    used_minutes INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    expires_at TEXT,
    container_id TEXT,
    ssh_host TEXT, ssh_port INTEGER, ssh_user TEXT, ssh_password TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS compute_ledger (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    instance_id INTEGER,
    delta_minutes INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with tx() as c:
        c.executescript(SCHEMA)


def hash_pw(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    salt, _ = stored.split("$", 1)
    return secrets.compare_digest(hash_pw(pw, salt), stored)
