"""
File chạy chính của bot Discord.
Xem README.md để biết cách setup Discord Bot + Firebase + deploy Render.
"""

import os
import time
import asyncio
import logging

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

import firebase
import level
from keepalive import keep_alive

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID") or None

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
log = logging.getLogger("bot")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
_message_cooldowns: dict[tuple[int, int], float] = {}


# ==================== XP KHI NHẮN TIN ====================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    key = (message.guild.id, message.author.id)
    now = time.time()
    if now - _message_cooldowns.get(key, 0) < level.MESSAGE_XP_COOLDOWN:
        return
    _message_cooldowns[key] = now

    result = level.add_xp(message.guild.id, message.author.id, level.random_message_xp())
    if result["leveled_up"]:
        try:
            await message.channel.send(view=level.LevelUpView(message.author, result))
        except discord.HTTPException:
            pass


# ==================== XP KHI VOICE CHAT ====================
def _find_notify_channel(guild: discord.Guild):
    for ch in guild.text_channels:
        if "level" in ch.name.lower():
            return ch
    for ch in guild.text_channels:
        if "general" in ch.name.lower() or "chat" in ch.name.lower():
            return ch
    return guild.text_channels[0] if guild.text_channels else None


@tasks.loop(seconds=level.VOICE_XP_INTERVAL_SECONDS)
async def voice_xp_task():
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            if level.VOICE_IGNORE_AFK_CHANNEL and guild.afk_channel and vc.id == guild.afk_channel.id:
                continue

            members = [m for m in vc.members if not m.bot]
            if level.VOICE_REQUIRE_NOT_ALONE and len(members) < 2:
                continue

            for member in members:
                vs = member.voice
                if not vs:
                    continue
                if level.VOICE_IGNORE_IF_MUTED_DEAFENED and (vs.self_mute or vs.self_deaf):
                    continue

                result = level.add_xp(guild.id, member.id, level.random_voice_xp())
                if result["leveled_up"]:
                    target = _find_notify_channel(guild)
                    if target:
                        try:
                            await target.send(view=level.LevelUpView(member, result))
                        except discord.HTTPException:
                            pass


@voice_xp_task.before_loop
async def before_voice_xp_task():
    await bot.wait_until_ready()


# ==================== LỆNH /công-dân ====================
async def ensure_citizen_role(guild: discord.Guild):
    role = discord.utils.get(guild.roles, name=level.CITIZEN_ROLE_NAME)
    if role is None:
        try:
            role = await guild.create_role(
                name=level.CITIZEN_ROLE_NAME,
                reason="Tự động tạo role Công Dân",
                colour=discord.Colour.blurple(),
            )
        except discord.Forbidden:
            return None
    return role


@bot.tree.command(name="công-dân", description="Tạo hoặc xem hồ sơ công dân của bạn trong server")
async def citizen(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    guild = interaction.guild
    member = interaction.user

    user_data = firebase.get_user(guild.id, member.id)
    citizen_data = firebase.get_citizen(guild.id, member.id)
    is_new = not citizen_data

    if is_new:
        firebase.create_citizen(guild.id, member.id, level.generate_citizen_id())
        citizen_data = firebase.get_citizen(guild.id, member.id)
        role = await ensure_citizen_role(guild)
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="Tạo hồ sơ công dân qua /công-dân")
            except discord.Forbidden:
                pass

    await interaction.followup.send(view=level.CitizenView(member, user_data, citizen_data, is_new))


# ==================== SỰ KIỆN KHỞI ĐỘNG ====================
@bot.event
async def on_ready():
    log.info(f"Đã đăng nhập với tên {bot.user} (ID: {bot.user.id})")

    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            log.info(f"Đã đồng bộ {len(synced)} slash command cho guild {GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            log.info(f"Đã đồng bộ {len(synced)} slash command (global, có thể mất tới 1h để hiện)")
    except Exception as e:
        log.exception(f"Lỗi khi đồng bộ slash command: {e}")

    if not voice_xp_task.is_running():
        voice_xp_task.start()

    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="XP & Level | /công-dân")
    )


async def main():
    firebase.init_firebase()
    log.info("Đã kết nối Firebase Realtime Database.")

    # Retry có chờ khi login bị Discord rate-limit (429), tránh crash-loop
    # càng làm nặng thêm rate limit (Render tự restart process ngay khi bot thoát).
    delay = 30
    max_delay = 300
    while True:
        try:
            async with bot:
                await bot.start(DISCORD_TOKEN)
            return  # bot.start thoát bình thường (ví dụ do logout) -> dừng hẳn
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, "retry_after", None) or delay
                log.warning(f"Bị Discord rate-limit (429) khi login. Chờ {retry_after:.0f}s rồi thử lại...")
                await asyncio.sleep(retry_after)
                delay = min(delay * 2, max_delay)
                continue
            raise


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Thiếu DISCORD_TOKEN trong biến môi trường (.env)")
    if not os.getenv("FIREBASE_DB_URL"):
        raise SystemExit("Thiếu FIREBASE_DB_URL trong biến môi trường (.env)")

    keep_alive()
    asyncio.run(main())
