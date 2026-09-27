"""
AI Chat dùng Groq API (endpoint OpenAI-compatible) — bot trả lời cộc lốc,
kiểu gen Z nhảm nhí, thỉnh thoảng chèn emoji custom của bot cho vui.

Cần biến môi trường GROQ_API_KEY (lấy free tại https://console.groq.com).
GROQ_MODEL có thể override qua env nếu Groq đổi/deprecate model mặc định.
"""

import os
import logging

import aiohttp

log = logging.getLogger("bot")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Emoji custom của bot (application emoji — dùng được ở mọi server bot có mặt).
# Model chỉ cần chèn thẳng chuỗi <:tên:id> vào câu trả lời — Discord tự hiển
# thị nhỏ gọn ngay lập tức, không cần tải/ghép ảnh gì thêm (khác với cách
# Emoji Kitchen cũ, vừa chậm vừa hay lỗi do phải dò nhiều URL).
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
    f"Khi hợp ngữ cảnh (buồn cười, cà khịa, đồng tình, sốc...) được chèn thẳng "
    f"vào câu trả lời, lặp lại 2-3 lần, 1 trong 2 emoji sau để tăng kịch tính: "
    f"{ICON_CUOI_CHAY_NUOC_MAT} (cười ra nước mắt/cà khịa/nhục) hoặc "
    f"{ICON_VERITY_OM_DUNG} (đồng tình/công nhận/an ủi). Không phải câu nào "
    "cũng cần emoji, chỉ chèn khi thực sự hợp, đừng lạm dụng. Chỉ dùng đúng 2 "
    "emoji này, không tự bịa emoji custom khác."
)

MAX_HISTORY_TURNS = 3  # số lượt hỏi-đáp gần nhất giữ làm ngữ cảnh (mỗi lượt = 2 message)
MAX_REPLY_CHARS = 300  # phòng AI lỡ trả lời dài, cắt bớt cho chắc


async def ask_groq(user_text: str, history: list[dict] | None = None) -> str | None:
    """
    Gửi 1 câu hỏi tới Groq (chat completions, OpenAI-compatible), trả về câu
    trả lời dạng text (có thể kèm emoji custom <:tên:id> chèn sẵn trong
    chuỗi), hoặc None nếu lỗi (thiếu API key, mạng lỗi, model lỗi...).
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
        "max_tokens": 200,
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
        reply = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        log.warning(f"Groq API trả về dữ liệu không đúng định dạng: {data}")
        return None

    if not reply:
        return None
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS].rstrip() + "..."
    return reply
