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
import aiohttp

import firebase
import level
import tiktok
from keepalive import keep_alive

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID") or None

TIKTOK_USERNAME = os.getenv("TIKTOK_USERNAME", "tahnuyo_0")
TIKTOK_CHECK_INTERVAL_SECONDS = 5 * 60 * 60  # cứ 5 tiếng check 1 lần
BOT_NAME_SUFFIX = " Bot"  # tên bot = "<Nickname TikTok> Bot", vd "Delta Mick Bot"

# Kênh thông báo lên level khi XP đến từ voice chat (không nhắn tin nên không
# có "kênh vừa nhắn" để dùng). Để trống (None) thì bot sẽ tự đoán 1 kênh text
# hợp lý (kênh có chữ "level", rồi "general"/"chat", rồi kênh đầu tiên).
VOICE_LEVEL_UP_CHANNEL_ID = os.getenv("VOICE_LEVEL_UP_CHANNEL_ID")
VOICE_LEVEL_UP_CHANNEL_ID = int(VOICE_LEVEL_UP_CHANNEL_ID) if VOICE_LEVEL_UP_CHANNEL_ID else None

# Dọn dẹp cooldown message-XP định kỳ để dict không phình to mãi theo thời gian
# (nếu không dọn, mỗi user từng nhắn 1 lần sẽ ở lại trong bộ nhớ vĩnh viễn).
COOLDOWN_CLEANUP_INTERVAL_SECONDS = 30 * 60

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

    # Đếm tin nhắn trong kênh daily để biết khi nào cần gửi lại container.
    if message.channel.id == level.DAILY_CHANNEL_ID:
        try:
            await firebase.increment_daily_message_count()
        except firebase.FirebaseUnavailable:
            log.warning("Không đếm được tin nhắn kênh daily (Firebase lỗi).")

    # Tính XP ở BẤT KỲ kênh nào có tin nhắn (không giới hạn kênh cụ thể).
    key = (message.guild.id, message.author.id)
    now = time.time()
    if now - _message_cooldowns.get(key, 0) < level.MESSAGE_XP_COOLDOWN:
        return
    _message_cooldowns[key] = now

    try:
        result = await level.add_xp(message.guild.id, message.author.id, level.random_message_xp())
    except firebase.FirebaseUnavailable:
        log.warning(f"Không cộng được XP cho {message.author.id} (Firebase lỗi).")
        return

    if result["leveled_up"]:
        # Gửi thông báo lên level vào đúng kênh mà member vừa nhắn tin.
        try:
            await message.channel.send(view=level.LevelUpView(message.author, result))
        except discord.HTTPException:
            log.warning(f"Không gửi được thông báo lên level trong #{message.channel} (HTTP lỗi).")


@tasks.loop(seconds=COOLDOWN_CLEANUP_INTERVAL_SECONDS)
async def cooldown_cleanup_task():
    """Xoá khỏi bộ nhớ các cooldown đã hết hạn từ lâu, tránh dict phình to mãi."""
    cutoff = time.time() - level.MESSAGE_XP_COOLDOWN
    expired = [key for key, ts in _message_cooldowns.items() if ts < cutoff]
    for key in expired:
        _message_cooldowns.pop(key, None)
    if expired:
        log.info(f"Đã dọn {len(expired)} cooldown XP hết hạn khỏi bộ nhớ.")


@cooldown_cleanup_task.before_loop
async def before_cooldown_cleanup_task():
    await bot.wait_until_ready()


# ==================== XP KHI VOICE CHAT ====================
def _find_notify_channel(guild: discord.Guild):
    """
    Chọn kênh text để thông báo lên level do voice XP (không có "kênh vừa
    nhắn" trong trường hợp này). Ưu tiên VOICE_LEVEL_UP_CHANNEL_ID nếu được
    cấu hình, sau đó mới đoán theo tên kênh.
    """
    if VOICE_LEVEL_UP_CHANNEL_ID:
        channel = guild.get_channel(VOICE_LEVEL_UP_CHANNEL_ID)
        if channel:
            return channel

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

                try:
                    result = await level.add_xp(guild.id, member.id, level.random_voice_xp())
                except firebase.FirebaseUnavailable:
                    log.warning(f"Không cộng được voice XP cho {member.id} (Firebase lỗi).")
                    continue

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


