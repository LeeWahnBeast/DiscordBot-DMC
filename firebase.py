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
    "tickets": 5,
    "tickets_last_regen": 0.0,  # timestamp lần hồi vé gần nhất (tính lazy)
    "tickets_date": "",         # "YYYY-MM-DD" ngày bình vé được reset đầy gần nhất
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


# ==================== VÉ GAME (bình vé hồi theo giờ, atomic) ====================
def _add_tickets_sync(guild_id: int, user_id: int, amount: int) -> int:
    ref = _user_ref(guild_id, user_id).child("tickets")

    def txn(current):
        return (current or 0) + amount

    result = ref.transaction(txn)
    return result if isinstance(result, int) else 0


async def add_tickets(guild_id: int, user_id: int, amount: int) -> int:
    return await _run(_add_tickets_sync, guild_id, user_id, amount)


# ==================== THƯỞNG/PHẠT DELTAN & AURA (mini-game mới) ====================
def _add_deltan_sync(guild_id: int, user_id: int, amount: int) -> int:
    ref = _user_ref(guild_id, user_id).child("deltan")

    def txn(current):
        return max(0, (current or 0) + amount)

    result = ref.transaction(txn)
    return result if isinstance(result, int) else 0


async def add_deltan(guild_id: int, user_id: int, amount: int) -> int:
    """Cộng (hoặc trừ nếu amount âm) Deltan, không cho xuống dưới 0."""
    return await _run(_add_deltan_sync, guild_id, user_id, amount)


def _spend_deltan_sync(guild_id: int, user_id: int, amount: int) -> dict:
    """
    Trừ Deltan bằng transaction trên toàn bộ user (atomic — tránh 2 lệnh /tặng
    hoặc /đổi-vé cùng lúc gây trừ âm). Trả về {"ok": bool, "deltan": int} với
    "deltan" là số dư SAU khi trừ (nếu ok) hoặc số dư HIỆN TẠI (nếu không đủ).
    """
    ref = _user_ref(guild_id, user_id)
    result_box = {"ok": False, "deltan": 0}

    def txn(current):
        data = dict(current) if current else dict(DEFAULT_USER)
        for k, v in DEFAULT_USER.items():
            data.setdefault(k, v)

        if data.get("deltan", 0) < amount:
            result_box["ok"] = False
            result_box["deltan"] = data.get("deltan", 0)
            return data  # không trừ gì

        data["deltan"] -= amount
        result_box["ok"] = True
        result_box["deltan"] = data["deltan"]
        return data

    ref.transaction(txn)
    return result_box


async def spend_deltan(guild_id: int, user_id: int, amount: int) -> dict:
    """Trừ Deltan nếu đủ số dư (dùng cho /tặng, /đổi-vé). Xem _spend_deltan_sync."""
    return await _run(_spend_deltan_sync, guild_id, user_id, amount)


def _add_aura_sync(guild_id: int, user_id: int, amount: float) -> float:
    ref = _user_ref(guild_id, user_id).child("aura")

    def txn(current):
        return round(max(0.0, (current or 0.0) + amount), 2)

    result = ref.transaction(txn)
    return result if isinstance(result, (int, float)) else 0.0


async def add_aura(guild_id: int, user_id: int, amount: float) -> float:
    """Cộng (hoặc trừ nếu amount âm) Aura, không cho xuống dưới 0."""
    return await _run(_add_aura_sync, guild_id, user_id, amount)


