"""
Logic XP / Level / Aura / Deltan + giao diện Components V2 (LevelUp, Hồ sơ công dân).
"""

import time
import random
import string

import discord

import firebase

# ==================== ICON ====================
ICON_XP = "<:xp:1552997682895913033>"
ICON_LEVEL = "<:level:1552996347622334474>"
ICON_AURA = "<:aura:1552995576432562176>"
ICON_DELTAN = "<:deltan:1552990527522078750>"

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

CITIZEN_ROLE_NAME = "Công Dân"


# ==================== CÔNG THỨC XP / LEVEL (kiểu MEE6) ====================
def xp_required_for_level(level: int) -> int:
    return 5 * (level ** 2) + 50 * level + 100


def level_from_total_xp(total_xp: int):
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
def add_xp(guild_id: int, user_id: int, amount: int) -> dict:
    user = firebase.get_user(guild_id, user_id)
    old_xp = user.get("xp", 0)
    old_level, _, _ = level_from_total_xp(old_xp)

    new_xp = old_xp + amount
    new_level, _, _ = level_from_total_xp(new_xp)
    levels_gained = new_level - old_level

    deltan_gained = 0
    aura_gained = 0.0
    if levels_gained > 0:
        for lvl in range(old_level + 1, new_level + 1):
            deltan_gained += LEVEL_UP_DELTAN_BONUS
            aura_gained += aura_bonus_for_level(lvl)

    updated = {
        "xp": new_xp,
        "level": new_level,
        "deltan": user.get("deltan", 0) + deltan_gained,
        "aura": round(user.get("aura", 0.0) + aura_gained, 2),
    }
    firebase.save_user(guild_id, user_id, updated)

    merged = dict(user)
    merged.update(updated)

    return {
        "leveled_up": levels_gained > 0,
        "new_level": new_level,
        "deltan_gained": deltan_gained,
        "aura_gained": aura_gained,
        "user": merged,
    }


def random_message_xp() -> int:
    return random.randint(MESSAGE_XP_MIN, MESSAGE_XP_MAX)


def random_voice_xp() -> int:
    return random.randint(VOICE_XP_MIN, VOICE_XP_MAX)


def generate_citizen_id() -> str:
    return "CD-" + "".join(random.choices(string.digits, k=6))


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
    """Hồ sơ công dân bằng Container Components V2."""

    def __init__(self, member: discord.Member, user_data: dict, citizen_data: dict, is_new: bool):
        super().__init__(timeout=None)

        level = user_data.get("level", 0)
        xp = user_data.get("xp", 0)
        _, xp_in_level, xp_needed = level_from_total_xp(xp)

        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        roles_text = ", ".join(roles) if roles else "*Chưa có role*"
        joined_at = member.joined_at.strftime("%d/%m/%Y") if member.joined_at else "Không rõ"
        citizen_id = citizen_data.get("citizen_id", "N/A")
        created_at = citizen_data.get("created_at")
        created_text = time.strftime("%d/%m/%Y %H:%M", time.localtime(created_at)) if created_at else "N/A"

        lines = [
            "## 🪪 HỒ SƠ CÔNG DÂN" + (" *(mới tạo)*" if is_new else ""),
            f"**Mã công dân:** `{citizen_id}`",
            f"**Thành viên:** {member.mention}",
            f"**Ngày cấp:** {created_text}",
            f"**Ngày vào server:** {joined_at}",
            "",
            f"{ICON_LEVEL} **Level:** {level}  ({xp_in_level}/{xp_needed} xp)",
            f"{ICON_XP} **Tổng XP:** {xp}",
            f"{ICON_AURA} **Aura:** {user_data.get('aura', 0.0)}",
            f"{ICON_DELTAN} **Deltan:** {user_data.get('deltan', 0)}",
            "",
            f"**Vai trò:** {roles_text}",
        ]
        container = discord.ui.Container(
            discord.ui.Section(
                "\n".join(lines),
                accessory=discord.ui.Thumbnail(media=member.display_avatar.url),
            ),
            accent_color=discord.Colour.blurple(),
        )
        self.add_item(container)
