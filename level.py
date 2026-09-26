"""
Logic XP / Level / Aura / Deltan + giao diện Components V2 (LevelUp, Hồ sơ công dân,
Bảng xếp hạng, Thú tội ẩn danh).
"""

import time
import random
import string
import datetime

import discord

import firebase
from wordlist import WORDLE_WORDS_EN, WORDLE_WORDS_VI

# Server chạy bot (Render) dùng giờ UTC, nhưng daily/streak phải theo giờ Việt
# Nam (UTC+7) chứ không phải giờ hệ thống — cố định offset vì VN không có DST.
VN_TZ = datetime.timezone(datetime.timedelta(hours=7))


def now_vn() -> datetime.datetime:
    return datetime.datetime.now(VN_TZ)

# ==================== ICON ====================
ICON_XP = "<:xp:1553010318861537380>"
ICON_LEVEL = "<:level:1553010322070306826>"
ICON_AURA = "<:aura:1553010328424546364>"
ICON_DELTAN = "<:deltan:1553010324758593649>"
ICON_ADMIN = "<:admin:1553016118430408764>"
ICON_MOD = "<:mod:1553016085140349069>"
ICON_CROWN = "<:vuongmien:1553254452083822692>"
ICON_STREAK = "<:streak:1553254951709581373>"
ICON_BADGE = "<:huyhieu:1553175460534423683>"
ICON_PARTY = "<:party:1553274852016791562>"
ICON_TOP1 = "<:top1:1553282917357199400>"
ICON_TOP2 = "<:top2:1553282914459066370>"
ICON_TOP3 = "<:top3:1553282911761989642>"
ICON_CUP = "<:cup:1553283351346020392>"
ICON_WARNING_CHECK = "<:warning_check:1553283556367925308>"  # đã "check" xong nhưng vẫn là 1 dạng nhắc nhở
ICON_BULLET = "<:bullet_triangle_glass_blue:1553284598375653437>"

ICON_CHECK = "<:dautich:1553019524335271996>"
ICON_CROSS = "<:daucheo:1553019526772170762>"
ICON_WARNING = "<:warning:1553019521164509264>"
ICON_TICKET = "<:ticket:1553020189589774336>"

# ==================== CẤU HÌNH XP ====================
MESSAGE_XP_MIN, MESSAGE_XP_MAX = 15, 25
MESSAGE_XP_COOLDOWN = 60  # giây

VOICE_XP_MIN, VOICE_XP_MAX = 8, 15
VOICE_XP_INTERVAL_SECONDS = 60
VOICE_REQUIRE_NOT_ALONE = True
VOICE_IGNORE_IF_MUTED_DEAFENED = True
VOICE_IGNORE_AFK_CHANNEL = True

LEVEL_UP_DELTAN_BONUS = 15
LEVEL_UP_AURA_BASE_PERCENT = 0.5

# Giới hạn an toàn: nếu 1 user có total_xp vượt mốc này, không cố tính level
# bằng vòng lặp cộng dồn nữa (tránh loop cực lâu / DoS do dữ liệu bất thường).
MAX_SANE_TOTAL_XP = 5_000_000_000

CITIZEN_ROLE_NAME = "Công Dân"

# ==================== CẤU HÌNH DAILY ====================
DAILY_CHANNEL_ID = 1552242897116594227
DAILY_OPEN_HOUR = 0    # 0:00 sáng
DAILY_CLOSE_HOUR = 10  # đến 10:00 sáng
DAILY_REWARD_DELTAN = 5
DAILY_MAX_MESSAGES_BEFORE_RESEND = 30  # quá 30 tin nhắn thì gửi lại container mới

# ==================== CẤU HÌNH VÉ GAME (bình vé hồi theo giờ) ====================
GAME_TICKET_COST = 1        # số vé tốn mỗi lượt chơi bất kỳ mini game nào
TICKETS_MAX = 5             # tối đa 5 vé (reset đầy mỗi ngày mới)
TICKETS_REGEN_SECONDS = 3 * 60 * 60  # mỗi vé đã dùng hồi lại sau 3 tiếng
GAME_COOLDOWN_SECONDS = 5 * 60  # chơi xong 1 ván (bất kỳ game nào) phải đợi 5 phút mới chơi tiếp

DELTAN_PER_TICKET = 15      # giá quy đổi trong /deltan-shop (Deltan -> vé game)
GIFT_MIN_DELTAN = 1         # số Deltan tối thiểu có thể tặng qua lệnh /tặng

# ==================== CẤU HÌNH THÚ TỘI ẨN DANH ====================
CONFESSION_CHANNEL_ID = 1539855082210861126

# ==================== CẤU HÌNH BẢNG XẾP HẠNG ====================
LEADERBOARD_FIELDS = {
    "deltan": {"label": "Deltan", "icon": ICON_DELTAN, "fmt": lambda v: f"{int(v):,}"},
    "level": {"label": "Level", "icon": ICON_LEVEL, "fmt": lambda v: f"{int(v):,}"},
    "aura": {"label": "Aura", "icon": ICON_AURA, "fmt": lambda v: f"{v:,.2f}"},
}
LEADERBOARD_PAGE_SIZE = 10
LEADERBOARD_MAX_FETCH = 50  # lấy tối đa 50 người để phân trang (5 trang x 10)


# ==================== CÔNG THỨC XP / LEVEL (kiểu MEE6) ====================
def xp_required_for_level(level: int) -> int:
    return 5 * (level ** 2) + 50 * level + 100


def level_from_total_xp(total_xp: int):
    """
    Trả về (level, xp_trong_level_hiện_tại, xp_cần_để_lên_level_kế).
    Dùng vòng lặp nhưng chặn ở MAX_SANE_TOTAL_XP để không bao giờ loop vô hạn
    / quá lâu nếu dữ liệu XP bị hỏng hoặc bị thao túng bất thường.
    """
    total_xp = max(0, min(int(total_xp), MAX_SANE_TOTAL_XP))
    level = 0
    remaining = total_xp
    while True:
        need = xp_required_for_level(level)
        if remaining < need:
            return level, remaining, need
        remaining -= need
        level += 1


def aura_bonus_for_level(new_level: int) -> float:
    return round(LEVEL_UP_AURA_BASE_PERCENT * new_level, 2)


# ==================== CỘNG XP ====================
async def add_xp(guild_id: int, user_id: int, amount: int) -> dict:
    """
    Cộng XP bằng Firebase transaction nguyên tử (đọc-tính-ghi trong 1 bước ở
    tầng Firebase) — tránh mất XP/level khi tin nhắn và voice XP cộng cùng
    lúc cho cùng 1 user (trước đây get() rồi update() riêng lẻ có thể bị
    ghi đè nếu 2 event chạy gần như đồng thời).
    """
    return await firebase.add_xp_atomic(
        guild_id, user_id, amount, level_from_total_xp, aura_bonus_for_level, LEVEL_UP_DELTAN_BONUS
    )


def random_message_xp() -> int:
    return random.randint(MESSAGE_XP_MIN, MESSAGE_XP_MAX)


def random_voice_xp() -> int:
    return random.randint(VOICE_XP_MIN, VOICE_XP_MAX)


def generate_citizen_id() -> str:
    return "CD-" + "".join(random.choices(string.digits, k=6))


# ==================== DAILY ====================
def is_daily_open(now: datetime.datetime | None = None) -> bool:
    """Daily mở từ 0:00 đến trước 10:00 sáng, theo giờ Việt Nam (UTC+7)."""
    hour = (now or now_vn()).hour
    return DAILY_OPEN_HOUR <= hour < DAILY_CLOSE_HOUR


def today_str() -> str:
    return now_vn().strftime("%Y-%m-%d")


def yesterday_str() -> str:
    return (now_vn() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


async def claim_daily(guild_id: int, user_id: int) -> dict:
    """
    Xử lý nhận daily cho user. Trả về:
      {"ok": True/False, "reason": str nếu False,
       "streak": int, "deltan_gained": int, "total_deltan": int}
    Cộng dồn streak nếu nhận liên tục mỗi ngày; nếu bỏ lỡ 1 ngày thì streak reset về 1.
    """
    user = await firebase.get_user(guild_id, user_id)
    today = today_str()

    if user.get("last_daily_date") == today:
        return {"ok": False, "reason": "already_claimed"}

    streak = user.get("daily_streak", 0)
    if user.get("last_daily_date") == yesterday_str():
        streak += 1
    else:
        streak = 1

    new_deltan = user.get("deltan", 0) + DAILY_REWARD_DELTAN
    await firebase.save_user(guild_id, user_id, {
        "deltan": new_deltan,
        "daily_streak": streak,
        "last_daily_date": today,
    })

    return {
        "ok": True,
        "streak": streak,
        "deltan_gained": DAILY_REWARD_DELTAN,
        "total_deltan": new_deltan,
    }


async def daily_status_icon(guild_id: int, user_id: int) -> str:
    """Icon trạng thái daily hôm nay của 1 user: đã nhận / chưa nhận / cảnh báo sắp lỡ."""
    user = await firebase.get_user(guild_id, user_id)
    today = today_str()
    if user.get("last_daily_date") == today:
        return ICON_CHECK
    if is_daily_open():
        return ICON_WARNING
    return ICON_CROSS


DAILY_STREAK_WEEK_SLOTS = 8  # "1 tuần" hiển thị = 8 ô icon cố định
ICON_STREAK_EMPTY = "<:checkbox:1553253919549956256>"  # ô chưa tới / chưa điểm danh


def format_daily_streak(user_data: dict) -> str:
    """
    Chuỗi 8 icon cố định biểu diễn daily streak trong "1 tuần", ví dụ:
    ✅✅⚠️<empty><empty><empty><empty><empty>
      - Mỗi ✅ là 1 ngày đã điểm danh liên tục trong streak hiện tại.
      - Icon kế tiếp là trạng thái HÔM NAY: ✅ nếu đã điểm danh (gộp luôn vào
        chuỗi check ở trên, không có icon riêng), ⚠️ nếu daily đang mở nhưng
        chưa điểm danh (sắp mất streak nếu không bấm kịp), ❌ nếu đã lỡ.
      - Các ô còn lại đến hết 8 ô là icon rỗng (chưa tới ngày đó).
      - Streak dài hơn 8 thì thêm số streak thật ở đầu, ví dụ [icon lửa] 12 ngày · ...
    """
    streak = user_data.get("daily_streak", 0)
    claimed_today = user_data.get("last_daily_date") == today_str()

    if claimed_today:
        checks = min(streak, DAILY_STREAK_WEEK_SLOTS)
        icons = ICON_CHECK * checks
        remaining = DAILY_STREAK_WEEK_SLOTS - checks
    else:
        checks = min(streak, DAILY_STREAK_WEEK_SLOTS - 1)
        status = ICON_WARNING if is_daily_open() else ICON_CROSS
        icons = (ICON_CHECK * checks) + status
        remaining = DAILY_STREAK_WEEK_SLOTS - checks - 1

    icons += ICON_STREAK_EMPTY * max(remaining, 0)

    if streak > DAILY_STREAK_WEEK_SLOTS:
        return f"{ICON_STREAK} **{streak}** ngày · {icons}"
    return icons


class DailyClaimView(discord.ui.LayoutView):
    """Container Components V2 hiển thị nút nhận daily."""

    def __init__(self):
        super().__init__(timeout=None)

        lines = [
            f"## {ICON_DELTAN} ĐIỂM DANH HẰNG NGÀY",
            f"Bấm nút bên dưới để nhận **{DAILY_REWARD_DELTAN} {ICON_DELTAN} Deltan** mỗi ngày.",
            f"-# Mở từ {DAILY_OPEN_HOUR:02d}:00 đến {DAILY_CLOSE_HOUR:02d}:00 sáng. "
            "Nhận liên tục mỗi ngày để cộng dồn streak — bỏ lỡ 1 ngày sẽ mất streak.",
        ]

        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(DailyClaimButton()),
            accent_color=discord.Colour.green(),
        )
        self.add_item(container)


class DailyClaimButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Nhận Daily",
            style=discord.ButtonStyle.success,
            emoji="🎁",
            custom_id="daily_claim_button",
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
            return

        if not is_daily_open():
            await interaction.response.send_message(
                f"{ICON_CROSS} Daily chỉ mở từ {DAILY_OPEN_HOUR:02d}:00 đến {DAILY_CLOSE_HOUR:02d}:00 sáng thôi nha!",
                ephemeral=True,
            )
            return

        # Ack ngay trong 3s đầu tiên (trước khi gọi Firebase) để tránh lỗi
        # "The application did not respond" nếu Firebase phản hồi chậm.
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await claim_daily(interaction.guild.id, interaction.user.id)
        except firebase.FirebaseUnavailable:
            await interaction.followup.send(
                f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!",
                ephemeral=True,
            )
            return

        if not result["ok"]:
            await interaction.followup.send(
                f"{ICON_WARNING_CHECK} Bạn đã nhận daily hôm nay rồi, quay lại vào ngày mai nhé!",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"{ICON_CHECK} Bạn nhận được **+{result['deltan_gained']} {ICON_DELTAN} Deltan**! "
            f"{ICON_STREAK} Streak hiện tại: **{result['streak']}** ngày.",
            ephemeral=True,
        )


# ==================== VÉ GAME / MINI GAME ====================
GAME_CHOICES = [
    "đoán số", "kéo búa bao", "xúc xắc", "wordle",
    "tài xỉu", "đoán chất bài", "vòng quay may mắn", "mở rương kho báu",
    "tính nhẩm", "trí nhớ",
    "đoán cờ", "đoán quốc gia", "đoán văn hoá", "đoán ứng dụng", "đoán truyền thống",
]

# Mô tả ngắn + danh mục dùng cho menu /game — game nào cần may mắn, game nào cần kỹ năng.
GAME_CATEGORIES = [
    {"key": "luck", "emoji": "🍀", "title": "May Rủi", "desc": "Ăn thua theo xác suất, càng khó thưởng càng cao."},
    {"key": "logic", "emoji": "🧠", "title": "Trí Tuệ - Logic", "desc": "Tính toán, ghi nhớ, đoán từ — không may rủi."},
    {"key": "knowledge", "emoji": "📚", "title": "Trí Tuệ - Kiến Thức", "desc": "Đoán cờ, quốc gia, văn hoá, ứng dụng, truyền thống."},
]

GAME_LIST_INFO = [
    {"key": "guess", "emoji": "🔢", "title": "Đoán Số", "type": "🍀 May rủi", "category": "luck",
     "desc": "Đoán 1 số bí mật từ 1-10. Đoán đúng nhận lại vé + Deltan."},
    {"key": "rps", "emoji": "✊", "title": "Kéo Búa Bao", "type": "🍀 May rủi", "category": "luck",
     "desc": "Oẳn tù tì với bot, thắng nhận lại vé + Deltan, hòa hoàn vé."},
    {"key": "dice", "emoji": "🎲", "title": "Xúc Xắc", "type": "🍀 May rủi", "category": "luck",
     "desc": "Đoán xúc xắc ra Cao (4-6) hay Thấp (1-3)."},
    {"key": "taixiu", "emoji": "🎲", "title": "Tài Xỉu", "type": "🍀 May rủi", "category": "luck",
     "desc": "Đoán tổng 3 viên xúc xắc là Tài (11-18) hay Xỉu (3-10)."},
    {"key": "bai", "emoji": "🃏", "title": "Đoán Chất Bài", "type": "🍀 May rủi", "category": "luck",
     "desc": "Đoán chất (♠♥♦♣) của lá bài được rút ngẫu nhiên."},
    {"key": "vongquay", "emoji": "🎡", "title": "Vòng Quay May Mắn", "type": "🍀 May rủi", "category": "luck",
     "desc": "Chọn 1 trong 6 ô số, trúng ô của mình thì ăn thưởng."},
    {"key": "ruong", "emoji": "🗝️", "title": "Mở Rương Kho Báu", "type": "🍀 May rủi", "category": "luck",
     "desc": "Chọn 1 trong 12 rương, mở đúng rương kho báu thưởng cực lớn."},
    {"key": "wordle", "emoji": "🟩", "title": "Wordle", "type": "🧠 Trí tuệ", "category": "logic",
     "desc": "Đoán từ 5 chữ trong 6 lượt (tiếng Anh hoặc tiếng Việt không dấu)."},
    {"key": "mathquiz", "emoji": "🧮", "title": "Tính Nhẩm", "type": "🧠 Trí tuệ", "category": "logic",
     "desc": "Giải 1 phép tính trong thời gian giới hạn — càng nhanh & đúng càng nhiều thưởng."},
    {"key": "memory", "emoji": "🧩", "title": "Trí Nhớ", "type": "🧠 Trí tuệ", "category": "logic",
     "desc": "Ghi nhớ và bấm lại đúng thứ tự dãy số vừa hiện ra."},
    {"key": "flag", "emoji": "🚩", "title": "Đoán Cờ", "type": "🧠 Trí tuệ", "category": "knowledge",
     "desc": "Nhìn lá cờ, chọn đúng tên quốc gia trong 4 lựa chọn."},
    {"key": "country", "emoji": "🌍", "title": "Đoán Quốc Gia", "type": "🧠 Trí tuệ", "category": "knowledge",
     "desc": "Đọc gợi ý (thủ đô, đặc điểm nổi bật...) rồi đoán đúng quốc gia."},
    {"key": "culture", "emoji": "🎎", "title": "Đoán Văn Hoá", "type": "🧠 Trí tuệ", "category": "knowledge",
     "desc": "Đoán biểu tượng/nét văn hoá thuộc về quốc gia hay vùng nào."},
    {"key": "app", "emoji": "📱", "title": "Đoán Ứng Dụng", "type": "🧠 Trí tuệ", "category": "knowledge",
     "desc": "Đoán tên ứng dụng/mạng xã hội qua biểu tượng emoji + mô tả gợi ý."},
    {"key": "tradition", "emoji": "🏮", "title": "Đoán Truyền Thống", "type": "🧠 Trí tuệ", "category": "knowledge",
     "desc": "Đoán tên lễ hội/phong tục truyền thống qua mô tả gợi ý."},
]

GAME_LIST_BY_KEY = {info["key"]: info for info in GAME_LIST_INFO}

# Deltan thưởng thêm cho 3 game cũ khi thắng (ngoài phần thưởng vé cũ), tính
# theo cùng công thức độ khó dùng cho 4 game mới bên dưới (xác suất thắng
# càng thấp thì thưởng càng cao).
GUESS_NUMBER_DELTAN_REWARD = 8   # thắng 1/10 ~ 10%
RPS_DELTAN_REWARD = 4            # thắng 1/3 ~ 33%
DICE_DELTAN_REWARD = 1           # thắng 1/2 ~ 50%

# ---- 4 game mới: thưởng/phạt bằng Deltan + Aura thay vì vé ----
# Thưởng tăng dần theo độ khó (xác suất thắng càng thấp thì thưởng càng cao):
#   Deltan: 1 -> 8, Aura: 0.5 -> 3.0. Thua thì bị trừ đúng số Aura lẽ ra được
# thưởng nếu thắng (game càng khó thì thua cũng mất càng nhiều Aura).
GAME_DEFS = {
    "taixiu": {
        "title": "🎲 Tài Xỉu",
        "desc": "Xúc xắc 3 viên: tổng 3-10 là **Xỉu**, 11-18 là **Tài**. Đoán đúng ăn thưởng!",
        "win_probability": 0.5,
        "deltan_reward": 1,
        "aura_reward": 0.5,
        "options": [("tài", "Tài", "🔴"), ("xỉu", "Xỉu", "⚪")],
    },
    "bai": {
        "title": "🃏 Đoán Chất Bài",
        "desc": "Rút 1 lá bài ngẫu nhiên, đoán đúng chất (♠ ♥ ♦ ♣) để ăn thưởng!",
        "win_probability": 0.25,
        "deltan_reward": 5,
        "aura_reward": 2.0,
        "options": [("♠", "Bích", "♠️"), ("♥", "Cơ", "♥️"), ("♦", "Rô", "♦️"), ("♣", "Chuồn", "♣️")],
    },
    "vongquay": {
        "title": "🎡 Vòng Quay May Mắn",
        "desc": "Chọn 1 trong 6 ô số, vòng quay dừng đúng ô của bạn thì ăn thưởng!",
        "win_probability": 1 / 6,
        "deltan_reward": 7,
        "aura_reward": 2.5,
        "options": [(str(n), str(n), "🔹") for n in range(1, 7)],
    },
    "ruong": {
        "title": "🗝️ Mở Rương Kho Báu",
        "desc": "Chọn 1 trong 12 rương, mở đúng rương có kho báu thì ăn thưởng cực lớn!",
        "win_probability": 1 / 12,
        "deltan_reward": 8,
        "aura_reward": 3.0,
        "options": [(str(n), str(n), "📦") for n in range(1, 13)],
    },
}

_CARD_SUITS = [("♠", "Bích"), ("♥", "Cơ"), ("♦", "Rô"), ("♣", "Chuồn")]
_CARD_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

# ---- Wordle ----
WORDLE_WORD_LENGTH = 5
WORDLE_MAX_GUESSES = 6
# Thắng/thua thưởng-phạt Deltan/Aura giống nhóm "4 game mới": xác suất thắng
# thực tế của Wordle khó hơn hẳn (phải đoán đúng cả từ trong 6 lượt) nên đặt
# mức thưởng cao nhất trong tất cả các mini game.
WORDLE_DELTAN_REWARD = 10
WORDLE_AURA_REWARD = 3.5

WORDLE_SQUARE_CORRECT = "🟩"   # đúng chữ, đúng vị trí
WORDLE_SQUARE_PRESENT = "🟨"   # đúng chữ, sai vị trí
WORDLE_SQUARE_ABSENT = "⬛"    # không có trong từ


def wordle_pick_word(mode: str) -> str:
    """mode: 'en' (tiếng Anh) hoặc 'vi' (tiếng Việt không dấu)."""
    pool = WORDLE_WORDS_EN if mode == "en" else WORDLE_WORDS_VI
    return random.choice(pool)


def wordle_score_guess(guess: str, secret: str) -> list[str]:
    """
    Chấm 1 lượt đoán theo đúng luật Wordle (xử lý đúng cả trường hợp chữ
    cái lặp lại trong từ bí mật): trả về list ký hiệu (🟩/🟨/⬛) theo từng
    vị trí của `guess`.
    """
    n = len(secret)
    result = [WORDLE_SQUARE_ABSENT] * n
    secret_letters = list(secret)

    # Bước 1: đánh dấu đúng vị trí trước, "tiêu thụ" chữ cái đó khỏi secret_letters
    for i in range(n):
        if guess[i] == secret[i]:
            result[i] = WORDLE_SQUARE_CORRECT
            secret_letters[i] = None

    # Bước 2: những ô còn lại, nếu chữ cái còn "tồn kho" trong secret_letters
    # (chưa bị dùng ở bước 1) thì đánh dấu sai vị trí, rồi tiêu thụ luôn.
    for i in range(n):
        if result[i] == WORDLE_SQUARE_CORRECT:
            continue
        if guess[i] in secret_letters:
            result[i] = WORDLE_SQUARE_PRESENT
            secret_letters[secret_letters.index(guess[i])] = None

    return result


def play_new_game(game_key: str, guess_value: str) -> dict:
    """Xử lý 1 lượt chơi cho 4 game mới (taixiu/bai/vongquay/ruong)."""
    if game_key == "taixiu":
        rolls = [random.randint(1, 6) for _ in range(3)]
        total = sum(rolls)
        actual = "tài" if total >= 11 else "xỉu"
        return {
            "win": guess_value == actual,
            "actual_label": f"**{actual.capitalize()}** ({'+'.join(map(str, rolls))} = {total})",
        }
    if game_key == "bai":
        actual_symbol, actual_name = random.choice(_CARD_SUITS)
        rank = random.choice(_CARD_RANKS)
        return {
            "win": guess_value == actual_symbol,
            "actual_label": f"**{rank}{actual_symbol}** ({actual_name})",
        }
    if game_key == "vongquay":
        actual = str(random.randint(1, 6))
        return {"win": guess_value == actual, "actual_label": f"**{actual}**"}
    if game_key == "ruong":
        actual = str(random.randint(1, 12))
        return {"win": guess_value == actual, "actual_label": f"Rương số **{actual}**"}
    raise ValueError(f"Unknown game_key: {game_key}")