def _regen_tickets(data: dict, now: float, max_tickets: int, regen_seconds: int, today: str) -> dict:
    """
    Tính lại số vé hiện tại của user theo cơ chế "bình vé":
      - Mỗi ngày mới (so với tickets_date lưu trong data) reset về đầy bình.
      - Trong ngày, mỗi `regen_seconds` giây thì hồi thêm 1 vé (không vượt
        quá max_tickets), tính lazy dựa trên tickets_last_regen thay vì cần
        1 task chạy nền riêng.
    Trả về data đã cập nhật field "tickets", "tickets_last_regen", "tickets_date".
    """
    last_date = data.get("tickets_date", "")
    tickets = data.get("tickets", max_tickets)
    last_regen = data.get("tickets_last_regen", now)

    if last_date != today:
        # Sang ngày mới: reset đầy bình.
        tickets = max_tickets
        last_regen = now
    elif tickets < max_tickets:
        elapsed = max(0, now - last_regen)
        regenerated = int(elapsed // regen_seconds)
        if regenerated > 0:
            tickets = min(max_tickets, tickets + regenerated)
            last_regen = last_regen + regenerated * regen_seconds

    data["tickets"] = tickets
    data["tickets_last_regen"] = last_regen
    data["tickets_date"] = today
    return data


def _get_ticket_state_sync(guild_id: int, user_id: int, max_tickets: int, regen_seconds: int, today: str) -> dict:
    """
    Đọc số vé hiện tại của user, tự động áp dụng hồi vé nếu cần, rồi lưu lại
    Firebase nếu có thay đổi (để tickets_last_regen luôn phản ánh đúng).
    Trả về dict user đầy đủ (đã tính hồi vé).
    """
    ref = _user_ref(guild_id, user_id)
    data = ref.get() or dict(DEFAULT_USER)
    for k, v in DEFAULT_USER.items():
        data.setdefault(k, v)

    before = (data.get("tickets"), data.get("tickets_date"))
    data = _regen_tickets(data, time.time(), max_tickets, regen_seconds, today)
    after = (data.get("tickets"), data.get("tickets_date"))

    if before != after:
        ref.update({
            "tickets": data["tickets"],
            "tickets_last_regen": data["tickets_last_regen"],
            "tickets_date": data["tickets_date"],
        })
    return data


async def get_ticket_state(guild_id: int, user_id: int, max_tickets: int, regen_seconds: int, today: str) -> dict:
    return await _run(_get_ticket_state_sync, guild_id, user_id, max_tickets, regen_seconds, today)


def _use_ticket_sync(guild_id: int, user_id: int, amount: int, max_tickets: int, regen_seconds: int, today: str) -> dict:
    """
    Trừ vé bằng Firebase transaction (atomic — tránh 2 request trừ vé cùng
    lúc gây trừ âm hoặc trừ 2 lần cho 1 lượt chơi). Trước khi trừ, áp dụng
    hồi vé theo thời gian ngay trong transaction để luôn nhất quán.
    Trả về {"ok": bool, "tickets": int, "next_regen_in": int|None}
    next_regen_in là số giây còn lại tới khi vé kế tiếp hồi (None nếu đã đầy bình).
    """
    ref = _user_ref(guild_id, user_id)
    result_box = {"ok": False, "tickets": 0, "next_regen_in": None}

    def txn(current):
        data = dict(current) if current else dict(DEFAULT_USER)
        for k, v in DEFAULT_USER.items():
            data.setdefault(k, v)

        data = _regen_tickets(data, time.time(), max_tickets, regen_seconds, today)

        if data["tickets"] < amount:
            result_box["ok"] = False
            result_box["tickets"] = data["tickets"]
            remaining = regen_seconds - ((time.time() - data["tickets_last_regen"]) % regen_seconds)
            result_box["next_regen_in"] = int(remaining)
            return data  # không trừ gì, nhưng vẫn lưu lại tickets đã hồi

        data["tickets"] -= amount
        result_box["ok"] = True
        result_box["tickets"] = data["tickets"]
        result_box["next_regen_in"] = None if data["tickets"] >= max_tickets else int(regen_seconds - ((time.time() - data["tickets_last_regen"]) % regen_seconds))
        return data

    ref.transaction(txn)
    return result_box


async def use_ticket(guild_id: int, user_id: int, amount: int, max_tickets: int, regen_seconds: int, today: str) -> dict:
    return await _run(_use_ticket_sync, guild_id, user_id, amount, max_tickets, regen_seconds, today)


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
