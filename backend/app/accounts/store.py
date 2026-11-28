"""SQLite user store: accounts, sessions and usage counters.

SQLite is fine to a few thousand users on one server. DATABASE_PATH sets the file
(default backend/.data/app.db). Passwords use scrypt (stdlib); session tokens are random and only
their SHA-256 is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .tiers import TIERS

SESSION_DAYS = 30
_LOCK = threading.Lock()


@dataclass
class User:
    id: int
    email: str
    tier: str
    is_admin: bool = False
    stripe_customer: str | None = None
    created_at: float = 0.0

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "tier": self.tier, "is_admin": self.is_admin}


def db_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", Path(__file__).resolve().parents[2] / ".data" / "app.db"))


def _conn() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


_DB: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _DB
    if _DB is None or not Path(db_path()).exists():
        _DB = _conn()
        _DB.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'free', is_admin INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            stripe_customer TEXT, stripe_subscription TEXT);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS usage (
            user_id INTEGER NOT NULL, period TEXT NOT NULL, feature TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, period, feature));
        """)
        _DB.commit()
    return _DB


def reset_for_tests() -> None:
    global _DB
    _DB = None


# ---------------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except Exception:  # noqa: BLE001
        return False


def _row_user(r) -> User | None:
    if r is None:
        return None
    return User(id=r["id"], email=r["email"], tier=r["tier"], is_admin=bool(r["is_admin"]),
                stripe_customer=r["stripe_customer"], created_at=r["created_at"])


# ---------------------------------------------------------------------- users
def create_user(email: str, pw: str, tier: str = "free") -> User:
    email = email.strip().lower()
    if "@" not in email or len(email) > 200:
        raise ValueError("Enter a valid email address.")
    if len(pw) < 8:
        raise ValueError("Use a password of at least 8 characters.")
    admins = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    with _LOCK:
        try:
            cur = db().execute("INSERT INTO users (email, pw_hash, tier, is_admin, created_at) VALUES (?,?,?,?,?)",
                               (email, hash_password(pw), tier, int(email in admins), time.time()))
            db().commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("An account with that email already exists.") from exc
    return get_user(cur.lastrowid)


def get_user(uid: int) -> User | None:
    return _row_user(db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


def get_user_by_email(email: str) -> User | None:
    return _row_user(db().execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone())


def authenticate(email: str, pw: str) -> User | None:
    r = db().execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if r is None:
        check_password(pw, hash_password("timing-equaliser"))
        return None
    return _row_user(r) if check_password(pw, r["pw_hash"]) else None


def set_tier(uid: int, tier: str, customer: str | None = None, subscription: str | None = None) -> None:
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier}")
    with _LOCK:
        db().execute("UPDATE users SET tier=?, stripe_customer=COALESCE(?, stripe_customer), "
                     "stripe_subscription=COALESCE(?, stripe_subscription) WHERE id=?", (tier, customer, subscription, uid))
        db().commit()


def user_by_customer(customer: str) -> User | None:
    return _row_user(db().execute("SELECT * FROM users WHERE stripe_customer=?", (customer,)).fetchone())


def count_users() -> dict:
    rows = db().execute("SELECT tier, COUNT(*) n FROM users GROUP BY tier").fetchall()
    return {r["tier"]: r["n"] for r in rows}


# ---------------------------------------------------------------------- sessions
def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(uid: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        db().execute("INSERT INTO sessions VALUES (?,?,?,?)", (_h(token), uid, now, now + SESSION_DAYS * 86400))
        db().execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        db().commit()
    return token


def user_for_token(token: str | None) -> User | None:
    if not token:
        return None
    r = db().execute("SELECT user_id, expires_at FROM sessions WHERE token_hash=?", (_h(token),)).fetchone()
    if r is None or r["expires_at"] < time.time():
        return None
    return get_user(r["user_id"])


def end_session(token: str | None) -> None:
    if token:
        with _LOCK:
            db().execute("DELETE FROM sessions WHERE token_hash=?", (_h(token),))
            db().commit()


# ---------------------------------------------------------------------- usage (e.g. copilot questions per month)
def usage(uid: int, feature: str, period: str) -> int:
    r = db().execute("SELECT count FROM usage WHERE user_id=? AND period=? AND feature=?", (uid, period, feature)).fetchone()
    return int(r["count"]) if r else 0


def add_usage(uid: int, feature: str, period: str, n: int = 1) -> int:
    with _LOCK:
        db().execute("INSERT INTO usage VALUES (?,?,?,?) ON CONFLICT(user_id, period, feature) DO UPDATE SET count=count+?",
                     (uid, period, feature, n, n))
        db().commit()
    return usage(uid, feature, period)
