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

# ==================== CẤU HÌNH THÚ TỘI ẨN DANH ====================
CONFESSION_CHANNEL_ID = 1539855082210861126

# ==================== CẤU HÌNH BẢNG XẾP HẠNG ====================
LEADERBOARD_FIELDS = {
    "deltan": {"label": "Deltan", "icon": ICON_DELTAN, "fmt": lambda v: f"{int(v):,}"},
    "level": {"label": "Level", "icon": ICON_LEVEL, "fmt": lambda v: f"{int(v):,}"},
    "aura": {"label": "Aura", "icon": ICON_AURA, "fmt": lambda v: f"{v:,.2f}"},
}
LEADERBOARD_SIZE = 10
_RANK_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


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


DAILY_STREAK_DISPLAY_MAX = 7  # số icon tối đa hiển thị trên 1 dòng, streak dài hơn thì rút gọn


def format_daily_streak(user_data: dict) -> str:
    """
    Chuỗi icon biểu diễn daily streak, ví dụ ✅✅✅✅✅⚠️:
      - Mỗi ✅ là 1 ngày đã điểm danh liên tục trong streak hiện tại.
      - Icon cuối cùng là trạng thái HÔM NAY: ✅ nếu đã điểm danh, ⚠️ nếu
        daily đang mở nhưng chưa điểm danh (sắp mất streak nếu không bấm kịp),
        ❌ nếu đã lỡ (daily đã đóng, streak sẽ reset ở lần điểm danh tới).
      - Streak dài hơn khung hiển thị thì chỉ hiện icon + số streak thật.
    """
    streak = user_data.get("daily_streak", 0)
    claimed_today = user_data.get("last_daily_date") == today_str()

    if claimed_today:
        shown = min(streak, DAILY_STREAK_DISPLAY_MAX)
        icons = ICON_CHECK * max(shown, 1)
    else:
        status = ICON_WARNING if is_daily_open() else ICON_CROSS
        shown = min(streak, DAILY_STREAK_DISPLAY_MAX - 1)
        icons = (ICON_CHECK * shown) + status

    if streak > DAILY_STREAK_DISPLAY_MAX:
        return f"🔥 **{streak}** ngày · {icons}"
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
                f"{ICON_WARNING} Bạn đã nhận daily hôm nay rồi, quay lại vào ngày mai nhé!",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"{ICON_CHECK} Bạn nhận được **+{result['deltan_gained']} {ICON_DELTAN} Deltan**! "
            f"🔥 Streak hiện tại: **{result['streak']}** ngày.",
            ephemeral=True,
        )


# ==================== VÉ GAME / MINI GAME ====================
GAME_CHOICES = [
    "đoán số", "kéo búa bao", "xúc xắc",
    "tài xỉu", "đoán chất bài", "vòng quay may mắn", "mở rương kho báu",
]

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
        "👑 Owner": [],
        "🛡️ Admin": [],
        "🔧 Mod": [],
        "🤖 Bot": [],
        "🏷️ Khác": [],
    }

    is_owner = guild.owner_id == member.id
    roles = [r for r in member.roles if r.name != "@everyone"]

    for role in roles:
        name_lower = role.name.lower()
        if is_owner and ("owner" in name_lower or "chủ" in name_lower):
            groups["👑 Owner"].append(role)
        elif role.permissions.administrator or "admin" in name_lower or "quản trị" in name_lower:
            groups["🛡️ Admin"].append(role)
        elif (
            "mod" in name_lower
            or role.permissions.manage_messages
            or role.permissions.kick_members
            or "kiểm duyệt" in name_lower
        ):
            groups["🔧 Mod"].append(role)
        elif "bot" in name_lower:
            groups["🤖 Bot"].append(role)
        else:
            groups["🏷️ Khác"].append(role)

    if is_owner and not groups["👑 Owner"]:
        groups["👑 Owner"].append(None)  # đánh dấu "là chủ server" dù không có role riêng

    return groups


