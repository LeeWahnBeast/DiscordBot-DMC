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
