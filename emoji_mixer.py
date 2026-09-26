"""
Ghép 2 emoji thành 1 ảnh "mashup" bằng Google Emoji Kitchen (tính năng vốn có
trong Gboard) — cùng nguồn dữ liệu ảnh mà gói npm "emoji-mixer"
(https://github.com/MattFor/emoji-mixer) dùng, chỉ khác là ở đây bot tự dựng
URL và kiểm tra thẳng bằng HTTP thay vì tải cả file dữ liệu tương thích (rất
nặng, ~20MB+) vào bộ nhớ mỗi lần chạy.

Cách hoạt động: ảnh ghép của Google được lưu tại 1 URL có dạng cố định:
    https://www.gstatic.com/android/keyboard/emojikitchen/{ngày}/u{cp1}/u{cp1}_u{cp2}.png
"ngày" là ngày phát hành đợt ghép đó (không phải mọi cặp emoji đều có ảnh ghép
— Google vẽ tay thủ công từng cặp). Vì không có sẵn danh sách đầy đủ các cặp
hợp lệ, bot thử lần lượt một số mốc ngày đã biết + cả 2 thứ tự trái/phải rồi
dùng HEAD request để kiểm tra ảnh có tồn tại không.
"""

import logging

import aiohttp

log = logging.getLogger("emoji_mixer")

BASE_URL = "https://www.gstatic.com/android/keyboard/emojikitchen"

# Một số mốc ngày phát hành ảnh ghép Emoji Kitchen đã biết (mới nhất trước,
# để ưu tiên bản vẽ mới nhất nếu 1 cặp tồn tại ở nhiều đợt). Có thể bổ sung
# thêm khi Google phát hành đợt mới.
KNOWN_DATES = [
    "20231113", "20230821", "20230301", "20221107", "20220815",
    "20220203", "20211115", "20210830", "20201001",
]


def _to_codepoints(emoji: str) -> str | None:
    """
    Chuyển 1 emoji thành chuỗi codepoint dạng Google dùng trong URL, ví dụ
    "🔥" -> "1f525", "❤️" -> "2764" (bỏ variation selector FE0F nếu có).
    Trả về None nếu input không phải đúng 1 emoji hợp lệ.
    """
    text = emoji.strip()
    if not text:
        return None

    codepoints = [ord(ch) for ch in text if ord(ch) != 0xFE0F]  # bỏ VS16
    if not codepoints:
        return None

    # 1 emoji hợp lệ chỉ nên gồm 1-2 codepoint "thấy được" (vd ZWJ sequence
    # sẽ không khớp Emoji Kitchen nên chặn luôn cho đơn giản/an toàn).
    if any(ch == 0x200D for ch in codepoints):  # ZWJ
        return None

    return "_".join(f"{cp:x}" for cp in codepoints)


def _build_url(date: str, left_cp: str, right_cp: str) -> str:
    return f"{BASE_URL}/{date}/u{left_cp}/u{left_cp}_u{right_cp}.png"


async def find_emoji_mix_url(emoji1: str, emoji2: str) -> dict:
    """
    Trả về:
      {"ok": True, "url": str}                    nếu tìm thấy ảnh ghép
      {"ok": False, "reason": "invalid"}          nếu emoji đầu vào không hợp lệ
      {"ok": False, "reason": "not_found"}        nếu không có ảnh ghép nào khớp
      {"ok": False, "reason": "network"}          nếu lỗi mạng khi kiểm tra
    """
    left_cp = _to_codepoints(emoji1)
    right_cp = _to_codepoints(emoji2)
    if not left_cp or not right_cp:
        return {"ok": False, "reason": "invalid"}

    candidates = []
    for date in KNOWN_DATES:
        candidates.append(_build_url(date, left_cp, right_cp))
        if left_cp != right_cp:
            candidates.append(_build_url(date, right_cp, left_cp))

    try:
        async with aiohttp.ClientSession() as session:
            for url in candidates:
                try:
                    async with session.head(
                        url, timeout=aiohttp.ClientTimeout(total=6), allow_redirects=True
                    ) as resp:
                        if resp.status == 200:
                            return {"ok": True, "url": url}
                except Exception:
                    continue
    except Exception:
        log.exception(f"Lỗi mạng khi kiểm tra emoji mix: {emoji1} + {emoji2}")
        return {"ok": False, "reason": "network"}

    return {"ok": False, "reason": "not_found"}
