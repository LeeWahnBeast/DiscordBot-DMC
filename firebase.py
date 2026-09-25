"""
Kết nối và thao tác Firebase Realtime Database.

Firebase Admin SDK là thư viện *đồng bộ* (blocking network I/O). Toàn bộ hàm
public ở đây chạy async và đẩy phần blocking vào executor riêng, để không
bao giờ đứng hình (block) vòng lặp sự kiện chính của bot khi Firebase chậm
hoặc mạng lag.
"""

import os
import json
import time
import random
import string
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import firebase_admin
from firebase_admin import credentials, db
from firebase_admin.exceptions import FirebaseError

log = logging.getLogger("firebase")

FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
FIREBASE_CREDENTIALS_FILE = os.getenv("FIREBASE_CREDENTIALS_FILE", "serviceAccountKey.json")
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")

# Executor riêng cho các lệnh gọi Firebase (SDK đồng bộ) — tránh chiếm hết
# default executor của asyncio (dùng chung với các tác vụ blocking khác).
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="firebase")

DEFAULT_USER = {
    "xp": 0,
    "level": 0,
    "aura": 0.0,
    "deltan": 0,
    "tickets": 0,
    "daily_streak": 0,
    "last_daily_date": "",  # "YYYY-MM-DD" (ngày cuối cùng nhận daily thành công)
}


class FirebaseUnavailable(Exception):
    """Raised khi không gọi được Firebase (mạng lỗi, DB lỗi...)."""


def init_firebase():
    if FIREBASE_CREDENTIALS_JSON:
        cred = credentials.Certificate(json.loads(FIREBASE_CREDENTIALS_JSON))
    else:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS_FILE)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