def play_guess_number(guess: int) -> dict:
    """Đoán số bí mật từ 1-10. Đoán đúng thắng, nhận thêm 1 vé."""
    secret = random.randint(1, 10)
    win = guess == secret
    return {"win": win, "secret": secret}


def play_rps(choice: str) -> dict:
    """Kéo búa bao: choice trong {'kéo','búa','bao'}."""
    options = ["kéo", "búa", "bao"]
    bot_choice = random.choice(options)
    beats = {"kéo": "bao", "búa": "kéo", "bao": "búa"}
    if choice == bot_choice:
        result = "draw"
    elif beats.get(choice) == bot_choice:
        result = "win"
    else:
        result = "lose"
    return {"result": result, "bot_choice": bot_choice}


def play_dice(guess_high_low: str) -> dict:
    """Xúc xắc: đoán 'cao' (4-6) hay 'thấp' (1-3)."""
    roll = random.randint(1, 6)
    actual = "cao" if roll >= 4 else "thấp"
    win = guess_high_low == actual
    return {"win": win, "roll": roll, "actual": actual}


# ---- Tính Nhẩm (không may rủi — đúng luôn thắng, sai luôn thua) ----
MATH_QUIZ_DELTAN_REWARD = 4
MATH_QUIZ_AURA_REWARD = 1.5


def generate_math_question() -> dict:
    """Sinh 1 phép tính ngẫu nhiên (+ - x) với số vừa phải, trả về đề bài + đáp án."""
    op = random.choice(["+", "-", "x"])
    if op == "x":
        a, b = random.randint(2, 12), random.randint(2, 12)
        answer = a * b
    elif op == "+":
        a, b = random.randint(10, 99), random.randint(10, 99)
        answer = a + b
    else:
        a = random.randint(10, 99)
        b = random.randint(10, a)  # đảm bảo không ra số âm
        answer = a - b
    return {"question": f"{a} {op} {b}", "answer": answer}


def check_math_answer(question_state: dict, guess_text: str) -> dict:
    guess_text = guess_text.strip().replace(" ", "")
    try:
        guess = int(guess_text)
    except ValueError:
        return {"win": False, "invalid": True}
    return {"win": guess == question_state["answer"], "invalid": False}


# ---- Trí Nhớ (không may rủi — nhớ đúng dãy số thì thắng) ----
MEMORY_SEQUENCE_LENGTH = 4
MEMORY_DELTAN_REWARD = 6
MEMORY_AURA_REWARD = 2.0


def generate_memory_sequence(length: int = MEMORY_SEQUENCE_LENGTH) -> list[int]:
    """Sinh dãy số ngẫu nhiên không lặp lại liên tiếp, mỗi số từ 1-9."""
    seq = [random.randint(1, 9)]
    for _ in range(length - 1):
        nxt = random.randint(1, 9)
        while nxt == seq[-1]:
            nxt = random.randint(1, 9)
        seq.append(nxt)
    return seq


# ---- 5 game "đoán trắc nghiệm" (Cờ / Quốc gia / Văn hoá / Ứng dụng / Truyền thống) ----
# Tất cả đều là game trí tuệ — không có yếu tố may rủi, chọn đúng luôn thắng.
QUIZ_DELTAN_REWARD = 5
QUIZ_AURA_REWARD = 1.5

# Mỗi mục: (emoji/hiển thị, đáp án đúng, list đáp án gây nhiễu)
FLAG_QUESTIONS = [
    ("🇻🇳", "Việt Nam", ["Trung Quốc", "Lào", "Campuchia"]),
    ("🇯🇵", "Nhật Bản", ["Hàn Quốc", "Trung Quốc", "Thái Lan"]),
    ("🇰🇷", "Hàn Quốc", ["Nhật Bản", "Triều Tiên", "Mông Cổ"]),
    ("🇺🇸", "Mỹ (Hoa Kỳ)", ["Anh", "Canada", "Úc"]),
    ("🇬🇧", "Anh", ["Mỹ (Hoa Kỳ)", "Pháp", "Úc"]),
    ("🇫🇷", "Pháp", ["Ý", "Hà Lan", "Đức"]),
    ("🇩🇪", "Đức", ["Bỉ", "Áo", "Ba Lan"]),
    ("🇮🇹", "Ý", ["Pháp", "Mexico", "Ireland"]),
    ("🇪🇸", "Tây Ban Nha", ["Bồ Đào Nha", "Mexico", "Colombia"]),
    ("🇧🇷", "Brazil", ["Bồ Đào Nha", "Argentina", "Colombia"]),
    ("🇨🇳", "Trung Quốc", ["Việt Nam", "Đài Loan", "Singapore"]),
    ("🇹🇭", "Thái Lan", ["Lào", "Campuchia", "Myanmar"]),
    ("🇮🇳", "Ấn Độ", ["Pakistan", "Bangladesh", "Sri Lanka"]),
    ("🇷🇺", "Nga", ["Serbia", "Slovenia", "Ukraina"]),
    ("🇨🇦", "Canada", ["Mỹ (Hoa Kỳ)", "Anh", "Úc"]),
    ("🇦🇺", "Úc", ["New Zealand", "Anh", "Mỹ (Hoa Kỳ)"]),
    ("🇲🇽", "Mexico", ["Ý", "Tây Ban Nha", "Bồ Đào Nha"]),
    ("🇪🇬", "Ai Cập", ["Jordan", "UAE", "Ả Rập Xê Út"]),
    ("🇿🇦", "Nam Phi", ["Kenya", "Nigeria", "Ghana"]),
    ("🇸🇬", "Singapore", ["Malaysia", "Indonesia", "Trung Quốc"]),
]

COUNTRY_QUESTIONS = [
    ("Thủ đô là Hà Nội, nổi tiếng với phở và áo dài.", "Việt Nam", ["Thái Lan", "Lào", "Campuchia"]),
    ("Thủ đô là Tokyo, có núi Phú Sĩ và văn hoá anime.", "Nhật Bản", ["Hàn Quốc", "Trung Quốc", "Đài Loan"]),
    ("Thủ đô là Paris, có tháp Eiffel.", "Pháp", ["Ý", "Đức", "Bỉ"]),
    ("Thủ đô là Rome, có đấu trường Colosseum.", "Ý", ["Pháp", "Hy Lạp", "Tây Ban Nha"]),
    ("Thủ đô là Cairo, nổi tiếng với kim tự tháp.", "Ai Cập", ["Jordan", "Ả Rập Xê Út", "Maroc"]),
    ("Thủ đô là Canberra, có biểu tượng kangaroo.", "Úc", ["New Zealand", "Nam Phi", "Mỹ (Hoa Kỳ)"]),
    ("Thủ đô là Ottawa, quốc gia lá phong đỏ.", "Canada", ["Mỹ (Hoa Kỳ)", "Anh", "Thụy Điển"]),
    ("Thủ đô là Brasília, quê hương của bóng đá samba.", "Brazil", ["Argentina", "Bồ Đào Nha", "Mexico"]),
    ("Thủ đô là New Delhi, sinh ra môn Yoga.", "Ấn Độ", ["Nepal", "Pakistan", "Sri Lanka"]),
    ("Thủ đô là Seoul, nổi tiếng với K-pop.", "Hàn Quốc", ["Nhật Bản", "Triều Tiên", "Trung Quốc"]),
    ("Thủ đô là Bangkok, đất nước Chùa Vàng.", "Thái Lan", ["Myanmar", "Lào", "Campuchia"]),
    ("Thủ đô là Moscow, quốc gia rộng nhất thế giới.", "Nga", ["Trung Quốc", "Canada", "Kazakhstan"]),
    ("Thủ đô là Washington D.C., biểu tượng Nữ thần Tự Do.", "Mỹ (Hoa Kỳ)", ["Anh", "Pháp", "Canada"]),
    ("Thủ đô là Berlin, nổi tiếng với bia và xe hơi.", "Đức", ["Áo", "Hà Lan", "Thụy Sĩ"]),
    ("Thủ đô là Madrid, nổi tiếng với bò tót và flamenco.", "Tây Ban Nha", ["Bồ Đào Nha", "Mexico", "Ý"]),
]

CULTURE_QUESTIONS = [
    ("🎎 Búp bê Hina, trà đạo và Kimono.", "Nhật Bản", ["Trung Quốc", "Hàn Quốc", "Thái Lan"]),
    ("🥢 Tết Nguyên Đán, áo dài và bánh chưng.", "Việt Nam", ["Trung Quốc", "Hàn Quốc", "Lào"]),
    ("💃 Flamenco và đấu bò tót.", "Tây Ban Nha", ["Ý", "Bồ Đào Nha", "Mexico"]),
    ("🎭 Kịch mặt nạ Opera Bắc Kinh.", "Trung Quốc", ["Nhật Bản", "Việt Nam", "Hàn Quốc"]),
    ("🥁 Điệu múa Samba và lễ hội Carnival.", "Brazil", ["Argentina", "Colombia", "Mexico"]),
    ("🫖 Văn hoá trà chiều và cricket.", "Anh", ["Mỹ (Hoa Kỳ)", "Ireland", "Úc"]),
    ("🎻 Nhạc cổ điển Waltz và lâu đài cổ tích.", "Áo", ["Đức", "Thụy Sĩ", "Hungary"]),
    ("🪘 Nhạc Gamelan và múa rối bóng Wayang.", "Indonesia", ["Malaysia", "Thái Lan", "Philippines"]),
    ("🎨 Tranh Henna và trang phục Sari.", "Ấn Độ", ["Pakistan", "Bangladesh", "Nepal"]),
    ("🪕 Nhạc đồng quê Country và cao bồi.", "Mỹ (Hoa Kỳ)", ["Canada", "Úc", "Anh"]),
    ("🥋 Võ Taekwondo và Hanbok truyền thống.", "Hàn Quốc", ["Nhật Bản", "Trung Quốc", "Việt Nam"]),
    ("🍝 Ẩm thực Pizza, Pasta và nhạc Opera.", "Ý", ["Pháp", "Hy Lạp", "Tây Ban Nha"]),
]

APP_QUESTIONS = [
    ("👻 Biểu tượng bóng ma vàng, gửi ảnh tự huỷ.", "Snapchat", ["Instagram", "TikTok", "Messenger"]),
    ("🎵 Nền tảng video ngắn nổi tiếng với nhảy trend.", "TikTok", ["YouTube Shorts", "Instagram Reels", "Snapchat"]),
    ("📷 Đăng ảnh/video, có tính năng Story và Reels.", "Instagram", ["Facebook", "TikTok", "Pinterest"]),
    ("💬 Ứng dụng nhắn tin mã hoá đầu-cuối màu xanh lá.", "WhatsApp", ["Telegram", "Messenger", "Zalo"]),
    ("✈️ Ứng dụng nhắn tin có bot & kênh, biểu tượng máy bay giấy.", "Telegram", ["WhatsApp", "Discord", "Signal"]),
    ("🎮 Ứng dụng chat cộng đồng gamer, có server và voice chat.", "Discord", ["Telegram", "Slack", "Twitch"]),
    ("🐦 Mạng xã hội cũ có biểu tượng chú chim (nay đổi tên thành X).", "Twitter (X)", ["Threads", "Facebook", "Instagram"]),
    ("🔍 Công cụ tìm kiếm phổ biến nhất thế giới.", "Google", ["Bing", "Yahoo", "Cốc Cốc"]),
    ("▶️ Nền tảng xem video lớn nhất, có nút Subscribe.", "YouTube", ["TikTok", "Twitch", "Netflix"]),
    ("🎧 Nghe nhạc trực tuyến, biểu tượng vòng tròn xanh lá.", "Spotify", ["Apple Music", "SoundCloud", "Zing MP3"]),
    ("🛒 Sàn thương mại điện tử màu cam nổi tiếng ở VN.", "Shopee", ["Lazada", "Tiki", "Amazon"]),
    ("🇻🇳 Ứng dụng nhắn tin phổ biến nhất Việt Nam.", "Zalo", ["Telegram", "WhatsApp", "Messenger"]),
    ("🎬 Dịch vụ xem phim trả phí, logo chữ N đỏ.", "Netflix", ["YouTube", "Disney+", "HBO Max"]),
    ("📌 Mạng xã hội lưu ý tưởng/ảnh vào bảng ghim.", "Pinterest", ["Instagram", "Tumblr", "Behance"]),
]

