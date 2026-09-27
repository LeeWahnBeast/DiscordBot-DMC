"""
AI Chat dùng Groq API (endpoint OpenAI-compatible) — bot trả lời cộc lốc,
kiểu gen Z nhảm nhí, thỉnh thoảng spam 2 emoji custom của bot cho vui.

Cần biến môi trường GROQ_API_KEY (lấy free tại https://console.groq.com).
GROQ_MODEL có thể override qua env nếu Groq đổi/deprecate model mặc định.
"""

import os
import json
import logging

import aiohttp

log = logging.getLogger("bot")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# 2 emoji custom của bot (application emoji — dùng được ở mọi server bot có mặt).
ICON_CUOI_CHAY_NUOC_MAT = "<:cuoichaynuocmat:1553559977421967420>"
ICON_VERITY_OM_DUNG = "<:verityomdung:1553561765361483906>"

SYSTEM_PROMPT = (
    "Mày là 1 con bot Discord tính tình gen Z, xàm xàm, ăn nói cộc lốc. "
    "LUÔN trả lời cực kỳ ngắn gọn — tối đa 1-2 câu ngắn, ưu tiên vài từ nếu "
    "được, TUYỆT ĐỐI không giải thích dài dòng, không văn vẻ, không khách sáo "
    "kiểu 'Xin chào, tôi có thể giúp gì'. Nói trổng, xưng 'tao', gọi người "
    "kia là 'mày'/'cu'/'bro' tuỳ ngữ cảnh cho vui, được phép cà khịa nhẹ. "
    "TUYỆT ĐỐI không chửi tục, không công kích ngoại hình/gia đình, không bàn "
    "chính trị/tôn giáo/nội dung 18+, không đưa lời khuyên y tế/pháp lý — gặp "
    "mấy chủ đề đó thì né sang xàm chuyện khác. "
    f"Khi hợp ngữ cảnh (buồn cười, cà khịa, đồng tình, sốc...) được lặp lại "
    f"2-3 lần 2 emoji sau để tăng kịch tính: {ICON_CUOI_CHAY_NUOC_MAT} (cười "
    f"ra nước mắt/cà khịa/nhục) và {ICON_VERITY_OM_DUNG} (đồng tình/công "
    "nhận/an ủi). Không phải câu nào cũng cần emoji, chỉ spam khi thực sự hợp. "
    "\n\nNgoài ra, thỉnh thoảng (không phải lúc nào cũng phải) mày có thể "
    "'trộn' 2 emoji Unicode thường (kiểu 😂+🔥) thành 1 ảnh nhỏ 48x48 gửi kèm "
    "câu trả lời cho vui, kiểu Emoji Kitchen của Google. Chỉ làm khi thực sự "
    "hợp ngữ cảnh, đừng lạm dụng. "
    "\n\nBẮT BUỘC trả lời CHỈ 1 object JSON hợp lệ, không kèm chữ nào khác, "
    "không markdown/backtick, đúng khuôn dạng: "
    '{"reply": "<câu trả lời ngắn>", "mix": ["<emoji1>", "<emoji2>"]} '
    'Nếu không muốn ghép emoji lần này thì để "mix": null. '
    'Mỗi phần tử trong "mix" phải là ĐÚNG 1 emoji Unicode đơn (không phải '
    "chuỗi ghép sẵn kiểu gia đình/ZWJ, không phải emoji custom của server)."
)

MAX_HISTORY_TURNS = 3  # số lượt hỏi-đáp gần nhất giữ làm ngữ cảnh (mỗi lượt = 2 message)
MAX_REPLY_CHARS = 300  # phòng AI lỡ trả lời dài, cắt bớt cho chắc


def _parse_response(content: str) -> tuple[str, list[str] | None] | None:
    """
    Parse JSON trả về từ Groq: {"reply": str, "mix": [emoji, emoji] | null}.
    Trả về (reply, mix_pair_hoac_None), hoặc None nếu parse lỗi/rỗng.
    Có fallback: nếu model lỡ trả text thường (không phải JSON), coi cả
    chuỗi đó là "reply" luôn, không chặn người dùng vì lỗi format.
    """
    text = content.strip()
    if not text:
        return None

    # Bỏ code fence nếu model lỡ bọc ```json ... ``` dù đã dặn không làm vậy.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text, None  # fallback: coi nguyên văn là câu trả lời

    if not isinstance(data, dict):
        return text, None

    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return None
    reply = reply.strip()

    mix = data.get("mix")
    mix_pair: list[str] | None = None
    if isinstance(mix, list) and len(mix) == 2 and all(isinstance(e, str) and e.strip() for e in mix):
        mix_pair = [mix[0].strip(), mix[1].strip()]

    return reply, mix_pair


async def ask_groq(
    user_text: str, history: list[dict] | None = None
) -> tuple[str, list[str] | None] | None:
    """
    Gửi 1 câu hỏi tới Groq (chat completions, OpenAI-compatible).
    Trả về (câu_trả_lời, cặp_emoji_để_mix_hoặc_None), hoặc None nếu lỗi
    (thiếu API key, mạng lỗi, model lỗi...).
    `history` là list [{"role": "user"/"assistant", "content": ...}, ...] gần nhất.
    """
    if not GROQ_API_KEY:
        log.warning("Chưa cấu hình GROQ_API_KEY trong .env, AI Chat sẽ không hoạt động.")
        return None

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 250,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                GROQ_API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning(f"Groq API trả về status {resp.status}: {body[:300]}")
                    return None
                data = await resp.json()
    except Exception:
        log.exception("Lỗi khi gọi Groq API")
        return None

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        log.warning(f"Groq API trả về dữ liệu không đúng định dạng: {data}")
        return None

    parsed = _parse_response(content)
    if parsed is None:
        log.warning(f"Không parse được nội dung Groq trả về (rỗng hoặc thiếu 'reply'): {content!r}")
        return None
    reply, mix_pair = parsed

    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS].rstrip() + "..."
    return reply, mix_pair
