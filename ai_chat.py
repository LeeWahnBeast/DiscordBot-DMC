"""
AI Chat dùng các API dạng OpenAI-compatible — bot trả lời cộc lốc, kiểu gen Z
nhảm nhí, thỉnh thoảng chèn emoji custom hoặc ghép 2 emoji Unicode (Emoji
Kitchen) cho vui.

Thử lần lượt nhiều provider (Groq trước, hết quota thì tự chuyển sang
OpenRouter) để đỡ bị chặn khi 1 bên hết hạn ngạch free tier. Chỉ khi TẤT CẢ
provider đều hết quota thì mới coi là "hết token" thật sự.

Cần biến môi trường GROQ_API_KEY (lấy free tại https://console.groq.com).
OPENROUTER_API_KEY (lấy free tại https://openrouter.ai/keys) là tuỳ chọn —
có thì bot tự dùng làm fallback khi Groq hết quota, không có thì bỏ qua.
GROQ_MODEL/OPENROUTER_MODEL có thể override qua env nếu provider đổi/deprecate
model mặc định.
"""

import os
import re
import logging

import aiohttp

log = logging.getLogger("bot")

# ==================== Emoji & marker mix (dùng chung cho mọi provider) ====================

# Emoji custom của bot (application emoji — dùng được ở mọi server bot có mặt).
# Model chỉ cần chèn thẳng chuỗi <:tên:id> vào câu trả lời — Discord tự hiển
# thị nhỏ gọn ngay lập tức, không cần tải/ghép ảnh gì thêm.
ICON_CUOI_CHAY_NUOC_MAT = "<:cuoichaynuocmat:1553559977421967420>"
ICON_VERITY_OM_DUNG = "<:verityomdung:1553561765361483906>"

# Model đánh dấu chỗ muốn ghép 2 emoji Unicode bằng cú pháp [[mix:😂+🔥]]
# ngay trong câu trả lời (text thuần, KHÔNG dùng JSON — tránh lỗi cũ "hết
# max_tokens giữa chừng khi ép JSON"). Bot tách marker này ra bằng regex, gọi
# emoji_mixer để lấy ảnh, rồi xoá marker khỏi câu trả lời text.
MIX_MARKER_RE = re.compile(r"\[\[mix:\s*([^\s+\]]+)\s*\+\s*([^\s+\]]+)\s*\]\]")

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
    "emoji này, không tự bịa emoji custom khác. "
    "\n\nNgoài ra, thỉnh thoảng (không bắt buộc, đừng lạm dụng) mày có thể "
    "ghép 2 emoji Unicode thường thành 1 ảnh mashup nhỏ cho vui, kiểu Emoji "
    "Kitchen của Google. Muốn ghép thì chèn NGUYÊN VĂN cú pháp "
    "[[mix:emoji1+emoji2]] vào chỗ muốn hiện ảnh trong câu trả lời, ví dụ "
    "'Vậy á [[mix:😂+🔥]]'. Chỉ dùng 2 emoji Unicode đơn (không phải chuỗi "
    "ghép sẵn kiểu gia đình/ZWJ, không phải emoji custom của server), và chỉ "
    "chèn TỐI ĐA 1 marker [[mix:...]] mỗi câu trả lời."
)

MAX_HISTORY_TURNS = 3  # số lượt hỏi-đáp gần nhất giữ làm ngữ cảnh (mỗi lượt = 2 message)
MAX_REPLY_CHARS = 300  # phòng AI lỡ trả lời dài, cắt bớt cho chắc

# Các mẫu nhận diện lỗi "hết quota/rate limit" trong body lỗi API trả về, để
# phân biệt với lỗi khác (mạng, model sai, format sai...). Dùng chung cho mọi
# provider vì các API OpenAI-compatible thường báo lỗi theo mẫu tương tự.
_QUOTA_ERROR_MARKERS = (
    "rate_limit_exceeded",
    "insufficient_quota",
    "quota",
    "rate limit",
)


class ProviderQuotaExhausted(Exception):
    """Raised khi 1 provider báo hết quota/rate limit (status 429 hoặc lỗi quota rõ ràng)."""


class AllProvidersExhausted(Exception):
    """Raised khi TẤT CẢ provider trong danh sách đều hết quota/rate limit."""


def _extract_mix_marker(reply: str) -> tuple[str, tuple[str, str] | None]:
    """
    Tách marker [[mix:emoji1+emoji2]] đầu tiên (nếu có) ra khỏi `reply`.
    Trả về (reply_đã_xoá_marker, (emoji1, emoji2) hoặc None).
    Nếu có nhiều hơn 1 marker, chỉ lấy cái đầu tiên, các cái sau bị xoá luôn
    (phòng model lỡ chèn nhiều lần dù đã dặn tối đa 1 cái).
    """
    match = MIX_MARKER_RE.search(reply)
    if not match:
        return reply, None
    emoji1, emoji2 = match.group(1), match.group(2)
    cleaned = MIX_MARKER_RE.sub("", reply).strip()
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)  # dọn khoảng trắng thừa sau khi xoá marker
    return cleaned, (emoji1, emoji2)