TRADITION_QUESTIONS = [
    ("🧧 Lì xì đỏ và bánh chưng vào dịp năm mới âm lịch.", "Tết Nguyên Đán", ["Trung Thu", "Vu Lan", "Giáng Sinh"]),
    ("🏮 Rước đèn lồng và múa lân dành cho trẻ em.", "Trung Thu", ["Tết Nguyên Đán", "Halloween", "Vu Lan"]),
    ("🥚 Trứng sô-cô-la và thỏ mang biểu tượng mùa xuân.", "Lễ Phục Sinh (Easter)", ["Giáng Sinh", "Halloween", "Thanksgiving"]),
    ("🎃 Hoá trang ma quái và xin kẹo 'trick or treat'.", "Halloween", ["Lễ Phục Sinh", "Thanksgiving", "Trung Thu"]),
    ("🦃 Bữa tiệc gà tây tạ ơn ở Bắc Mỹ.", "Lễ Tạ Ơn (Thanksgiving)", ["Giáng Sinh", "Lễ Phục Sinh", "Halloween"]),
    ("🎄 Cây thông trang trí và ông già Noel tặng quà.", "Giáng Sinh (Christmas)", ["Lễ Phục Sinh", "Thanksgiving", "Trung Thu"]),
    ("🕉️ Lễ hội ánh sáng Diwali với đèn dầu.", "Diwali", ["Holi", "Eid al-Fitr", "Vesak"]),
    ("🎨 Lễ hội té màu sắc lên nhau ở Ấn Độ.", "Holi", ["Diwali", "Eid al-Fitr", "Songkran"]),
    ("💦 Lễ hội té nước mừng năm mới ở Thái Lan.", "Songkran", ["Holi", "Tết Nguyên Đán", "Diwali"]),
    ("🌸 Ngắm hoa anh đào Sakura nở rộ mùa xuân.", "Hanami", ["Trung Thu", "Obon", "Songkran"]),
    ("🕯️ Lễ hội tưởng nhớ tổ tiên với đèn lồng ở Nhật.", "Obon", ["Hanami", "Trung Thu", "Vu Lan"]),
    ("🌙 Tháng nhịn ăn ban ngày rồi ăn mừng cuối tháng.", "Ramadan / Eid al-Fitr", ["Diwali", "Holi", "Vesak"]),
]


def _build_quiz_options(correct: str, distractors: list[str]) -> list[str]:
    """Trộn ngẫu nhiên đáp án đúng với các đáp án nhiễu."""
    options = [correct] + list(distractors)
    random.shuffle(options)
    return options


def generate_quiz_question(pool: list[tuple[str, str, list[str]]]) -> dict:
    """Chọn ngẫu nhiên 1 câu hỏi từ pool (đoán cờ/quốc gia/văn hoá/ứng dụng/truyền thống)."""
    prompt, correct, distractors = random.choice(pool)
    options = _build_quiz_options(correct, distractors)
    return {"prompt": prompt, "correct": correct, "options": options}


def _progress_bar(current: int, total: int, length: int = 12) -> str:
    """Thanh tiến trình dạng text, ví dụ: ▰▰▰▰▰▱▱▱▱▱▱▱"""
    if total <= 0:
        filled = length
    else:
        filled = round(length * min(current / total, 1))
    return "▰" * filled + "▱" * (length - filled)


def _classify_roles(member: discord.Member) -> dict[str, list[discord.Role]]:
    """
    Phân loại role của member thành các nhóm quen thuộc (Owner, Admin, Mod, Bot, Khác)
    dựa trên tên role và quyền hạn, để hiển thị đẹp trong hồ sơ công dân.
    """
    guild = member.guild
    groups = {
        f"{ICON_CROWN} Owner": [],
        f"{ICON_ADMIN} Admin": [],
        f"{ICON_MOD} Mod": [],
        "🤖 Bot": [],
        "🏷️ Khác": [],
    }

    is_owner = guild.owner_id == member.id
    roles = [r for r in member.roles if r.name != "@everyone"]

    for role in roles:
        name_lower = role.name.lower()
        if is_owner and ("owner" in name_lower or "chủ" in name_lower):
            groups[f"{ICON_CROWN} Owner"].append(role)
        elif role.permissions.administrator or "admin" in name_lower or "quản trị" in name_lower:
            groups[f"{ICON_ADMIN} Admin"].append(role)
        elif (
            "mod" in name_lower
            or role.permissions.manage_messages
            or role.permissions.kick_members
            or "kiểm duyệt" in name_lower
        ):
            groups[f"{ICON_MOD} Mod"].append(role)
        elif "bot" in name_lower:
            groups["🤖 Bot"].append(role)
        else:
            groups["🏷️ Khác"].append(role)

    if is_owner and not groups[f"{ICON_CROWN} Owner"]:
        groups[f"{ICON_CROWN} Owner"].append(None)  # đánh dấu "là chủ server" dù không có role riêng

    return groups


_STATUS_LABELS = {
    discord.Status.online: ("🟢", "Đang online"),
    discord.Status.idle: ("🌙", "Đang rời đi (Idle)"),
    discord.Status.dnd: ("⛔", "Không làm phiền (DND)"),
    discord.Status.offline: ("⚫", "Offline"),
    discord.Status.invisible: ("⚫", "Offline"),
}


def _format_presence_block(member: discord.Member) -> str:
    """Trạng thái online/offline + rich presence (game đang chơi, Spotify, v.v.) của member."""
    emoji, label = _STATUS_LABELS.get(member.status, ("⚫", "Offline"))
    lines = [f"{emoji} **{label}**"]

    activities = [a for a in (member.activities or []) if a is not None]
    for act in activities:
        if isinstance(act, discord.Spotify):
            lines.append(f"🎧 Đang nghe **{act.title}** — {act.artist}")
        elif isinstance(act, discord.CustomActivity):
            text = act.name or ""
            if act.emoji:
                text = f"{act.emoji} {text}".strip()
            if text:
                lines.append(f"💬 {text}")
        elif isinstance(act, discord.Game):
            lines.append(f"🎮 Đang chơi **{act.name}**")
        elif isinstance(act, discord.Streaming):
            lines.append(f"🔴 Đang stream **{act.name}**")
        elif isinstance(act, discord.Activity):
            verb = {
                discord.ActivityType.watching: "Đang xem",
                discord.ActivityType.listening: "Đang nghe",
                discord.ActivityType.competing: "Đang thi đấu",
            }.get(act.type, "Đang")
            lines.append(f"✨ {verb} **{act.name}**")

    return "\n".join(lines)


def _format_roles_block(member: discord.Member, max_per_group: int = 4) -> str:
    groups = _classify_roles(member)
    lines = []
    for label, roles in groups.items():
        if not roles:
            continue
        if label == f"{ICON_CROWN} Owner" and roles == [None]:
            lines.append(f"{label}: *Chủ sở hữu server*")
            continue

        mentions = [r.mention for r in roles if r is not None]
        if not mentions:
            continue

        shown = mentions[:max_per_group]
        extra = len(mentions) - len(shown)
        text = ", ".join(shown)
        if extra > 0:
            text += f" *và +{extra} role khác*"
        lines.append(f"{label}: {text}")

    if not lines:
        return "*Chưa có role nào*"
    return "\n".join(lines)


# ==================== GIAO DIỆN (Components V2) ====================
class LevelUpView(discord.ui.LayoutView):
    """Thông báo lên level bằng Container Components V2."""

    def __init__(self, member: discord.Member, result: dict):
        super().__init__(timeout=None)
        user_data = result["user"]
        lines = [
            f"## {ICON_PARTY} {member.mention} vừa lên **Level {result['new_level']}**!",
            "",
            f"{ICON_LEVEL} Level: **{result['new_level']}**",
            f"{ICON_XP} XP: **{user_data.get('xp', 0)}**",
            f"{ICON_DELTAN} Nhận thêm: **+{result['deltan_gained']}** (tổng: {user_data.get('deltan', 0)})",
            f"{ICON_AURA} Nhận thêm: **+{result['aura_gained']}** (tổng: {user_data.get('aura', 0)})",
        ]
        container = discord.ui.Container(
            discord.ui.Section(
                "\n".join(lines),
                accessory=discord.ui.Thumbnail(media=member.display_avatar.url),
            ),
            accent_color=discord.Colour.gold(),
        )
        self.add_item(container)