# ==================== ĐỒNG BỘ TÊN BOT THEO TIKTOK ====================
async def sync_tiktok_name(force: bool = False) -> dict:
    """
    Kiểm tra tên/avatar TikTok của TIKTOK_USERNAME. Nếu có thay đổi so với
    lần trước (hoặc force=True), đổi tên bot thành "<Nickname TikTok> Bot"
    và đổi avatar bot theo TikTok. Không đụng tới tên/icon của server.
    """
    profile = await tiktok.fetch_tiktok_profile(TIKTOK_USERNAME)
    if not profile:
        return {"ok": False, "reason": "Không lấy được dữ liệu từ TikTok (mạng lỗi hoặc TikTok chặn)."}

    nickname = profile["nickname"]
    avatar_url = profile["avatar_url"]
    bot_name = (nickname + BOT_NAME_SUFFIX)[:32]

    try:
        state = await firebase.get_tiktok_sync_state()
    except firebase.FirebaseUnavailable:
        return {"ok": False, "reason": "Không đọc được trạng thái đồng bộ TikTok (Firebase lỗi)."}

    if not force and state.get("nickname") == nickname and state.get("avatar_url") == avatar_url:
        return {"ok": True, "changed": False}

    async with aiohttp.ClientSession() as session:
        avatar_bytes = await tiktok.download_bytes(session, avatar_url)

    if not avatar_bytes:
        return {"ok": False, "reason": "Không tải được ảnh đại diện TikTok."}

    errors: list[str] = []
    try:
        await bot.user.edit(username=bot_name, avatar=avatar_bytes)
    except discord.HTTPException as e:
        errors.append(f"đổi tên/avatar bot: {e}")

    try:
        await firebase.save_tiktok_sync_state({
            "nickname": nickname,
            "avatar_url": avatar_url,
            "updated_at": time.time(),
        })
    except firebase.FirebaseUnavailable:
        errors.append("lưu trạng thái đồng bộ vào Firebase thất bại")

    return {"ok": True, "changed": True, "bot_name": bot_name, "errors": errors}


@tasks.loop(seconds=TIKTOK_CHECK_INTERVAL_SECONDS)
async def tiktok_sync_task():
    result = await sync_tiktok_name()
    if not result.get("ok"):
        log.warning(f"Đồng bộ tên bot theo TikTok thất bại: {result.get('reason')}")
    elif result.get("changed"):
        log.info(f"Đã đổi tên bot theo TikTok @{TIKTOK_USERNAME}: {result.get('bot_name')}")
        if result.get("errors"):
            log.warning(f"Có lỗi khi đồng bộ TikTok: {result['errors']}")


@tiktok_sync_task.before_loop
async def before_tiktok_sync_task():
    await bot.wait_until_ready()


# Lệnh /đồng-bộ-tiktok thủ công đã bị xoá — tiktok_sync_task tự động chạy
# định kỳ mỗi TIKTOK_CHECK_INTERVAL_SECONDS nên không cần bấm tay nữa.


# ==================== LỆNH /level ====================
@bot.tree.command(name="level", description="Xem Level, XP, Aura, Deltan và vé game của bạn (hoặc người khác)")
@discord.app_commands.describe(thành_viên="Xem thông tin của thành viên khác (bỏ trống để xem của chính bạn)")
async def level_command(interaction: discord.Interaction, thành_viên: discord.Member | None = None):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
        return

    member = thành_viên or interaction.user
    await interaction.response.defer(thinking=True)
    try:
        # Dùng get_ticket_state để số vé hiển thị đã tính hồi theo giờ, không
        # phải giá trị Firebase thô có thể chưa cập nhật từ lần dùng vé cuối.
        user_data = await firebase.get_ticket_state(
            interaction.guild.id, member.id, level.TICKETS_MAX, level.TICKETS_REGEN_SECONDS, level.today_str()
        )
    except firebase.FirebaseUnavailable:
        await interaction.followup.send("❌ Không đọc được dữ liệu lúc này, thử lại sau nhé!")
        return

    await interaction.followup.send(view=level.LevelView(member, user_data))


# ==================== LỆNH /bảng-xếp-hạng ====================
@bot.tree.command(name="bảng-xếp-hạng", description="Xem bảng xếp hạng top 10 theo Deltan, Level hoặc Aura")
@discord.app_commands.describe(loại="Xếp hạng theo tiêu chí nào")
@discord.app_commands.choices(loại=[
    discord.app_commands.Choice(name="Deltan", value="deltan"),
    discord.app_commands.Choice(name="Level", value="level"),
    discord.app_commands.Choice(name="Aura", value="aura"),
])
async def leaderboard_command(interaction: discord.Interaction, loại: discord.app_commands.Choice[str]):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    try:
        ranked = await firebase.get_leaderboard(interaction.guild.id, loại.value, level.LEADERBOARD_SIZE)
    except firebase.FirebaseUnavailable:
        await interaction.followup.send("❌ Không đọc được dữ liệu lúc này, thử lại sau nhé!")
        return

    await interaction.followup.send(view=level.LeaderboardView(interaction.guild, loại.value, ranked))