# ==================== Danh sách provider (OpenAI-compatible) ====================
# Mỗi provider là 1 dict {"name", "api_key", "api_url", "model"}. Thử lần
# lượt theo thứ tự trong list; provider nào thiếu api_key thì tự bị bỏ qua
# (không raise lỗi, coi như provider đó không được cấu hình).

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# OpenRouter — fallback khi Groq hết quota. Free tier riêng biệt với Groq nên
# tổng công suất 2 bên cộng lại cao hơn dùng 1 mình Groq. Model mặc định là
# 1 model ":free" (không tốn phí) — đổi qua OPENROUTER_MODEL nếu cần.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

_PROVIDERS = [
    {
        "name": "Groq",
        "api_key": GROQ_API_KEY,
        "api_url": GROQ_API_URL,
        "model": GROQ_MODEL,
        "extra_headers": {},
    },
    {
        "name": "OpenRouter",
        "api_key": OPENROUTER_API_KEY,
        "api_url": OPENROUTER_API_URL,
        "model": OPENROUTER_MODEL,
        # OpenRouter khuyến khích 2 header này để định danh app (không bắt
        # buộc để chạy được, nhưng giúp tránh bị coi là traffic vô danh).
        "extra_headers": {
            "HTTP-Referer": "https://github.com/",
            "X-Title": "Discord Bot DMC",
        },
    },
]


async def _call_provider(provider: dict, messages: list[dict]) -> str | None:
    """
    Gọi 1 provider cụ thể (payload OpenAI chat completions chuẩn).
    Trả về nội dung reply (text thô, chưa xử lý mix marker/cắt độ dài), hoặc
    None nếu lỗi thường (không phải lỗi quota — lỗi quota sẽ raise thẳng
    ProviderQuotaExhausted để `ask_ai` biết mà thử provider kế tiếp).
    """
    payload = {
        "model": provider["model"],
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 200,
    }
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
        **provider["extra_headers"],
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                provider["api_url"], json=payload, timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    is_quota_error = resp.status == 429 or any(
                        marker in body.lower() for marker in _QUOTA_ERROR_MARKERS
                    )
                    if is_quota_error:
                        log.warning(f"{provider['name']} báo hết quota/rate limit (status {resp.status}): {body[:300]}")
                        raise ProviderQuotaExhausted(f"{provider['name']}: {body[:300]}")
                    log.warning(f"{provider['name']} trả về status {resp.status}: {body[:300]}")
                    return None
                data = await resp.json()
    except ProviderQuotaExhausted:
        raise
    except Exception:
        log.exception(f"Lỗi khi gọi {provider['name']}")
        return None

    try:
        reply = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        log.warning(f"{provider['name']} trả về dữ liệu không đúng định dạng: {data}")
        return None

    return reply or None


async def ask_ai(
    user_text: str, history: list[dict] | None = None
) -> tuple[str, tuple[str, str] | None] | None:
    """
    Gửi 1 câu hỏi tới AI, thử lần lượt các provider trong _PROVIDERS (provider
    thiếu api_key tự bị bỏ qua). Trả về (câu_trả_lời, cặp_emoji_để_mix_hoặc_
    None), hoặc None nếu lỗi thường ở provider cuối cùng còn lại (mạng lỗi,
    model lỗi, format sai...).
    Raise AllProvidersExhausted nếu MỌI provider đã cấu hình đều báo hết
    quota/rate limit, để nơi gọi phân biệt được với lỗi thường và xử lý
    (báo + lưu DB) khác đi.
    `history` là list [{"role": "user"/"assistant", "content": ...}, ...] gần nhất.
    """
    configured = [p for p in _PROVIDERS if p["api_key"]]
    if not configured:
        log.warning("Chưa cấu hình GROQ_API_KEY/OPENROUTER_API_KEY trong .env, AI Chat sẽ không hoạt động.")
        return None

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_text})

    reply = None
    exhausted_providers: list[str] = []

    for provider in configured:
        try:
            reply = await _call_provider(provider, messages)
        except ProviderQuotaExhausted:
            exhausted_providers.append(provider["name"])
            continue  # thử provider kế tiếp trong danh sách

        if reply:
            if provider is not configured[0]:
                log.info(f"Đã fallback sang {provider['name']} thành công.")
            break

    if reply is None:
        if exhausted_providers and len(exhausted_providers) == len(configured):
            # Tất cả provider đã cấu hình đều hết quota -> báo lỗi riêng.
            raise AllProvidersExhausted(", ".join(exhausted_providers))
        return None

    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS].rstrip() + "..."

    reply, mix_pair = _extract_mix_marker(reply)
    if not reply and not mix_pair:
        return None
    return reply, mix_pair


# Alias giữ tương thích ngược với code cũ gọi ask_groq() trực tiếp.
ask_groq = ask_ai
GroqQuotaExhausted = AllProvidersExhausted