class CitizenView(discord.ui.LayoutView):
    """Hồ sơ công dân bằng Container Components V2, hiển thị role theo nhóm (Owner/Admin/Mod/Bot/Khác)."""

    def __init__(self, member: discord.Member, user_data: dict, citizen_data: dict, is_new: bool):
        super().__init__(timeout=None)

        level = user_data.get("level", 0)
        xp = user_data.get("xp", 0)
        _, xp_in_level, xp_needed = level_from_total_xp(xp)
        bar = _progress_bar(xp_in_level, xp_needed)

        joined_at = member.joined_at.strftime("%d/%m/%Y") if member.joined_at else "Không rõ"
        citizen_id = citizen_data.get("citizen_id", "N/A")
        created_at = citizen_data.get("created_at")
        created_text = (
            datetime.datetime.fromtimestamp(created_at, VN_TZ).strftime("%d/%m/%Y %H:%M")
            if created_at else "N/A"
        )

        header_lines = [
            "## 🪪 HỒ SƠ CÔNG DÂN" + (" · *mới tạo* ✨" if is_new else ""),
            f"**Mã công dân:** `{citizen_id}`  •  **Thành viên:** {member.mention}",
            f"-# Cấp ngày {created_text} · Vào server {joined_at}",
        ]

        stats_lines = [
            f"### {ICON_LEVEL} Level {level}",
            f"{bar}  `{xp_in_level}/{xp_needed}` {ICON_XP}",
            "",
            f"{ICON_XP} **Tổng XP:** {xp:,}",
            f"{ICON_AURA} **Aura:** {user_data.get('aura', 0.0)}",
            f"{ICON_DELTAN} **Deltan:** {user_data.get('deltan', 0):,}",
            f"{ICON_STREAK} **Daily Streak:** {format_daily_streak(user_data)}",
        ]

        roles_lines = [
            "### 🎖️ Vai trò",
            _format_roles_block(member),
        ]

        presence_lines = [
            "### 📶 Trạng thái Discord",
            _format_presence_block(member),
        ]

        container = discord.ui.Container(
            discord.ui.Section(
                "\n".join(header_lines),
                accessory=discord.ui.Thumbnail(media=member.display_avatar.url),
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay("\n".join(stats_lines)),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay("\n".join(presence_lines)),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay("\n".join(roles_lines)),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


class LevelView(discord.ui.LayoutView):
    """Hiển thị nhanh Level/XP/Aura/Deltan/Vé của một thành viên, dùng cho lệnh /level."""

    def __init__(self, member: discord.Member, user_data: dict):
        super().__init__(timeout=None)

        level = user_data.get("level", 0)
        xp = user_data.get("xp", 0)
        _, xp_in_level, xp_needed = level_from_total_xp(xp)
        bar = _progress_bar(xp_in_level, xp_needed)

        lines = [
            f"### {ICON_LEVEL} {member.mention} — Level {level}",
            f"{bar}  `{xp_in_level}/{xp_needed}` {ICON_XP}",
            "",
            f"{ICON_XP} **Tổng XP:** {xp:,}",
            f"{ICON_AURA} **Aura:** {user_data.get('aura', 0.0)}",
            f"{ICON_DELTAN} **Deltan:** {user_data.get('deltan', 0):,}",
            f"{ICON_TICKET} **Vé game:** {user_data.get('tickets', 0)}/{TICKETS_MAX}",
            f"{ICON_STREAK} **Daily Streak:** {format_daily_streak(user_data)}",
        ]

        container = discord.ui.Container(
            discord.ui.Section(
                "\n".join(lines),
                accessory=discord.ui.Thumbnail(media=member.display_avatar.url),
            ),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


# ==================== /deltan-shop ====================
DELTAN_SHOP_TICKET_OPTIONS = [1, 3, 5]  # số vé có thể mua mỗi lượt, tối đa = TICKETS_MAX


class DeltanShopView(discord.ui.LayoutView):
    """
    Cửa hàng đổi Deltan lấy các thứ khác trong bot. Hiện tại chỉ có đổi vé
    game, nhưng gộp chung vào đây để sau này thêm món gì liên quan tới Deltan
    (vd đổi role, đổi vật phẩm...) thì chỉ cần thêm ActionRow/nút mới.
    """

    def __init__(self, guild_id: int, owner_id: int, deltan: int, tickets: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id

        lines = [
            "## 🛒 DELTAN SHOP",
            f"{ICON_DELTAN} Deltan của bạn: **{deltan}**  •  {ICON_TICKET} Vé hiện có: **{tickets}/{TICKETS_MAX}**",
            f"-# 🎟️ Đổi vé chơi game — giá **{DELTAN_PER_TICKET} {ICON_DELTAN} / vé**.",
        ]

        buy_buttons = [
            DeltanShopBuyButton(guild_id, owner_id, qty, qty * DELTAN_PER_TICKET)
            for qty in DELTAN_SHOP_TICKET_OPTIONS
        ]

        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(*buy_buttons),
            accent_color=discord.Colour.gold(),
        )
        self.add_item(container)


class DeltanShopBuyButton(discord.ui.Button):
    def __init__(self, guild_id: int, owner_id: int, quantity: int, cost: int):
        super().__init__(
            label=f"Mua {quantity} vé — {cost} Deltan",
            style=discord.ButtonStyle.success,
            emoji=ICON_TICKET,
        )
        self.guild_id, self.owner_id, self.quantity, self.cost = guild_id, owner_id, quantity, cost

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.owner_id):
            return

        # Ack ngay (deferred update) trước khi gọi Firebase, tránh timeout 3s.
        await interaction.response.defer()

        try:
            state = await firebase.get_ticket_state(
                self.guild_id, self.owner_id, TICKETS_MAX, TICKETS_REGEN_SECONDS, today_str(),
            )
        except firebase.FirebaseUnavailable:
            await interaction.edit_original_response(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if state.get("tickets", 0) + self.quantity > TICKETS_MAX:
            await interaction.edit_original_response(
                view=GameResultView(
                    f"{ICON_CROSS} Bạn chỉ được giữ tối đa **{TICKETS_MAX}** {ICON_TICKET}, "
                    f"hiện có **{state.get('tickets', 0)}**, không thể mua thêm {self.quantity} vé."
                ),
            )
            return

        try:
            spend = await firebase.spend_deltan(self.guild_id, self.owner_id, self.cost)
        except firebase.FirebaseUnavailable:
            await interaction.edit_original_response(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if not spend["ok"]:
            await interaction.edit_original_response(
                view=GameResultView(
                    f"{ICON_CROSS} Bạn không đủ Deltan! Cần **{self.cost} {ICON_DELTAN}**, "
                    f"hiện có **{spend['deltan']} {ICON_DELTAN}**."
                ),
            )
            return

        try:
            new_tickets = await firebase.add_tickets(self.guild_id, self.owner_id, self.quantity)
        except firebase.FirebaseUnavailable:
            # Đã trừ Deltan nhưng chưa cộng được vé — hoàn Deltan lại ngay.
            await firebase.add_deltan(self.guild_id, self.owner_id, self.cost)
            await interaction.edit_original_response(
                view=GameResultView(f"{ICON_WARNING} Có lỗi kết nối, Deltan đã được hoàn lại, thử lại sau nhé!"),
            )
            return

        # Làm mới lại shop với số dư mới để có thể mua tiếp ngay trong cùng 1 tin nhắn.
        await interaction.edit_original_response(
            view=DeltanShopView(self.guild_id, self.owner_id, spend["deltan"], new_tickets),
        )


# ==================== /help ====================
class HelpView(discord.ui.LayoutView):
    """Danh sách lệnh + vai trò cần thiết, dùng cho lệnh /help."""

    def __init__(self, categories: list[dict]):
        super().__init__(timeout=None)

        lines = [f"## {ICON_BADGE} DANH SÁCH LỆNH"]
        for cat in categories:
            lines.append(f"### {cat['title']}")
            for cmd in cat["commands"]:
                role_note = f" · *{cmd['role']}*" if cmd.get("role") else ""
                lines.append(f"{ICON_BULLET} `/{cmd['name']}` — {cmd['desc']}{role_note}")

        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


# ==================== BẢNG XẾP HẠNG ====================
def _leaderboard_lines(guild: discord.Guild, field: str, ranked: list[tuple[int, dict]], page: int) -> list[str]:
    meta = LEADERBOARD_FIELDS[field]
    total_pages = max(1, -(-len(ranked) // LEADERBOARD_PAGE_SIZE))  # ceil div
    start = page * LEADERBOARD_PAGE_SIZE
    page_items = ranked[start:start + LEADERBOARD_PAGE_SIZE]

    lines = [
        f"## {ICON_CUP} BẢNG XẾP HẠNG — {meta['label'].upper()}",
        f"-# Trang **{page + 1}/{total_pages}**",
    ]

    if not page_items:
        lines.append("*Chưa có dữ liệu nào để xếp hạng.*")
    else:
        for offset, (user_id, data) in enumerate(page_items):
            rank = start + offset + 1
            member = guild.get_member(user_id)
            name = member.mention if member else f"`{user_id}`"
            value = meta["fmt"](data.get(field, 0) or 0)
            lines.append(f"{ICON_BULLET} `#{rank}` {name} — **{value}** {meta['icon']}")

    return lines


class LeaderboardView(discord.ui.LayoutView):
    """Hiển thị bảng xếp hạng theo Deltan / Level / Aura với nút chuyển trang, dùng cho /bảng-xếp-hạng."""

    def __init__(self, guild: discord.Guild, field: str, ranked: list[tuple[int, dict]], page: int = 0):
        super().__init__(timeout=180)
        self.guild, self.field, self.ranked, self.page = guild, field, ranked, page
        total_pages = max(1, -(-len(ranked) // LEADERBOARD_PAGE_SIZE))

        lines = _leaderboard_lines(guild, field, ranked, page)

        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(
                LeaderboardPageButton(self, -1, "◀", page <= 0),
                LeaderboardPageButton(self, 1, "▶", page >= total_pages - 1),
            ),
            accent_color=discord.Colour.gold(),
        )
        self.add_item(container)


class LeaderboardPageButton(discord.ui.Button):
    def __init__(self, parent: "LeaderboardView", delta: int, emoji: str, disabled: bool):
        super().__init__(style=discord.ButtonStyle.secondary, emoji=emoji, disabled=disabled)
        self.parent_view, self.delta = parent, delta

    async def callback(self, interaction: discord.Interaction):
        new_page = self.parent_view.page + self.delta
        await interaction.response.edit_message(
            view=LeaderboardView(self.parent_view.guild, self.parent_view.field, self.parent_view.ranked, new_page)
        )


# ==================== THÚ TỘI ẨN DANH ====================
class ConfessionView(discord.ui.LayoutView):
    """
    Container hiển thị 1 thú tội ẩn danh — hoàn toàn không kèm tên/avatar người
    gửi. `confession_id` chỉ là mã ngẫu nhiên không thể tra ngược ra người gửi.
    """

    def __init__(self, content: str, confession_number: int, confession_id: str, sent_at: float):
        super().__init__(timeout=None)

        lines = [
            f"**Lời Thú Tội Ẩn Danh #{confession_number}**",
            content,
            "",
            f"-# ID: {confession_id} ·",
            f"-# <t:{int(sent_at)}:F>",
            "-# Thú tội ẩn danh · không thể truy ra người gửi",
        ]

        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            accent_color=discord.Colour.dark_grey(),
        )
        self.add_item(container)


# ==================== VÉ GAME / MINI GAME (owner-checked) ====================
class GameSelectView(discord.ui.LayoutView):
    """Bước 1 của /game: chọn danh mục (May Rủi / Trí Tuệ - Logic / Trí Tuệ - Kiến Thức)."""

    def __init__(self, owner_id: int, tickets: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        lines = [
            "## 🎮 MINI GAME",
            f"{ICON_TICKET} Vé của bạn: **{tickets}**  •  Mỗi lượt chơi tốn **{GAME_TICKET_COST}** {ICON_TICKET} "
            f"• Cách nhau tối thiểu **{GAME_COOLDOWN_SECONDS // 60} phút**/lượt",
            "-# Chọn 1 danh mục bên dưới để xem các game trong đó:",
        ]
        for cat in GAME_CATEGORIES:
            lines.append(f"{cat['emoji']} **{cat['title']}** — {cat['desc']}")

        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(*[
                GameCategoryButton(owner_id, tickets, cat["key"], cat["title"], cat["emoji"])
                for cat in GAME_CATEGORIES
            ]),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


class GameCategoryButton(discord.ui.Button):
    def __init__(self, owner_id: int, tickets: int, category_key: str, label: str, emoji: str):
        super().__init__(label=label, style=discord.ButtonStyle.primary, emoji=emoji)
        self.owner_id, self.tickets, self.category_key = owner_id, tickets, category_key

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.owner_id):
            return
        await interaction.response.edit_message(
            view=GameCategoryGamesView(self.owner_id, self.tickets, self.category_key)
        )


class GameCategoryGamesView(discord.ui.LayoutView):
    """Bước 2 của /game: hiện các game trong 1 danh mục đã chọn + nút Quay lại."""

    def __init__(self, owner_id: int, tickets: int, category_key: str):
        super().__init__(timeout=60)
        cat = next(c for c in GAME_CATEGORIES if c["key"] == category_key)
        games = [info for info in GAME_LIST_INFO if info["category"] == category_key]

        lines = [
            f"## {cat['emoji']} {cat['title'].upper()}",
            f"{ICON_TICKET} Vé của bạn: **{tickets}**  •  Mỗi lượt tốn **{GAME_TICKET_COST}** {ICON_TICKET}",
        ]
        for info in games:
            lines.append(f"{info['emoji']} **{info['title']}** — {info['desc']}")
        lines.append("-# Chọn một trò chơi bên dưới:")

        rows = [
            discord.ui.ActionRow(*[
                GameChoiceButton(owner_id, info["key"], info["title"], info["emoji"])
                for info in chunk
            ])
            for chunk in (games[i:i + 4] for i in range(0, len(games), 4))
        ]

        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            *rows,
            discord.ui.ActionRow(GameBackButton(owner_id, tickets)),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


class GameBackButton(discord.ui.Button):
    def __init__(self, owner_id: int, tickets: int):
        super().__init__(label="Quay lại danh mục", style=discord.ButtonStyle.secondary, emoji="◀")
        self.owner_id, self.tickets = owner_id, tickets

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.owner_id):
            return
        await interaction.response.edit_message(view=GameSelectView(self.owner_id, self.tickets))


async def _reject_if_not_owner(interaction: discord.Interaction, owner_id: int) -> bool:
    """Trả về True (và đã trả lời interaction) nếu người bấm KHÔNG phải chủ ván chơi."""
    if interaction.user.id != owner_id:
        await interaction.response.send_message(
            f"{ICON_CROSS} Đây không phải ván chơi của bạn! Dùng lệnh `/game` để tạo ván riêng nhé.",
            ephemeral=True,
        )
        return True
    return False


class GameChoiceButton(discord.ui.Button):
    def __init__(self, owner_id: int, game_key: str, label: str, emoji: str):
        super().__init__(label=label, style=discord.ButtonStyle.primary, emoji=emoji)
        self.owner_id = owner_id
        self.game_key = game_key

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        if await _reject_if_not_owner(interaction, self.owner_id):
            return

        if self.game_key == "guess":
            await interaction.response.send_message(
                view=GuessNumberView(interaction.guild.id, interaction.user.id),
                ephemeral=True,
            )
        elif self.game_key == "rps":
            await interaction.response.send_message(
                view=RPSView(interaction.guild.id, interaction.user.id),
                ephemeral=True,
            )
        elif self.game_key == "dice":
            await interaction.response.send_message(
                view=DiceView(interaction.guild.id, interaction.user.id),
                ephemeral=True,
            )
        elif self.game_key == "wordle":
            await interaction.response.send_message(
                view=WordleModeView(interaction.guild.id, interaction.user.id),
                ephemeral=True,
            )
        elif self.game_key == "mathquiz":
            await interaction.response.send_message(
                view=MathQuizView(interaction.guild.id, interaction.user.id),
                ephemeral=True,
            )
        elif self.game_key == "memory":
            await interaction.response.send_message(
                view=MemoryGameView(interaction.guild.id, interaction.user.id),
                ephemeral=True,
            )
        elif self.game_key in QUIZ_GAME_CONFIG:
            await interaction.response.send_message(
                view=QuizGameView(interaction.guild.id, interaction.user.id, self.game_key),
                ephemeral=True,
            )
        elif self.game_key in GAME_DEFS:
            await interaction.response.send_message(
                view=NewGameView(interaction.guild.id, interaction.user.id, self.game_key),
                ephemeral=True,
            )


async def _spend_ticket_or_none(guild_id: int, user_id: int) -> dict:
    """
    Trả về dict {"ok": bool, "tickets": int, "next_regen_in": int|None,
    "reason": "cooldown"|"no_ticket"|None, "cooldown_remaining": int|None}
    từ firebase.use_ticket. Mỗi lượt chơi (bất kỳ game nào) cách nhau tối
    thiểu GAME_COOLDOWN_SECONDS, kiểm tra atomic cùng lúc với trừ vé.
    """
    return await firebase.use_ticket(
        guild_id, user_id, GAME_TICKET_COST, TICKETS_MAX, TICKETS_REGEN_SECONDS, today_str(),
        cooldown_seconds=GAME_COOLDOWN_SECONDS,
    )


def _format_no_ticket_message(result: dict) -> str:
    """Thông báo khi không chơi được: đang trong cooldown 5 phút, hoặc hết vé."""
    if result.get("reason") == "cooldown":
        remaining = max(result.get("cooldown_remaining", 0), 0)
        minutes, seconds = divmod(remaining, 60)
        time_text = f"{minutes} phút {seconds} giây" if minutes else f"{seconds} giây"
        return f"{ICON_WARNING} Chơi hơi nhanh rồi đó! Nghỉ **{time_text}** nữa rồi quay lại chơi tiếp nhé."

    base = f"{ICON_CROSS} Vé = 0 thì ko thể chơi 😂😂"
    next_in = result.get("next_regen_in")
    if next_in and next_in > 0:
        hours = next_in // 3600
        minutes = (next_in % 3600) // 60
        if hours > 0:
            base += f"\n-# Vé kế tiếp hồi sau khoảng **{hours} giờ {minutes} phút** nữa."
        else:
            base += f"\n-# Vé kế tiếp hồi sau khoảng **{minutes} phút** nữa."
    return base


class GameResultView(discord.ui.LayoutView):
    """Container Components V2 dùng để hiển thị kết quả cuối game (thay cho
    content=... vì message gốc đã bật cờ IS_COMPONENTS_V2 — không thể trộn
    content thường với Components V2, phải sửa lại bằng 1 container khác)."""
    def __init__(self, text: str):
        super().__init__(timeout=None)
        container = discord.ui.Container(
            discord.ui.TextDisplay(text),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


class GuessNumberView(discord.ui.LayoutView):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=60)
        lines = [
            "### 🔢 Đoán Số (1-10)",
            f"Đoán đúng số bí mật để thắng! Tốn **{GAME_TICKET_COST}** {ICON_TICKET} mỗi lượt.",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(*[GuessNumberButton(guild_id, user_id, n) for n in range(1, 6)]),
            discord.ui.ActionRow(*[GuessNumberButton(guild_id, user_id, n) for n in range(6, 11)]),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


class GuessNumberButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int, number: int):
        super().__init__(label=str(number), style=discord.ButtonStyle.secondary)
        self.guild_id, self.user_id, self.number = guild_id, user_id, number

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        # Ack ngay (deferred update) trước khi gọi Firebase, tránh timeout 3s.
        await interaction.response.defer()

        try:
            spend = await _spend_ticket_or_none(self.guild_id, self.user_id)
        except firebase.FirebaseUnavailable:
            await interaction.edit_original_response(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if not spend["ok"]:
            await interaction.edit_original_response(
                view=GameResultView(_format_no_ticket_message(spend)),
            )
            return

        result = play_guess_number(self.number)
        if result["win"]:
            await firebase.add_tickets(self.guild_id, self.user_id, 1)
            await firebase.add_deltan(self.guild_id, self.user_id, GUESS_NUMBER_DELTAN_REWARD)
            text = (
                f"{ICON_CHECK} Chính xác! Số bí mật là **{result['secret']}**. "
                f"Bạn nhận lại +1 {ICON_TICKET} và +{GUESS_NUMBER_DELTAN_REWARD} {ICON_DELTAN}!"
            )
        else:
            text = f"{ICON_CROSS} Sai rồi! Số bí mật là **{result['secret']}**."
        await interaction.edit_original_response(view=GameResultView(text))


class RPSView(discord.ui.LayoutView):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=60)
        lines = [
            "### ✊ Kéo Búa Bao",
            f"Chọn 1 trong 3! Tốn **{GAME_TICKET_COST}** {ICON_TICKET} mỗi lượt.",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(
                RPSButton(guild_id, user_id, "kéo", "✌️"),
                RPSButton(guild_id, user_id, "búa", "✊"),
                RPSButton(guild_id, user_id, "bao", "🖐️"),
            ),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


class RPSButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int, choice: str, emoji: str):
        super().__init__(label=choice.capitalize(), style=discord.ButtonStyle.secondary, emoji=emoji)
        self.guild_id, self.user_id, self.choice = guild_id, user_id, choice

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        # Ack ngay (deferred update) trước khi gọi Firebase, tránh timeout 3s.
        await interaction.response.defer()

        try:
            spend = await _spend_ticket_or_none(self.guild_id, self.user_id)
        except firebase.FirebaseUnavailable:
            await interaction.edit_original_response(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if not spend["ok"]:
            await interaction.edit_original_response(
                view=GameResultView(_format_no_ticket_message(spend)),
            )
            return

        result = play_rps(self.choice)
        if result["result"] == "win":
            await firebase.add_tickets(self.guild_id, self.user_id, 1)
            await firebase.add_deltan(self.guild_id, self.user_id, RPS_DELTAN_REWARD)
            text = (
                f"{ICON_CHECK} Bạn thắng! Bot chọn **{result['bot_choice']}**. "
                f"Bạn nhận lại +1 {ICON_TICKET} và +{RPS_DELTAN_REWARD} {ICON_DELTAN}!"
            )
        elif result["result"] == "draw":
            await firebase.add_tickets(self.guild_id, self.user_id, GAME_TICKET_COST)
            text = f"{ICON_WARNING} Hòa! Bot cũng chọn **{result['bot_choice']}**. Vé được hoàn lại."
        else:
            text = f"{ICON_CROSS} Bạn thua! Bot chọn **{result['bot_choice']}**."
        await interaction.edit_original_response(view=GameResultView(text))


class DiceView(discord.ui.LayoutView):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=60)
        lines = [
            "### 🎲 Xúc Xắc",
            f"Đoán xúc xắc sẽ ra **Cao (4-6)** hay **Thấp (1-3)**! Tốn **{GAME_TICKET_COST}** {ICON_TICKET} mỗi lượt.",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(
                DiceButton(guild_id, user_id, "cao"),
                DiceButton(guild_id, user_id, "thấp"),
            ),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


class DiceButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int, guess: str):
        super().__init__(label=guess.capitalize(), style=discord.ButtonStyle.secondary)
        self.guild_id, self.user_id, self.guess = guild_id, user_id, guess

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        # Ack ngay (deferred update) trước khi gọi Firebase, tránh timeout 3s.
        await interaction.response.defer()

        try:
            spend = await _spend_ticket_or_none(self.guild_id, self.user_id)
        except firebase.FirebaseUnavailable:
            await interaction.edit_original_response(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if not spend["ok"]:
            await interaction.edit_original_response(
                view=GameResultView(_format_no_ticket_message(spend)),
            )
            return

        result = play_dice(self.guess)
        if result["win"]:
            await firebase.add_tickets(self.guild_id, self.user_id, 1)
            await firebase.add_deltan(self.guild_id, self.user_id, DICE_DELTAN_REWARD)
            text = (
                f"{ICON_CHECK} Xúc xắc ra **{result['roll']}** ({result['actual']})! Bạn đoán đúng, "
                f"nhận lại +1 {ICON_TICKET} và +{DICE_DELTAN_REWARD} {ICON_DELTAN}!"
            )
        else:
            text = f"{ICON_CROSS} Xúc xắc ra **{result['roll']}** ({result['actual']})! Bạn đoán sai."
        await interaction.edit_original_response(view=GameResultView(text))


# ==================== 4 GAME MỚI: thưởng/phạt bằng Deltan + Aura ====================
class NewGameView(discord.ui.LayoutView):
    """View dùng chung cho 4 game mới (taixiu/bai/vongquay/ruong), cấu hình lấy từ GAME_DEFS."""

    def __init__(self, guild_id: int, user_id: int, game_key: str):
        super().__init__(timeout=60)
        cfg = GAME_DEFS[game_key]
        lines = [
            f"### {cfg['title']}",
            cfg["desc"],
            f"Tốn **{GAME_TICKET_COST}** {ICON_TICKET} mỗi lượt. "
            f"Thắng nhận **+{cfg['deltan_reward']} {ICON_DELTAN}** và **+{cfg['aura_reward']} {ICON_AURA}** "
            f"— thua bị trừ **-{cfg['aura_reward']} {ICON_AURA}**.",
        ]
        rows = [
            discord.ui.ActionRow(
                *[NewGameButton(guild_id, user_id, game_key, value, label, emoji) for value, label, emoji in chunk]
            )
            for chunk in (cfg["options"][i:i + 5] for i in range(0, len(cfg["options"]), 5))
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            *rows,
            accent_color=discord.Colour.gold(),
        )
        self.add_item(container)


class NewGameButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int, game_key: str, value: str, label: str, emoji: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, emoji=emoji)
        self.guild_id, self.user_id, self.game_key, self.value = guild_id, user_id, game_key, value

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        # Ack ngay (deferred update) trước khi gọi Firebase, tránh timeout 3s.
        await interaction.response.defer()

        try:
            spend = await _spend_ticket_or_none(self.guild_id, self.user_id)
        except firebase.FirebaseUnavailable:
            await interaction.edit_original_response(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if not spend["ok"]:
            await interaction.edit_original_response(
                view=GameResultView(_format_no_ticket_message(spend)),
            )
            return

        cfg = GAME_DEFS[self.game_key]
        result = play_new_game(self.game_key, self.value)

        try:
            if result["win"]:
                await firebase.add_deltan(self.guild_id, self.user_id, cfg["deltan_reward"])
                await firebase.add_aura(self.guild_id, self.user_id, cfg["aura_reward"])
                text = (
                    f"{ICON_CHECK} Chính xác! Kết quả: {result['actual_label']}.\n"
                    f"Bạn nhận **+{cfg['deltan_reward']} {ICON_DELTAN}** và **+{cfg['aura_reward']} {ICON_AURA}**!"
                )
            else:
                await firebase.add_aura(self.guild_id, self.user_id, -cfg["aura_reward"])
                text = (
                    f"{ICON_CROSS} Sai rồi! Kết quả: {result['actual_label']}.\n"
                    f"Bạn bị trừ **-{cfg['aura_reward']} {ICON_AURA}**."
                )
        except firebase.FirebaseUnavailable:
            # Vé đã bị trừ nhưng không cộng/trừ được Deltan/Aura — báo lỗi rõ ràng
            # thay vì im lặng mất phần thưởng của người chơi.
            text = f"{ICON_WARNING} Đã ghi nhận kết quả nhưng không cộng/trừ được Deltan/Aura do lỗi kết nối. Vé đã bị trừ, báo admin nếu cần hoàn lại."

        await interaction.edit_original_response(view=GameResultView(text))


# ==================== TÍNH NHẨM (game trí tuệ - không may rủi) ====================
class MathQuizView(discord.ui.LayoutView):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=60)
        lines = [
            "### 🧮 Tính Nhẩm",
            f"Giải đúng phép tính để thắng — không có yếu tố may rủi! "
            f"Tốn **{GAME_TICKET_COST}** {ICON_TICKET} mỗi lượt.",
            f"Đúng nhận **+{MATH_QUIZ_DELTAN_REWARD} {ICON_DELTAN}** và **+{MATH_QUIZ_AURA_REWARD} {ICON_AURA}** "
            f"— sai bị trừ **-{MATH_QUIZ_AURA_REWARD} {ICON_AURA}**.",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(MathQuizStartButton(guild_id, user_id)),
            accent_color=discord.Colour.teal(),
        )
        self.add_item(container)


class MathQuizStartButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(label="Bắt đầu", style=discord.ButtonStyle.success, emoji="▶️")
        self.guild_id, self.user_id = guild_id, user_id

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        # Không defer vì cần mở modal ngay sau response đầu tiên.
        try:
            spend = await _spend_ticket_or_none(self.guild_id, self.user_id)
        except firebase.FirebaseUnavailable:
            await interaction.response.edit_message(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if not spend["ok"]:
            await interaction.response.edit_message(
                view=GameResultView(_format_no_ticket_message(spend)),
            )
            return

        question = generate_math_question()
        await interaction.response.send_modal(MathQuizModal(self.guild_id, self.user_id, question))


class MathQuizModal(discord.ui.Modal):
    def __init__(self, guild_id: int, user_id: int, question: dict):
        super().__init__(title="🧮 Tính Nhẩm")
        self.guild_id, self.user_id, self.question = guild_id, user_id, question
        self.answer_input = discord.ui.TextInput(
            label=f"{question['question']} = ?",
            placeholder="Nhập đáp án...",
            max_length=10,
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction):
        result = check_math_answer(self.question, self.answer_input.value)

        try:
            if result["win"]:
                await firebase.add_deltan(self.guild_id, self.user_id, MATH_QUIZ_DELTAN_REWARD)
                await firebase.add_aura(self.guild_id, self.user_id, MATH_QUIZ_AURA_REWARD)
                text = (
                    f"{ICON_CHECK} Chính xác! **{self.question['question']} = {self.question['answer']}**.\n"
                    f"Bạn nhận **+{MATH_QUIZ_DELTAN_REWARD} {ICON_DELTAN}** và **+{MATH_QUIZ_AURA_REWARD} {ICON_AURA}**!"
                )
            else:
                await firebase.add_aura(self.guild_id, self.user_id, -MATH_QUIZ_AURA_REWARD)
                wrong_note = "*(không phải là một số hợp lệ)*" if result["invalid"] else ""
                text = (
                    f"{ICON_CROSS} Sai rồi {wrong_note}! Đáp án đúng: "
                    f"**{self.question['question']} = {self.question['answer']}**.\n"
                    f"Bạn bị trừ **-{MATH_QUIZ_AURA_REWARD} {ICON_AURA}**."
                )
        except firebase.FirebaseUnavailable:
            text = f"{ICON_WARNING} Đã ghi nhận kết quả nhưng không cộng/trừ được Deltan/Aura do lỗi kết nối. Vé đã bị trừ, báo admin nếu cần hoàn lại."

        await interaction.response.send_message(view=GameResultView(text), ephemeral=True)


# ==================== TRÍ NHỚ (game trí tuệ - không may rủi) ====================
class MemoryGameView(discord.ui.LayoutView):
    """Bước 1: hiện dãy số cần nhớ trong vài giây, sau đó chuyển sang bước nhập lại."""

    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=60)
        lines = [
            "### 🧩 Trí Nhớ",
            f"Ghi nhớ dãy số bên dưới rồi bấm lại **đúng thứ tự** ở bước sau — không may rủi! "
            f"Tốn **{GAME_TICKET_COST}** {ICON_TICKET} mỗi lượt.",
            f"Đúng nhận **+{MEMORY_DELTAN_REWARD} {ICON_DELTAN}** và **+{MEMORY_AURA_REWARD} {ICON_AURA}** "
            f"— sai bị trừ **-{MEMORY_AURA_REWARD} {ICON_AURA}**.",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(MemoryStartButton(guild_id, user_id)),
            accent_color=discord.Colour.teal(),
        )
        self.add_item(container)


class MemoryStartButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(label="Bắt đầu", style=discord.ButtonStyle.success, emoji="▶️")
        self.guild_id, self.user_id = guild_id, user_id

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        await interaction.response.defer()

        try:
            spend = await _spend_ticket_or_none(self.guild_id, self.user_id)
        except firebase.FirebaseUnavailable:
            await interaction.edit_original_response(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if not spend["ok"]:
            await interaction.edit_original_response(
                view=GameResultView(_format_no_ticket_message(spend)),
            )
            return

        sequence = generate_memory_sequence()
        await interaction.edit_original_response(
            view=MemorySequenceShowView(self.guild_id, self.user_id, sequence)
        )


class MemorySequenceShowView(discord.ui.LayoutView):
    """Hiện dãy số cần nhớ + nút để chuyển sang màn nhập lại khi đã sẵn sàng."""

    def __init__(self, guild_id: int, user_id: int, sequence: list[int]):
        super().__init__(timeout=60)
        seq_text = "   ".join(f"`{n}`" for n in sequence)
        lines = [
            "### 🧩 Trí Nhớ — Ghi nhớ dãy số này!",
            f"## {seq_text}",
            "-# Nhớ kỹ thứ tự rồi bấm **Sẵn sàng** để bấm lại đúng thứ tự (dãy sẽ bị ẩn đi).",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(MemoryReadyButton(guild_id, user_id, sequence)),
            accent_color=discord.Colour.teal(),
        )
        self.add_item(container)


class MemoryReadyButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int, sequence: list[int]):
        super().__init__(label="Sẵn sàng — Nhập lại", style=discord.ButtonStyle.primary, emoji="✅")
        self.guild_id, self.user_id, self.sequence = guild_id, user_id, sequence

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return
        await interaction.response.edit_message(
            view=MemoryInputView(self.guild_id, self.user_id, self.sequence, [])
        )


class MemoryInputView(discord.ui.LayoutView):
    """Bàn phím số 1-9 để người chơi bấm lại đúng thứ tự dãy đã cho."""

    def __init__(self, guild_id: int, user_id: int, sequence: list[int], picked: list[int]):
        super().__init__(timeout=60)
        picked_text = " ".join(f"`{n}`" for n in picked) if picked else "*(chưa bấm số nào)*"
        lines = [
            "### 🧩 Trí Nhớ — Bấm lại đúng thứ tự",
            f"Đã bấm: {picked_text}  ({len(picked)}/{len(sequence)})",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(*[
                MemoryDigitButton(guild_id, user_id, sequence, picked, n) for n in range(1, 6)
            ]),
            discord.ui.ActionRow(*[
                MemoryDigitButton(guild_id, user_id, sequence, picked, n) for n in range(6, 10)
            ]),
            accent_color=discord.Colour.teal(),
        )
        self.add_item(container)


class MemoryDigitButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int, sequence: list[int], picked: list[int], digit: int):
        super().__init__(label=str(digit), style=discord.ButtonStyle.secondary)
        self.guild_id, self.user_id = guild_id, user_id
        self.sequence, self.picked, self.digit = sequence, picked, digit

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        new_picked = self.picked + [self.digit]
        expected = self.sequence[len(self.picked)]

        if self.digit != expected:
            # Bấm sai số ngay lập tức -> kết thúc ván, thua luôn.
            await interaction.response.defer()
            try:
                await firebase.add_aura(self.guild_id, self.user_id, -MEMORY_AURA_REWARD)
            except firebase.FirebaseUnavailable:
                pass
            seq_text = " ".join(f"`{n}`" for n in self.sequence)
            text = (
                f"{ICON_CROSS} Sai rồi! Dãy đúng là: {seq_text}.\n"
                f"Bạn bị trừ **-{MEMORY_AURA_REWARD} {ICON_AURA}**."
            )
            await interaction.edit_original_response(view=GameResultView(text))
            return

        if len(new_picked) == len(self.sequence):
            # Bấm đủ và đúng hết -> thắng.
            await interaction.response.defer()
            try:
                await firebase.add_deltan(self.guild_id, self.user_id, MEMORY_DELTAN_REWARD)
                await firebase.add_aura(self.guild_id, self.user_id, MEMORY_AURA_REWARD)
                text = (
                    f"{ICON_CHECK} Chính xác! Bạn nhận **+{MEMORY_DELTAN_REWARD} {ICON_DELTAN}** "
                    f"và **+{MEMORY_AURA_REWARD} {ICON_AURA}**!"
                )
            except firebase.FirebaseUnavailable:
                text = f"{ICON_WARNING} Đã ghi nhận kết quả nhưng không cộng/trừ được Deltan/Aura do lỗi kết nối. Vé đã bị trừ, báo admin nếu cần hoàn lại."
            await interaction.edit_original_response(view=GameResultView(text))
            return

        # Đúng nhưng chưa đủ dãy -> cập nhật lại bàn phím, tiếp tục bấm.
        await interaction.response.edit_message(
            view=MemoryInputView(self.guild_id, self.user_id, self.sequence, new_picked)
        )


# ==================== 5 GAME TRẮC NGHIỆM (Cờ/Quốc gia/Văn hoá/Ứng dụng/Truyền thống) ====================
QUIZ_GAME_CONFIG = {
    "flag": {"title": "🚩 Đoán Cờ", "pool": FLAG_QUESTIONS, "prompt_label": "Lá cờ này là của quốc gia nào?", "big_prompt": True},
    "country": {"title": "🌍 Đoán Quốc Gia", "pool": COUNTRY_QUESTIONS, "prompt_label": "Đoán quốc gia qua gợi ý:", "big_prompt": False},
    "culture": {"title": "🎎 Đoán Văn Hoá", "pool": CULTURE_QUESTIONS, "prompt_label": "Nét văn hoá này thuộc về đâu?", "big_prompt": False},
    "app": {"title": "📱 Đoán Ứng Dụng", "pool": APP_QUESTIONS, "prompt_label": "Đây là ứng dụng nào?", "big_prompt": False},
    "tradition": {"title": "🏮 Đoán Truyền Thống", "pool": TRADITION_QUESTIONS, "prompt_label": "Đây là lễ hội/truyền thống nào?", "big_prompt": False},
}


class QuizGameView(discord.ui.LayoutView):
    """View chung cho 5 game trắc nghiệm (Cờ/Quốc gia/Văn hoá/Ứng dụng/Truyền thống)."""

    def __init__(self, guild_id: int, user_id: int, quiz_key: str):
        super().__init__(timeout=60)
        cfg = QUIZ_GAME_CONFIG[quiz_key]
        lines = [
            f"### {cfg['title']}",
            f"Không may rủi — chọn đúng luôn thắng! Tốn **{GAME_TICKET_COST}** {ICON_TICKET} mỗi lượt.",
            f"Đúng nhận **+{QUIZ_DELTAN_REWARD} {ICON_DELTAN}** và **+{QUIZ_AURA_REWARD} {ICON_AURA}** "
            f"— sai bị trừ **-{QUIZ_AURA_REWARD} {ICON_AURA}**.",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(QuizStartButton(guild_id, user_id, quiz_key)),
            accent_color=discord.Colour.teal(),
        )
        self.add_item(container)


class QuizStartButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int, quiz_key: str):
        super().__init__(label="Bắt đầu", style=discord.ButtonStyle.success, emoji="▶️")
        self.guild_id, self.user_id, self.quiz_key = guild_id, user_id, quiz_key

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        await interaction.response.defer()

        try:
            spend = await _spend_ticket_or_none(self.guild_id, self.user_id)
        except firebase.FirebaseUnavailable:
            await interaction.edit_original_response(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if not spend["ok"]:
            await interaction.edit_original_response(
                view=GameResultView(_format_no_ticket_message(spend)),
            )
            return

        cfg = QUIZ_GAME_CONFIG[self.quiz_key]
        question = generate_quiz_question(cfg["pool"])
        await interaction.edit_original_response(
            view=QuizQuestionView(self.guild_id, self.user_id, self.quiz_key, question)
        )


class QuizQuestionView(discord.ui.LayoutView):
    def __init__(self, guild_id: int, user_id: int, quiz_key: str, question: dict):
        super().__init__(timeout=60)
        cfg = QUIZ_GAME_CONFIG[quiz_key]
        prompt_display = f"# {question['prompt']}" if cfg["big_prompt"] else f"**{question['prompt']}**"
        lines = [
            f"### {cfg['title']}",
            cfg["prompt_label"],
            prompt_display,
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(*[
                QuizOptionButton(guild_id, user_id, quiz_key, question, opt)
                for opt in question["options"]
            ]),
            accent_color=discord.Colour.teal(),
        )
        self.add_item(container)


class QuizOptionButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int, quiz_key: str, question: dict, option: str):
        super().__init__(label=option[:80], style=discord.ButtonStyle.secondary)
        self.guild_id, self.user_id, self.quiz_key = guild_id, user_id, quiz_key
        self.question, self.option = question, option

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        await interaction.response.defer()
        win = self.option == self.question["correct"]

        try:
            if win:
                await firebase.add_deltan(self.guild_id, self.user_id, QUIZ_DELTAN_REWARD)
                await firebase.add_aura(self.guild_id, self.user_id, QUIZ_AURA_REWARD)
                text = (
                    f"{ICON_CHECK} Chính xác! Đáp án là **{self.question['correct']}**.\n"
                    f"Bạn nhận **+{QUIZ_DELTAN_REWARD} {ICON_DELTAN}** và **+{QUIZ_AURA_REWARD} {ICON_AURA}**!"
                )
            else:
                await firebase.add_aura(self.guild_id, self.user_id, -QUIZ_AURA_REWARD)
                text = (
                    f"{ICON_CROSS} Sai rồi! Đáp án đúng là **{self.question['correct']}**.\n"
                    f"Bạn bị trừ **-{QUIZ_AURA_REWARD} {ICON_AURA}**."
                )
        except firebase.FirebaseUnavailable:
            text = f"{ICON_WARNING} Đã ghi nhận kết quả nhưng không cộng/trừ được Deltan/Aura do lỗi kết nối. Vé đã bị trừ, báo admin nếu cần hoàn lại."

        await interaction.edit_original_response(view=GameResultView(text))


# ==================== WORDLE ====================
class WordleModeView(discord.ui.LayoutView):
    """Bước 1 của /game -> Wordle: chọn tiếng Anh hay tiếng Việt không dấu."""

    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=60)
        lines = [
            "### 🟩 Wordle",
            f"Đoán đúng từ bí mật gồm **{WORDLE_WORD_LENGTH} chữ cái** trong tối đa "
            f"**{WORDLE_MAX_GUESSES} lượt**. Tốn **{GAME_TICKET_COST}** {ICON_TICKET} mỗi ván.",
            f"Thắng nhận **+{WORDLE_DELTAN_REWARD} {ICON_DELTAN}** và **+{WORDLE_AURA_REWARD} {ICON_AURA}** "
            f"— thua bị trừ **-{WORDLE_AURA_REWARD} {ICON_AURA}**.",
            "-# Chọn chế độ chơi:",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(
                WordleModeButton(guild_id, user_id, "en", "Tiếng Anh", "🇬🇧"),
                WordleModeButton(guild_id, user_id, "vi", "Tiếng Việt (không dấu)", "🇻🇳"),
            ),
            accent_color=discord.Colour.green(),
        )
        self.add_item(container)


class WordleModeButton(discord.ui.Button):
    def __init__(self, guild_id: int, user_id: int, mode: str, label: str, emoji: str):
        super().__init__(label=label, style=discord.ButtonStyle.success, emoji=emoji)
        self.guild_id, self.user_id, self.mode = guild_id, user_id, mode

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.user_id):
            return

        # Trừ vé ngay khi bắt đầu ván (không defer vì cần mở modal ngay sau
        # response đầu tiên — modal chỉ mở được từ interaction CHƯA response).
        try:
            spend = await _spend_ticket_or_none(self.guild_id, self.user_id)
        except firebase.FirebaseUnavailable:
            await interaction.response.edit_message(
                view=GameResultView(f"{ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!"),
            )
            return

        if not spend["ok"]:
            await interaction.response.edit_message(
                view=GameResultView(_format_no_ticket_message(spend)),
            )
            return

        secret = wordle_pick_word(self.mode)
        state = WordleState(self.guild_id, self.user_id, self.mode, secret)
        await interaction.response.edit_message(view=WordleGameView(state))


class WordleState:
    """Giữ trạng thái 1 ván Wordle đang chơi (không lưu Firebase, chỉ lưu tạm
    trong bộ nhớ view — mất khi bot restart, chấp nhận được vì ván chơi ngắn)."""

    def __init__(self, guild_id: int, user_id: int, mode: str, secret: str):
        self.guild_id = guild_id
        self.user_id = user_id
        self.mode = mode
        self.secret = secret
        self.guesses: list[str] = []
        self.rows: list[list[str]] = []
        self.finished = False


def _wordle_render_lines(state: WordleState) -> list[str]:
    mode_label = "Tiếng Anh" if state.mode == "en" else "Tiếng Việt (không dấu)"
    lines = [
        "### 🟩 Wordle",
        f"-# Chế độ: **{mode_label}** · Từ dài **{WORDLE_WORD_LENGTH}** chữ · "
        f"Lượt **{len(state.guesses)}/{WORDLE_MAX_GUESSES}**",
    ]
    if not state.guesses:
        lines.append("*Chưa đoán lượt nào. Bấm nút bên dưới để nhập từ đoán đầu tiên!*")
    for guess, row in zip(state.guesses, state.rows):
        squares = " ".join(row)
        letters = "  ".join(guess.upper())
        lines.append(f"{squares}\n`{letters}`")
    return lines


class WordleGameView(discord.ui.LayoutView):
    """Hiển thị lưới các lượt đoán đã có + nút mở modal để nhập lượt tiếp theo."""

    def __init__(self, state: WordleState):
        super().__init__(timeout=180)
        lines = _wordle_render_lines(state)
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(WordleGuessButton(state)),
            accent_color=discord.Colour.green(),
        )
        self.add_item(container)


