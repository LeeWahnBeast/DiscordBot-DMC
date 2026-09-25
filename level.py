"""
Logic XP / Level / Aura / Deltan + giao diện Components V2 (LevelUp, Hồ sơ công dân).
"""

import time
import random
import string

import discord

import firebase

# ==================== ICON ====================
ICON_XP = "<:xp:1553010318861537380>"
ICON_LEVEL = "<:level:1553010322070306826>"
ICON_AURA = "<:aura:1553010328424546364>"
ICON_DELTAN = "<:deltan:1553010324758593649>"

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
        created_text = time.strftime("%d/%m/%Y %H:%M", time.localtime(created_at)) if created_at else "N/A"

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