# ==================== LỆNH /game ====================
@bot.tree.command(name="game", description="Chơi mini game để kiếm thêm vé game / phần thưởng")
async def game_command(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
        return

    try:
        # Đọc số vé đã tính hồi theo giờ (không phải giá trị cũ chưa cập nhật).
        user_data = await firebase.get_ticket_state(
            interaction.guild.id, interaction.user.id,
            level.TICKETS_MAX, level.TICKETS_REGEN_SECONDS, level.today_str(),
        )
    except firebase.FirebaseUnavailable:
        await interaction.response.send_message("❌ Không đọc được dữ liệu lúc này, thử lại sau nhé!", ephemeral=True)
        return

    tickets = user_data.get("tickets", 0)
    # Gửi CÔNG KHAI để cả kênh thấy ai đang chơi, nhưng nút chỉ chủ ván mới bấm được
    # (GameSelectView/_reject_if_not_owner đã chặn người khác ở tầng callback).
    await interaction.response.send_message(
        view=level.GameSelectView(interaction.user.id, tickets)
    )


# ==================== LỆNH /thú-tội ====================
@bot.tree.command(name="thú-tội", description="Gửi một lời thú tội ẩn danh — không ai biết bạn là người gửi")
@discord.app_commands.describe(nội_dung="Nội dung lời thú tội của bạn")
async def confession_command(interaction: discord.Interaction, nội_dung: str):
    channel = bot.get_channel(level.CONFESSION_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message(
            "❌ Không tìm thấy kênh thú tội, báo admin kiểm tra lại cấu hình nhé.", ephemeral=True
        )
        return

    if not nội_dung.strip():
        await interaction.response.send_message("❌ Nội dung thú tội không được để trống.", ephemeral=True)
        return

    # Phản hồi ephemeral ngay lập tức để không ai (kể cả log tương tác) có thể
    # suy ra danh tính người gửi từ độ trễ phản hồi công khai.
    await interaction.response.send_message(
        "✅ Thú tội của bạn đã được gửi ẩn danh!", ephemeral=True
    )

    try:
        confession_id = await firebase.generate_confession_id()
        confession_number = await firebase.next_confession_number()
    except firebase.FirebaseUnavailable:
        await interaction.followup.send(
            "⚠️ Có lỗi khi lưu thú tội, vui lòng thử lại sau.", ephemeral=True
        )
        return

    try:
        await channel.send(
            view=level.ConfessionView(nội_dung, confession_number, confession_id, time.time()),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        log.exception("Không gửi được thú tội ẩn danh vào kênh.")
        await interaction.followup.send(
            "⚠️ Có lỗi khi đăng thú tội lên kênh, vui lòng thử lại sau.", ephemeral=True
        )


# ==================== HỆ THỐNG DAILY ====================
async def _send_new_daily_container(channel: discord.TextChannel):
    msg = await channel.send(view=level.DailyClaimView())
    await firebase.save_daily_state({
        "message_id": msg.id,
        "date": level.today_str(),
        "message_count": 0,
    })


@tasks.loop(minutes=5)
async def daily_container_task():
    """
    Đảm bảo trong khung giờ mở daily (0:00 - 10:00) luôn có 1 container nhận
    daily hợp lệ trong kênh. Gửi container mới khi: sang ngày mới, chưa có
    container nào, hoặc kênh đã có quá 30 tin nhắn kể từ khi gửi container.
    """
    if not level.is_daily_open():
        return

    channel = bot.get_channel(level.DAILY_CHANNEL_ID)
    if channel is None:
        return

    try:
        state = await firebase.get_daily_state()
    except firebase.FirebaseUnavailable:
        log.warning("Không đọc được trạng thái daily (Firebase lỗi), bỏ qua lượt kiểm tra này.")
        return

    today = level.today_str()

    if state.get("date") != today or "message_id" not in state:
        await _send_new_daily_container(channel)
        return

    if state.get("message_count", 0) >= level.DAILY_MAX_MESSAGES_BEFORE_RESEND:
        await _send_new_daily_container(channel)


@daily_container_task.before_loop
async def before_daily_container_task():
    await bot.wait_until_ready()


# ==================== LỆNH /daily ====================
@bot.tree.command(name="daily", description="Nhận Deltan điểm danh hằng ngày — dùng được ở bất kỳ đâu")
async def daily_command(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
        return

    if not level.is_daily_open():
        await interaction.response.send_message(
            f"{level.ICON_CROSS} Daily chỉ mở từ {level.DAILY_OPEN_HOUR:02d}:00 đến "
            f"{level.DAILY_CLOSE_HOUR:02d}:00 sáng thôi nha!",
            ephemeral=True,
        )
        return

    try:
        result = await level.claim_daily(interaction.guild.id, interaction.user.id)
    except firebase.FirebaseUnavailable:
        await interaction.response.send_message(
            f"{level.ICON_WARNING} Không kết nối được dữ liệu lúc này, thử lại sau nhé!",
            ephemeral=True,
        )
        return

    if not result["ok"]:
        await interaction.response.send_message(
            f"{level.ICON_WARNING} Bạn đã nhận daily hôm nay rồi, quay lại vào ngày mai nhé!",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"{level.ICON_CHECK} Bạn nhận được **+{result['deltan_gained']} {level.ICON_DELTAN} Deltan**! "
        f"🔥 Streak hiện tại: **{result['streak']}** ngày.",
        ephemeral=True,
    )


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

    try:
        user_data = await firebase.get_user(guild.id, member.id)
        citizen_data = await firebase.get_citizen(guild.id, member.id)
    except firebase.FirebaseUnavailable:
        await interaction.followup.send("❌ Không đọc được dữ liệu lúc này, thử lại sau nhé!")
        return

    is_new = not citizen_data

    if is_new:
        try:
            await firebase.create_citizen(guild.id, member.id, level.generate_citizen_id())
            citizen_data = await firebase.get_citizen(guild.id, member.id)
        except firebase.FirebaseUnavailable:
            await interaction.followup.send("❌ Không tạo được hồ sơ công dân lúc này, thử lại sau nhé!")
            return

        role = await ensure_citizen_role(guild)
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="Tạo hồ sơ công dân qua /công-dân")
            except discord.Forbidden:
                pass

    await interaction.followup.send(view=level.CitizenView(member, user_data, citizen_data, is_new))


# ==================== XỬ LÝ LỖI CHUNG CHO SLASH COMMAND ====================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    log.exception(f"Lỗi khi xử lý lệnh /{interaction.command.name if interaction.command else '?'}: {error}")
    message = "❌ Có lỗi xảy ra khi thực hiện lệnh, vui lòng thử lại sau."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


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

    if not daily_container_task.is_running():
        daily_container_task.start()

    if not tiktok_sync_task.is_running():
        tiktok_sync_task.start()

    if not cooldown_cleanup_task.is_running():
        cooldown_cleanup_task.start()

    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="Tích Tốc")
    )


async def main():
    firebase.init_firebase()
    log.info("Đã kết nối Firebase Realtime Database.")
    bot.add_view(level.DailyClaimView())  # để nút "Nhận Daily" hoạt động sau khi bot restart

    # discord.py không cho start() lại trên cùng 1 bot instance sau khi nó đã
    # đóng (session bị close), nên không retry bằng cách gọi lại start() nhiều lần.
    # Thay vào đó: nếu bị 429 ngay ở lần thử đầu, đợi đúng retry_after Discord yêu
    # cầu rồi để tiến trình thoát - Render sẽ tự khởi động lại tiến trình mới,
    # lúc đó session hoàn toàn mới nên không còn vướng lỗi "Session is closed".
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    except discord.HTTPException as e:
        if e.status == 429:
            retry_after = getattr(e, "retry_after", None) or 60
            log.warning(
                f"Bị Discord rate-limit (429) khi login. "
                f"Chờ {retry_after:.0f}s rồi thoát để Render tự khởi động lại tiến trình mới..."
            )
            await asyncio.sleep(retry_after)
            raise SystemExit(1)
        raise


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Thiếu DISCORD_TOKEN trong biến môi trường (.env)")
    if not os.getenv("FIREBASE_DB_URL"):
        raise SystemExit("Thiếu FIREBASE_DB_URL trong biến môi trường (.env)")

    keep_alive()
    asyncio.run(main())
