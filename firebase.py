"""
Kết nối và thao tác Firebase Realtime Database.
"""

import os
import json
import time
import threading

import firebase_admin
from firebase_admin import credentials, db

FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
FIREBASE_CREDENTIALS_FILE = os.getenv("FIREBASE_CREDENTIALS_FILE", "serviceAccountKey.json")
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")

_lock = threading.Lock()

DEFAULT_USER = {
    "xp": 0,
    "level": 0,
    "aura": 0.0,
    "deltan": 0,
    "tickets": 0,
    "daily_streak": 0,
    "last_daily_date": "",  # "YYYY-MM-DD" (ngày cuối cùng nhận daily thành công)
}


def init_firebase():
    if FIREBASE_CREDENTIALS_JSON:
        cred = credentials.Certificate(json.loads(FIREBASE_CREDENTIALS_JSON))
    else:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS_FILE)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


def _user_ref(guild_id: int, user_id: int):
    return db.reference(f"/users/{guild_id}/{user_id}")


def get_user(guild_id: int, user_id: int) -> dict:
    with _lock:
        ref = _user_ref(guild_id, user_id)
        data = ref.get()
        if not data:
            data = dict(DEFAULT_USER)
            ref.set(data)
        else:
            missing = {k: v for k, v in DEFAULT_USER.items() if k not in data}
            if missing:
                data.update(missing)
                ref.update(missing)
        return data


def save_user(guild_id: int, user_id: int, data: dict):
    with _lock:
        _user_ref(guild_id, user_id).update(data)


def get_citizen(guild_id: int, user_id: int):
    with _lock:
        return _user_ref(guild_id, user_id).child("citizen").get()


def create_citizen(guild_id: int, user_id: int, citizen_id: str):
    with _lock:
        _user_ref(guild_id, user_id).child("citizen").set({
            "created_at": time.time(),
            "citizen_id": citizen_id,
        })


# ==================== DAILY ====================
def _daily_state_ref():
    return db.reference("/daily_state")


def get_daily_state() -> dict:
    """
    Trạng thái container daily hiện tại trong kênh:
    {"message_id": int, "date": "YYYY-MM-DD", "message_count": int}
    """
    with _lock:
        return _daily_state_ref().get() or {}


def save_daily_state(data: dict):
    with _lock:
        _daily_state_ref().set(data)


def increment_daily_message_count() -> int:
    with _lock:
        ref = _daily_state_ref().child("message_count")
        current = ref.get() or 0
        new_value = current + 1
        ref.set(new_value)
        return new_value


# ==================== VÉ GAME ====================
def add_tickets(guild_id: int, user_id: int, amount: int) -> int:
    with _lock:
        ref = _user_ref(guild_id, user_id).child("tickets")
        current = ref.get() or 0
        new_value = current + amount
        ref.set(new_value)
        return new_value


def use_ticket(guild_id: int, user_id: int, amount: int = 1) -> bool:
    """Trừ vé nếu đủ, trả về True nếu thành công, False nếu không đủ vé."""
    with _lock:
        ref = _user_ref(guild_id, user_id).child("tickets")
        current = ref.get() or 0
        if current < amount:
            return False
        ref.set(current - amount)
        return True