class WordleGuessButton(discord.ui.Button):
    def __init__(self, state: WordleState):
        remaining = WORDLE_MAX_GUESSES - len(state.guesses)
        super().__init__(
            label=f"Nhập từ đoán ({remaining} lượt còn lại)",
            style=discord.ButtonStyle.primary,
            emoji="⌨️",
        )
        self.state = state

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.state.user_id):
            return
        await interaction.response.send_modal(WordleGuessModal(self.state))


class WordleGuessModal(discord.ui.Modal):
    """Bảng nhập (modal) để gõ từ đoán, thay vì phải gõ lệnh/chat ra kênh."""

    def __init__(self, state: WordleState):
        super().__init__(title=f"Wordle — Lượt {len(state.guesses) + 1}/{WORDLE_MAX_GUESSES}")
        self.state = state
        self.guess_input = discord.ui.TextInput(
            label=f"Từ đoán của bạn ({WORDLE_WORD_LENGTH} chữ cái)",
            placeholder="Nhập không dấu, không khoảng trắng...",
            min_length=WORDLE_WORD_LENGTH,
            max_length=WORDLE_WORD_LENGTH,
            required=True,
        )
        self.add_item(self.guess_input)

    async def on_submit(self, interaction: discord.Interaction):
        state = self.state
        guess = self.guess_input.value.strip().lower()

        if len(guess) != WORDLE_WORD_LENGTH or not guess.isalpha():
            await interaction.response.send_message(
                f"{ICON_CROSS} Từ đoán phải gồm đúng **{WORDLE_WORD_LENGTH} chữ cái**, không số/không ký tự đặc biệt.",
                ephemeral=True,
            )
            return

        row = wordle_score_guess(guess, state.secret)
        state.guesses.append(guess)
        state.rows.append(row)

        won = guess == state.secret
        lost_out_of_guesses = not won and len(state.guesses) >= WORDLE_MAX_GUESSES

        if won or lost_out_of_guesses:
            state.finished = True
            lines = _wordle_render_lines(state)

            try:
                if won:
                    await firebase.add_deltan(state.guild_id, state.user_id, WORDLE_DELTAN_REWARD)
                    await firebase.add_aura(state.guild_id, state.user_id, WORDLE_AURA_REWARD)
                    lines.append(
                        f"\n{ICON_CHECK} Chính xác! Từ bí mật là **{state.secret.upper()}**. "
                        f"Bạn nhận **+{WORDLE_DELTAN_REWARD} {ICON_DELTAN}** và **+{WORDLE_AURA_REWARD} {ICON_AURA}**!"
                    )
                else:
                    await firebase.add_aura(state.guild_id, state.user_id, -WORDLE_AURA_REWARD)
                    lines.append(
                        f"\n{ICON_CROSS} Hết lượt! Từ bí mật là **{state.secret.upper()}**. "
                        f"Bạn bị trừ **-{WORDLE_AURA_REWARD} {ICON_AURA}**."
                    )
            except firebase.FirebaseUnavailable:
                lines.append(
                    f"\n{ICON_WARNING} Đã ghi nhận kết quả nhưng không cộng/trừ được Deltan/Aura do lỗi kết nối."
                )

            container = discord.ui.Container(
                discord.ui.TextDisplay("\n".join(lines)),
                accent_color=discord.Colour.green() if won else discord.Colour.red(),
            )
            final_view = discord.ui.LayoutView(timeout=None)
            final_view.add_item(container)
            await interaction.response.edit_message(view=final_view)
            return

        # Chưa thắng/thua — hiện lại view với lượt đoán mới, cho đoán tiếp.
        await interaction.response.edit_message(view=WordleGameView(state))