def _format_roles_block(member: discord.Member, max_per_group: int = 4) -> str:
    groups = _classify_roles(member)
    lines = []
    for label, roles in groups.items():
        if not roles:
            continue
        if label == "👑 Owner" and roles == [None]:
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
            f"## 🎉 {member.mention} vừa lên **Level {result['new_level']}**!",
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
            f"🔥 **Daily Streak:** {format_daily_streak(user_data)}",
        ]

        roles_lines = [
            "### 🎖️ Vai trò",
            _format_roles_block(member),
        ]

        container = discord.ui.Container(
            discord.ui.Section(
                "\n".join(header_lines),
                accessory=discord.ui.Thumbnail(media=member.display_avatar.url),
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay("\n".join(stats_lines)),
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
            f"🔥 **Daily Streak:** {format_daily_streak(user_data)}",
        ]

        container = discord.ui.Container(
            discord.ui.Section(
                "\n".join(lines),
                accessory=discord.ui.Thumbnail(media=member.display_avatar.url),
            ),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


# ==================== BẢNG XẾP HẠNG ====================
class LeaderboardView(discord.ui.LayoutView):
    """Hiển thị top 10 theo Deltan / Level / Aura, dùng cho lệnh /bảng-xếp-hạng."""

    def __init__(self, guild: discord.Guild, field: str, ranked: list[tuple[int, dict]]):
        super().__init__(timeout=None)
        meta = LEADERBOARD_FIELDS[field]

        lines = [f"## 🏆 BẢNG XẾP HẠNG — {meta['label'].upper()}"]

        if not ranked:
            lines.append("*Chưa có dữ liệu nào để xếp hạng.*")
        else:
            for i, (user_id, data) in enumerate(ranked, start=1):
                medal = _RANK_MEDALS.get(i, f"`#{i}`")
                member = guild.get_member(user_id)
                name = member.mention if member else f"`{user_id}`"
                value = meta["fmt"](data.get(field, 0) or 0)
                lines.append(f"{medal} {name} — **{value}** {meta['icon']}")

        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            accent_color=discord.Colour.gold(),
        )
        self.add_item(container)


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
    """Menu chọn mini game cho lệnh /game."""

    def __init__(self, owner_id: int, tickets: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        lines = [
            "## 🎮 MINI GAME",
            f"{ICON_TICKET} Vé của bạn: **{tickets}**  •  Mỗi lượt chơi tốn **{GAME_TICKET_COST}** {ICON_TICKET}",
            "-# 3 game đầu thắng nhận lại vé + Deltan. 4 game dưới thắng nhận Deltan + Aura, "
            "**thua sẽ bị trừ Aura** — game càng khó thưởng/phạt càng cao.",
            "-# Chọn một trò chơi bên dưới:",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.ActionRow(
                GameChoiceButton(owner_id, "guess", "Đoán Số", "🔢"),
                GameChoiceButton(owner_id, "rps", "Kéo Búa Bao", "✊"),
                GameChoiceButton(owner_id, "dice", "Xúc Xắc", "🎲"),
            ),
            discord.ui.ActionRow(
                GameChoiceButton(owner_id, "taixiu", "Tài Xỉu", "🎲"),
                GameChoiceButton(owner_id, "bai", "Đoán Chất Bài", "🃏"),
                GameChoiceButton(owner_id, "vongquay", "Vòng Quay May Mắn", "🎡"),
                GameChoiceButton(owner_id, "ruong", "Mở Rương Kho Báu", "🗝️"),
            ),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)


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
        elif self.game_key in GAME_DEFS:
            await interaction.response.send_message(
                view=NewGameView(interaction.guild.id, interaction.user.id, self.game_key),
                ephemeral=True,
            )


async def _spend_ticket_or_none(guild_id: int, user_id: int) -> dict:
    """Trả về dict {"ok": bool, "tickets": int, "next_regen_in": int|None} từ firebase.use_ticket."""
    return await firebase.use_ticket(
        guild_id, user_id, GAME_TICKET_COST, TICKETS_MAX, TICKETS_REGEN_SECONDS, today_str()
    )


def _format_no_ticket_message(result: dict) -> str:
    """Thông báo khi hết vé, kèm thời gian hồi vé kế tiếp nếu có."""
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
