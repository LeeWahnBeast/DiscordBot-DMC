"""
Lấy thông tin công khai (tên hiển thị + ảnh đại diện) của một tài khoản TikTok
bằng cách đọc dữ liệu JSON nhúng sẵn trong trang profile (không cần API
chính thức / không cần đăng nhập).
"""

import re
import json
import logging

import aiohttp

log = logging.getLogger("tiktok")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_REHYDRATION_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_SIGI_STATE_RE = re.compile(
    r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
    re.DOTALL,
)


async def fetch_tiktok_profile(username: str) -> dict | None:
    """
    Trả về {"nickname": str, "avatar_url": str} cho @username,
    hoặc None nếu không lấy được (lỗi mạng / TikTok đổi cấu trúc trang / chặn bot...).
    """
    url = f"https://www.tiktok.com/@{username}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    log.warning(f"TikTok trả về status {resp.status} cho @{username}")
                    return None
                html = await resp.text()
    except Exception:
        log.exception(f"Lỗi khi tải trang TikTok @{username}")
        return None

    data = _extract_user_data(html)
    if not data:
        log.warning(f"Không parse được dữ liệu user từ HTML TikTok @{username}")
    return data


def _extract_user_data(html: str) -> dict | None:
    match = _REHYDRATION_RE.search(html)
    if match:
        try:
            raw = json.loads(match.group(1))
            user_info = raw["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]["user"]
            return _pick_fields(user_info)
        except Exception:
            pass

    match = _SIGI_STATE_RE.search(html)
    if match:
        try:
            raw = json.loads(match.group(1))
            users = raw["UserModule"]["users"]
            user_info = users[next(iter(users))]
            return _pick_fields(user_info)
        except Exception:
            pass

    return None


def _pick_fields(user_info: dict) -> dict | None:
    nickname = user_info.get("nickname") or user_info.get("uniqueId")
    avatar_url = (
        user_info.get("avatarLarger")
        or user_info.get("avatarMedium")
        or user_info.get("avatarThumb")
    )
    if not nickname or not avatar_url:
        return None
    return {"nickname": nickname, "avatar_url": avatar_url}


async def download_bytes(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return await resp.read()
            log.warning(f"Tải ảnh thất bại, status {resp.status}: {url}")
    except Exception:
        log.exception(f"Lỗi khi tải ảnh: {url}")
    return None