# ==================== /mix-emoji ====================
class MixEmojiView(discord.ui.LayoutView):
    """View của /mix-emoji: bấm nút để mở bảng (modal) nhập 2 emoji, thay vì
    phải gõ trực tiếp 2 emoji vào tham số lệnh."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        lines = [
            "### 🍳 Mix Emoji",
            "Ghép 2 emoji thành 1 ảnh mashup (Google Emoji Kitchen). "
            "Bấm nút bên dưới để mở bảng nhập emoji.",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(MixEmojiButton(owner_id)),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


class MixEmojiButton(discord.ui.Button):
    def __init__(self, owner_id: int):
        super().__init__(label="Mix Emoji", style=discord.ButtonStyle.primary, emoji="🍳")
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.owner_id):
            return
        await interaction.response.send_modal(MixEmojiModal(self.owner_id))


class MixEmojiModal(discord.ui.Modal):
    """Bảng nhập 2 emoji để ghép, thay cho việc gõ 2 tham số emoji vào chat."""

    def __init__(self, owner_id: int):
        super().__init__(title="Mix Emoji")
        self.owner_id = owner_id
        self.emoji1_input = discord.ui.TextInput(
            label="Emoji thứ nhất", placeholder="Vd: 🔥", min_length=1, max_length=8, required=True,
        )
        self.emoji2_input = discord.ui.TextInput(
            label="Emoji thứ hai", placeholder="Vd: 😃", min_length=1, max_length=8, required=True,
        )
        self.add_item(self.emoji1_input)
        self.add_item(self.emoji2_input)

    async def on_submit(self, interaction: discord.Interaction):
        import emoji_mixer  # import cục bộ để tránh phụ thuộc vòng module

        await interaction.response.defer(thinking=True)
        emoji1 = self.emoji1_input.value.strip()
        emoji2 = self.emoji2_input.value.strip()

        result = await emoji_mixer.find_emoji_mix_url(emoji1, emoji2)

        if not result["ok"]:
            reason = result["reason"]
            if reason == "invalid":
                text = (
                    f"{ICON_CROSS} Chỉ nhập được **1 emoji đơn** cho mỗi ô "
                    f"(không phải chuỗi emoji ghép sẵn kiểu 👨‍👩‍👧)."
                )
            elif reason == "network":
                text = f"{ICON_WARNING} Không kết nối được để kiểm tra ảnh ghép lúc này, thử lại sau nhé!"
            else:
                text = (
                    f"{ICON_CROSS} Không tìm thấy ảnh ghép cho **{emoji1} + {emoji2}**. "
                    f"Không phải cặp emoji nào cũng có ảnh ghép (Google vẽ tay thủ công từng cặp), thử cặp khác xem sao!"
                )
            await interaction.followup.send(text, ephemeral=True)
            return

        container = discord.ui.Container(
            discord.ui.TextDisplay(f"### 🍳 {emoji1} + {emoji2}"),
            discord.ui.MediaGallery(discord.MediaGalleryItem(result["url"])),
            accent_color=discord.Colour.blurple(),
        )
        result_view = discord.ui.LayoutView(timeout=None)
        result_view.add_item(container)
        await interaction.followup.send(view=result_view)