async def _run(func, *args, **kwargs):
    """Chạy 1 hàm blocking (SDK Firebase) trong executor riêng, bọc lỗi rõ ràng."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_executor, lambda: func(*args, **kwargs))
    except FirebaseError as e:
        log.exception("Lỗi Firebase")
        raise FirebaseUnavailable(str(e)) from e
    except Exception as e:
        log.exception("Lỗi không xác định khi gọi Firebase")
        raise FirebaseUnavailable(str(e)) from e


def _user_ref(guild_id: int, user_id: int):
    return db.reference(f"/users/{guild_id}/{user_id}")


def _guild_users_ref(guild_id: int):
    return db.reference(f"/users/{guild_id}")


# ==================== USER (đọc/ghi cơ bản) ====================
def _get_user_sync(guild_id: int, user_id: int) -> dict:
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


async def get_user(guild_id: int, user_id: int) -> dict:
    return await _run(_get_user_sync, guild_id, user_id)


def _save_user_sync(guild_id: int, user_id: int, data: dict):
    _user_ref(guild_id, user_id).update(data)


async def save_user(guild_id: int, user_id: int, data: dict):
    await _run(_save_user_sync, guild_id, user_id, data)


def _add_xp_atomic_sync(guild_id: int, user_id: int, amount: int, level_from_total_xp, aura_bonus_for_level, deltan_bonus: int) -> dict:
    """
    Cộng XP bằng Firebase transaction thật sự (đọc-tính-ghi nguyên tử), để
    tránh mất XP khi 2 nguồn (tin nhắn + voice) cộng XP cùng lúc cho 1 user
    — trường hợp get()/update() riêng lẻ có thể ghi đè lên nhau.
    """
    ref = _user_ref(guild_id, user_id)
    result_box: dict = {}

    def txn(current):
        data = dict(current) if current else dict(DEFAULT_USER)
        for k, v in DEFAULT_USER.items():
            data.setdefault(k, v)

        old_xp = data.get("xp", 0)
        old_level, _, _ = level_from_total_xp(old_xp)

        new_xp = old_xp + amount
        new_level, _, _ = level_from_total_xp(new_xp)
        levels_gained = new_level - old_level

        deltan_gained = 0
        aura_gained = 0.0
        if levels_gained > 0:
            for lvl in range(old_level + 1, new_level + 1):
                deltan_gained += deltan_bonus
                aura_gained += aura_bonus_for_level(lvl)

        data["xp"] = new_xp
        data["level"] = new_level
        data["deltan"] = data.get("deltan", 0) + deltan_gained
        data["aura"] = round(data.get("aura", 0.0) + aura_gained, 2)

        result_box["leveled_up"] = levels_gained > 0
        result_box["new_level"] = new_level
        result_box["deltan_gained"] = deltan_gained
        result_box["aura_gained"] = aura_gained
        result_box["user"] = dict(data)
        return data

    ref.transaction(txn)
    return result_box


async def add_xp_atomic(guild_id: int, user_id: int, amount: int, level_from_total_xp, aura_bonus_for_level, deltan_bonus: int) -> dict:
    return await _run(
        _add_xp_atomic_sync, guild_id, user_id, amount, level_from_total_xp, aura_bonus_for_level, deltan_bonus
    )


def _get_citizen_sync(guild_id: int, user_id: int):
    return _user_ref(guild_id, user_id).child("citizen").get()


async def get_citizen(guild_id: int, user_id: int):
    return await _run(_get_citizen_sync, guild_id, user_id)


def _create_citizen_sync(guild_id: int, user_id: int, citizen_id: str):
    _user_ref(guild_id, user_id).child("citizen").set({
        "created_at": time.time(),
        "citizen_id": citizen_id,
    })


async def create_citizen(guild_id: int, user_id: int, citizen_id: str):
    await _run(_create_citizen_sync, guild_id, user_id, citizen_id)


# ==================== BẢNG XẾP HẠNG ====================
_ALLOWED_LEADERBOARD_FIELDS = {"deltan", "level", "aura"}


def _get_leaderboard_sync(guild_id: int, field: str, limit: int) -> list[tuple[int, dict]]:
    """
    Trả về top `limit` user (user_id, data) sắp theo `field` giảm dần.
    Với "level", sắp phụ theo "xp" để tie-break công bằng (nhiều level bằng nhau).
    """
    all_users = _guild_users_ref(guild_id).get() or {}

    def sort_key(item):
        _, data = item
        if not isinstance(data, dict):
            return (0, 0)
        primary = data.get(field, 0) or 0
        secondary = (data.get("xp", 0) or 0) if field == "level" else 0
        return (primary, secondary)

    ranked = sorted(all_users.items(), key=sort_key, reverse=True)
    return [(int(uid), data) for uid, data in ranked[:limit] if isinstance(data, dict)]


async def get_leaderboard(guild_id: int, field: str, limit: int = 10) -> list[tuple[int, dict]]:
    if field not in _ALLOWED_LEADERBOARD_FIELDS:
        raise ValueError(f"Trường bảng xếp hạng không hợp lệ: {field}")
    return await _run(_get_leaderboard_sync, guild_id, field, limit)


# ==================== DAILY ====================
def _daily_state_ref():
    return db.reference("/daily_state")


def _get_daily_state_sync() -> dict:
    return _daily_state_ref().get() or {}


async def get_daily_state() -> dict:
    return await _run(_get_daily_state_sync)


def _save_daily_state_sync(data: dict):
    _daily_state_ref().set(data)


async def save_daily_state(data: dict):
    await _run(_save_daily_state_sync, data)


def _increment_daily_message_count_sync() -> int:
    ref = _daily_state_ref().child("message_count")

    def txn(current):
        return (current or 0) + 1

    result = ref.transaction(txn)
    return result if isinstance(result, int) else 0


async def increment_daily_message_count() -> int:
    return await _run(_increment_daily_message_count_sync)


# ==================== VÉ GAME (atomic bằng transaction) ====================
def _add_tickets_sync(guild_id: int, user_id: int, amount: int) -> int:
    ref = _user_ref(guild_id, user_id).child("tickets")

    def txn(current):
        return (current or 0) + amount

    result = ref.transaction(txn)
    return result if isinstance(result, int) else 0


async def add_tickets(guild_id: int, user_id: int, amount: int) -> int:
    return await _run(_add_tickets_sync, guild_id, user_id, amount)


def _use_ticket_sync(guild_id: int, user_id: int, amount: int) -> bool:
    """
    Trừ vé bằng Firebase transaction (atomic thật sự — tránh race condition
    khi 2 request trừ vé chạy song song và đều đọc cùng 1 giá trị cũ, dẫn tới
    trừ vé âm hoặc trừ 2 lần cho 1 lượt chơi).
    Trả về True nếu trừ thành công, False nếu không đủ vé.
    """
    ref = _user_ref(guild_id, user_id).child("tickets")
    ok = {"value": False}

    def txn(current):
        current = current or 0
        if current < amount:
            ok["value"] = False
            return current  # không đổi gì
        ok["value"] = True
        return current - amount

    ref.transaction(txn)
    return ok["value"]


async def use_ticket(guild_id: int, user_id: int, amount: int = 1) -> bool:
    return await _run(_use_ticket_sync, guild_id, user_id, amount)


# ==================== THÚ TỘI ẨN DANH (chống trùng ID) ====================
def _confession_ids_ref():
    return db.reference("/confession_ids")


def _generate_confession_id_sync() -> str:
    """
    Sinh 1 mã thú tội (32 ký tự) và đăng ký atomic vào Firebase để tránh 2 thú
    tội đăng cùng lúc bị trùng mã. Xác suất trùng ngẫu nhiên là gần như 0, cơ
    chế transaction chỉ để chống trường hợp cực xui.
    """
    alphabet = string.ascii_letters + string.digits + "_-"
    for _ in range(10):
        candidate = "".join(random.choices(alphabet, k=32))
        ref = _confession_ids_ref().child(candidate)
        claimed = {"value": False}

        def txn(current):
            if current is not None:
                claimed["value"] = False
                return current
            claimed["value"] = True
            return {"created_at": time.time()}

        ref.transaction(txn)
        if claimed["value"]:
            return candidate

    # Cực kỳ khó xảy ra sau 10 lần thử: thêm timestamp để chắc chắn không trùng.
    return "".join(random.choices(alphabet, k=24)) + hex(int(time.time() * 1000))[2:]


async def generate_confession_id() -> str:
    return await _run(_generate_confession_id_sync)


def _confession_counter_ref():
    return db.reference("/confession_counter")


def _next_confession_number_sync() -> int:
    """Sinh số thứ tự thú tội tăng dần (1, 2, 3...) bằng transaction, atomic
    thật sự nên 2 người gửi thú tội cùng lúc sẽ không bị trùng số thứ tự."""
    ref = _confession_counter_ref()

    def txn(current):
        return (current or 0) + 1

    result = ref.transaction(txn)
    return result if isinstance(result, int) else 1


async def next_confession_number() -> int:
    return await _run(_next_confession_number_sync)


# ==================== ĐỒNG BỘ TÊN BOT THEO TIKTOK ====================
def _tiktok_sync_state_ref():
    return db.reference("/tiktok_sync_state")


def _get_tiktok_sync_state_sync() -> dict:
    return _tiktok_sync_state_ref().get() or {}


async def get_tiktok_sync_state() -> dict:
    return await _run(_get_tiktok_sync_state_sync)


def _save_tiktok_sync_state_sync(data: dict):
    _tiktok_sync_state_ref().set(data)


async def save_tiktok_sync_state(data: dict):
    await _run(_save_tiktok_sync_state_sync, data)
