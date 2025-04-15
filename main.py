python
import discord
from discord.ext import commands
import yt_dlp
import random
import asyncio
import os
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import logging
import datetime
import time
import openai
import re
import json
from typing import Optional
import lyricsgenius
from functools import lru_cache
import logging.handlers
from asyncio import TimeoutError

# Cấu hình logging
logger = logging.getLogger("discord")
logger.setLevel(logging.INFO)
file_handler = logging.handlers.RotatingFileHandler(
    filename="hinaa_bot.log",
    encoding="utf-8",
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=5,
)
file_handler.setFormatter(logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s"))
logger.addHandler(file_handler)
logging.getLogger("yt_dlp").setLevel(logging.WARNING)

# Tải biến môi trường
load_dotenv()

# Cấu hình intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Cấu hình biến môi trường
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GENIUS_API_TOKEN = os.getenv("GENIUS_API_TOKEN")

if not DISCORD_BOT_TOKEN:
    logger.error("DISCORD_BOT_TOKEN không được cấu hình!")
    exit(1)

if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    logger.warning("OPENAI_API_KEY không được cấu hình. Tính năng AI bị giới hạn.")

if GENIUS_API_TOKEN:
    genius = lyricsgenius.Genius(GENIUS_API_TOKEN)
else:
    logger.warning("GENIUS_API_TOKEN không được cấu hình. Tính năng lời bài hát bị giới hạn.")

# Kết nối Spotify
try:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET))
    logger.info("Kết nối thành công với Spotify API")
except Exception as e:
    logger.error(f"Lỗi khi kết nối Spotify: {e}")
    sp = None

# Biến toàn cục
queues = {}
current_song = {}
autoplay_enabled = {}
votes_to_skip = {}
playlists = {}
guess_lol_scores = {}

# Cấu hình AI chat
ai_config = {
    "enabled": True,
    "cooldown": 5,
    "last_chat": {},
}

# Danh sách chuyện cười
jokes = [
    "Sao Ashe chia tay Tryndamere vậy? Vì anh ấy cứ combat mà không để ý cô ấy! 😅",
    "Tướng nào hay bị bắt lẻ nhất? Ezreal, cứ tưởng mình là siêu sao! 😎",
    "Sao Yasuo không bao giờ hết gió? Vì fan của anh ấy thổi suốt ngày! 🌪️",
]

class AIManager:
    def __init__(self):
        self.personality = """
        Bạn là Hinaa, một bot siêu dễ thương, hòa đồng, yêu nhạc và thích Liên Minh Huyền Thoại.
        Trả lời ngắn, vui tươi, dùng emoji như 😊🎶💖, và luôn tích cực!
        Hiểu ý định từ tin nhắn để phát nhạc, kể chuyện, hoặc trò chuyện.
        """

    async def analyze_intent(self, message: str, guild_id: int) -> dict:
        if not OPENAI_API_KEY:
            return {"intent": "chat", "response": None, "url": None}
        try:
            prompt = f"""
            {self.personality}
            Tin nhắn: "{message}"
            Ý định:
            - play_music: phát nhạc (trích xuất URL nếu có).
            - tell_joke: kể chuyện cười.
            - chat: trò chuyện thông thường.
            Trả về JSON: {{"intent": "tên_ý_định", "response": "phản_hồi_nếu_có", "url": "URL_nếu_có_hoặc_null"}}
            """
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "system", "content": prompt}, {"role": "user", "content": message}],
                    max_tokens=150,
                    temperature=0.7,
                ),
            )
            response_content = response.choices[0].message.content.strip()
            return json.loads(response_content)
        except (json.JSONDecodeError, KeyError) as e:
            logger.exception(f"Lỗi JSON từ OpenAI: {e}")
            return {"intent": "chat", "response": "Hinaa hơi rối! Bạn nói lại nha! 😅", "url": None}
        except Exception as e:
            logger.exception(f"Lỗi phân tích ý định: {e}")
            return {"intent": "chat", "response": None, "url": None}

    def can_chat(self, guild_id: int) -> bool:
        current_time = time.time()
        return current_time - ai_config["last_chat"].get(guild_id, 0) >= ai_config["cooldown"]

    def update_chat_time(self, guild_id: int):
        ai_config["last_chat"][guild_id] = time.time()

    async def get_random_song(self) -> Optional[str]:
        if sp:
            try:
                playlist = sp.playlist("37i9dQZF1DXcBWIGoYBM5M", market="VN")
                tracks = playlist["tracks"]["items"]
                track = random.choice(tracks)["track"]
                return track["external_urls"]["spotify"]
            except Exception as e:
                logger.exception(f"Lỗi lấy bài hát ngẫu nhiên: {e}")
        return None

ai_manager = AIManager()

class MusicControls(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.paused = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.ctx.voice_client:
            await interaction.response.send_message("Bot chưa ở trong voice chat!", ephemeral=True)
            return False
        perms = self.ctx.voice_client.channel.permissions_for(self.ctx.guild.me)
        if not (perms.speak and perms.connect):
            await interaction.response.send_message("Bot không có quyền phát âm thanh hoặc kết nối!", ephemeral=True)
            return False
        return interaction.user.guild_permissions.administrator or interaction.user == self.ctx.author

    @discord.ui.button(label="⏸️", style=discord.ButtonStyle.blurple, custom_id="pause_resume")
    async def toggle_pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not self.ctx.voice_client:
            await interaction.followup.send("🎵 Bot chưa ở trong voice chat! 😅", ephemeral=True)
            return
        if self.ctx.voice_client.is_playing() and not self.paused:
            self.ctx.voice_client.pause()
            self.paused = True
            button.label = "▶️"
            button.style = discord.ButtonStyle.green
            await interaction.message.edit(view=self)
            await interaction.followup.send("🎶 Nhạc tạm dừng rồi nha! 😊", ephemeral=True)
        elif self.ctx.voice_client.is_paused() and self.paused:
            self.ctx.voice_client.resume()
            self.paused = False
            button.label = "⏸️"
            button.style = discord.ButtonStyle.blurple
            await interaction.message.edit(view=self)
            await interaction.followup.send("🎶 Hát tiếp nào! 💖", ephemeral=True)
        else:
            await interaction.followup.send("🎵 Không có nhạc đang phát! 😅", ephemeral=True)

    @discord.ui.button(label="⏭️ Bài Tiếp", style=discord.ButtonStyle.grey)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.ctx.voice_client and (self.ctx.voice_client.is_playing() or self.ctx.voice_client.is_paused()):
            self.ctx.voice_client.stop()
            await interaction.followup.send("🎶 Chuyển bài tiếp theo! 🎵", ephemeral=True)
            await play_next(self.ctx)
        else:
            await interaction.followup.send("🎵 Không có nhạc để chuyển! 😊", ephemeral=True)

    @discord.ui.button(label="🛑 Dừng", style=discord.ButtonStyle.red)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        server_id = self.ctx.guild.id
        if self.ctx.voice_client and (self.ctx.voice_client.is_playing() or self.ctx.voice_client.is_paused()):
            self.ctx.voice_client.stop()
            current_song.pop(server_id, None)
            await interaction.followup.send("🎶 Nhạc đã dừng! 😊", ephemeral=True)
        else:
            await interaction.followup.send("🎵 Chưa có nhạc đang phát! 😅", ephemeral=True)

    @discord.ui.select(placeholder="⚙️ Quản lý", options=[
        discord.SelectOption(label="🚪 Thoát Voice Chat", value="leave"),
        discord.SelectOption(label="🗑️ Xóa Hàng Đợi", value="clear_queue"),
        discord.SelectOption(label="🔀 Xáo Trộn", value="shuffle"),
        discord.SelectOption(label="🔄 Tự Phát", value="autoplay"),
    ])
    async def select_menu(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        server_id = self.ctx.guild.id
        if select.values[0] == "leave":
            if self.ctx.voice_client:
                await self.ctx.voice_client.disconnect()
                queues.pop(server_id, None)
                current_song.pop(server_id, None)
                await interaction.followup.send("👋 Hinaa rời kênh rồi! 😊", ephemeral=True)
            else:
                await interaction.followup.send("🎵 Bot chưa ở trong voice chat! 😅", ephemeral=True)
        elif select.values[0] == "clear_queue":
            if server_id in queues and queues[server_id]:
                queues[server_id].clear()
                await interaction.followup.send("🗑️ Hàng đợi đã được xóa! 🎵", ephemeral=True)
            else:
                await interaction.followup.send("🎵 Hàng đợi đã trống rồi! 😊", ephemeral=True)
        elif select.values[0] == "shuffle":
            if server_id in queues and queues[server_id]:
                random.shuffle(queues[server_id])
                await interaction.followup.send("🎶 Xáo trộn xong! 🎵", ephemeral=True)
            else:
                await interaction.followup.send("🎵 Hàng đợi trống, không có gì để xáo! 😊", ephemeral=True)
        elif select.values[0] == "autoplay":
            autoplay_enabled[server_id] = not autoplay_enabled.get(server_id, False)
            state = "bật" if autoplay_enabled[server_id] else "tắt"
            await interaction.followup.send(f"🎶 Tự phát đã {state}! 😊", ephemeral=True)

def create_progress_bar(current, total):
    if total == 0:
        return "🔘────────── 0%"
    progress = min(int((current / total) * 10), 10)
    bar = "🔘" + "─" * progress + "─" * (10 - progress)
    percentage = min(int((current / total) * 100), 100)
    return f"{bar} {percentage}%"

async def update_progress(ctx, message, duration, start_time):
    while ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        if elapsed >= duration:
            break
        embed = message.embeds[0]
        embed.set_field_at(1, name="📊 𝗧𝗶ế𝗻 𝗧𝗿ì𝗻𝗵", value=create_progress_bar(elapsed, duration), inline=False)
        try:
            await message.edit(embed=embed)
        except discord.errors.HTTPException:
            break
        await asyncio.sleep(5)

async def fetch_song_info_async(url: str, is_search: bool = False) -> Optional[dict]:
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "simulate": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    if is_search:
        ydl_opts["default_search"] = "ytsearch5"
    try:
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False)),
                timeout=10.0
            )
            if not info:
                logger.warning(f"Không lấy được thông tin từ URL: {url}")
                return None
            if is_search and "entries" in info:
                for entry in info["entries"]:
                    if entry and entry.get("url"):
                        return {
                            "url": entry["url"],
                            "title": entry.get("title", "Unknown Title"),
                            "artist": entry.get("uploader", "Unknown Artist"),
                            "duration": entry.get("duration", 0),
                            "thumbnail": entry.get("thumbnail", "https://i.imgur.com/5z1oX0Z.png"),
                        }
                return None
            return {
                "url": info["url"],
                "title": info.get("title", "Unknown Title"),
                "artist": info.get("uploader", "Unknown Artist"),
                "duration": info.get("duration", 0),
                "thumbnail": info.get("thumbnail", "https://i.imgur.com/5z1oX0Z.png"),
            }
    except asyncio.TimeoutError:
        logger.exception(f"Timeout khi tải thông tin bài hát: {url}")
        return None
    except Exception as e:
        logger.exception(f"Lỗi khi tải thông tin bài hát: {e}")
        return None

async def is_valid_url(url: str) -> bool:
    return (
        "youtube.com" in url or 
        "youtu.be" in url or 
        "spotify.com" in url or 
        "soundcloud.com" in url
    )

async def handle_spotify(ctx, url: str) -> dict:
    if not sp:
        raise ValueError("Spotify API chưa kết nối!")
    try:
        if "track" in url:
            track = sp.track(url, market="VN")
            return {
                "title": track["name"],
                "artist": track["artists"][0]["name"],
                "search_query": f"{track['name']} {track['artists'][0]['name']} audio",
            }
        elif "playlist" in url:
            playlist = sp.playlist(url, market="VN")
            tracks = playlist["tracks"]["items"]
            server_id = ctx.guild.id
            if server_id not in queues:
                queues[server_id] = []
            valid_tracks = 0
            for track_item in tracks[:50]:  # Giới hạn 50 bài
                track = track_item["track"]
                track_url = track["external_urls"]["spotify"]
                if await is_valid_url(track_url):
                    queues[server_id].append(track_url)
                    valid_tracks += 1
            return {"is_playlist": True, "count": valid_tracks}
        else:
            raise ValueError("Chỉ hỗ trợ track/playlist Spotify!")
    except Exception as e:
        logger.exception(f"Lỗi khi xử lý Spotify: {e}")
        raise ValueError("Lỗi khi xử lý Spotify, thử lại nhé!")

async def play_source(ctx, song_info: dict, url: str):
    server_id = ctx.guild.id
    start_time = datetime.datetime.now()
    current_song[server_id] = {
        "title": song_info["title"],
        "artist": song_info["artist"],
        "url": url,
        "duration": song_info["duration"],
        "start_time": start_time,
        "thumbnail": song_info["thumbnail"],
    }
    votes_to_skip[server_id] = set()
    try:
        source = discord.FFmpegPCMAudio(
            song_info["url"],
            executable=FFMPEG_PATH,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        )
        embed = discord.Embed(title="🎵 𝗛𝗶𝗻𝗮𝗮'𝘀 𝗠𝘂𝘀𝗶𝗰 𝗣𝗹𝗮𝘆𝗲𝗿", color=discord.Color.from_rgb(255, 182, 193))
        embed.add_field(name="🎶 𝗕à𝗶 𝗛á𝘁", value=f"**{song_info['title']}**", inline=False)
        embed.add_field(name="📊 𝗧𝗶ế𝗻 𝗧𝗿ì𝗻𝗵", value=create_progress_bar(0, song_info["duration"]), inline=False)
        embed.add_field(name="🎤 𝗡𝗴𝗵ệ 𝗦ĩ", value=f"**{song_info['artist']}**", inline=False)
        embed.set_image(url=song_info["thumbnail"])
        embed.set_footer(text="✨ Hinaa luôn sẵn sàng nè! ✨")
        view = MusicControls(ctx)
        message = await ctx.send(embed=embed, view=view)
        logger.info(f"Phát bài: {song_info['title']} - {song_info['artist']}")
        ctx.voice_client.play(source, after=lambda e: bot.loop.create_task(play_next(ctx)))
        asyncio.create_task(update_progress(ctx, message, song_info["duration"], start_time))
    except Exception as e:
        logger.exception(f"Lỗi khi phát âm thanh: {e}")
        embed = discord.Embed(description="🚫 Không thể phát bài hát này, thử bài khác nhé! 😅", color=discord.Color.red())
        await ctx.send(embed=embed)
        current_song.pop(server_id, None)
        await play_next(ctx)

async def play_music(ctx, url: str):
    try:
        server_id = ctx.guild.id
        if not ctx.author.voice:
            embed = discord.Embed(description="🚫 Bạn cần vào kênh voice trước nha! 😊", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        if not ctx.voice_client:
            perms = ctx.author.voice.channel.permissions_for(ctx.guild.me)
            if not (perms.connect and perms.speak):
                embed = discord.Embed(description="🚫 Bot không có quyền kết nối hoặc phát âm thanh! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            await ctx.author.voice.channel.connect()
        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            if await is_valid_url(url):
                if server_id not in queues:
                    queues[server_id] = []
                queues[server_id].append(url)
                embed = discord.Embed(description=f"🎶 Thêm bài vào hàng đợi! 😊", color=discord.Color.blue())
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(description="🚫 URL không hợp lệ, chỉ hỗ trợ YouTube, Spotify, SoundCloud! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
            return
        if "spotify.com" in url:
            spotify_data = await handle_spotify(ctx, url)
            if spotify_data.get("is_playlist"):
                embed = discord.Embed(
                    description=f"🎶 Thêm **{spotify_data['count']} bài** từ playlist Spotify! 😊",
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed)
                if not ctx.voice_client.is_playing() and server_id in queues and queues[server_id]:
                    next_url = queues[server_id].pop(0)
                    await play_music(ctx, next_url)
                return
            song_info = await fetch_song_info_async(spotify_data["search_query"], is_search=True)
        elif "youtube.com/playlist" in url:
            ydl_opts = {"extract_flat": True, "quiet": True, "ignoreerrors": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=False)),
                    timeout=10.0
                )
            if server_id not in queues:
                queues[server_id] = []
            valid_entries = 0
            for entry in info.get("entries", [])[:50]:  # Giới hạn 50 bài
                if entry and entry.get("url") and await is_valid_url(entry["url"]):
                    queues[server_id].append(entry["url"])
                    valid_entries += 1
            embed = discord.Embed(description=f"🎶 Thêm **{valid_entries} bài** từ playlist YouTube! 😊", color=discord.Color.blue())
            await ctx.send(embed=embed)
            if not ctx.voice_client.is_playing() and server_id in queues and queues[server_id]:
                next_url = queues[server_id].pop(0)
                await play_music(ctx, next_url)
            return
        elif "soundcloud.com" in url:
            song_info = await fetch_song_info_async(url)
        else:
            song_info = await fetch_song_info_async(url)
        if not song_info:
            embed = discord.Embed(description="🚫 Bài hát này không khả dụng, thử bài khác nhé! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        await play_source(ctx, song_info, url)
    except discord.errors.ClientException:
        logger.exception("Bot chưa vào voice chat")
        embed = discord.Embed(description="🚫 Bot chưa vào voice chat, dùng !join nha! 😊", color=discord.Color.red())
        await ctx.send(embed=embed)
    except ValueError as e:
        embed = discord.Embed(description=f"🚫 {str(e)} 😅", color=discord.Color.red())
        await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi phát nhạc: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

async def play_next(ctx):
    server_id = ctx.guild.id
    if server_id in queues and queues[server_id]:
        for _ in range(min(3, len(queues[server_id]))):  # Thử tối đa 3 bài
            url = queues[server_id].pop(0)
            try:
                song_info = await fetch_song_info_async(url)
                if song_info:
                    await play_source(ctx, song_info, url)
                    return
                logger.warning(f"Bỏ qua URL không khả dụng: {url}")
            except Exception as e:
                logger.exception(f"Lỗi khi phát bài tiếp theo: {e}")
        embed = discord.Embed(description="🚫 Không tìm được bài khả dụng trong hàng đợi, thử thêm bài mới nhé! 😅", color=discord.Color.red())
        await ctx.send(embed=embed)
    elif autoplay_enabled.get(server_id, False):
        for _ in range(3):  # Thử tối đa 3 lần
            url = await ai_manager.get_random_song()
            if url and await is_valid_url(url):
                try:
                    song_info = await fetch_song_info_async(url)
                    if song_info:
                        await play_source(ctx, song_info, url)
                        return
                    logger.warning(f"Bỏ qua URL không khả dụng: {url}")
                except Exception as e:
                    logger.exception(f"Lỗi khi phát bài ngẫu nhiên: {e}")
        embed = discord.Embed(description="🚫 Không tìm được bài ngẫu nhiên khả dụng, thử lại nhé! 😅", color=discord.Color.red())
        await ctx.send(embed=embed)
    elif ctx.voice_client:
        current_song.pop(server_id, None)
        embed = discord.Embed(description="🎶 Hàng đợi hết rồi! Thêm bài mới nha! 😊", color=discord.Color.blue())
        await ctx.send(embed=embed)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not ai_config["enabled"]:
        await bot.process_commands(message)
        return
    server_id = message.guild.id
    hinaa_pattern = re.compile(r"\bhinaa?\b", re.IGNORECASE)
    is_hinaa_mentioned = hinaa_pattern.search(message.content) is not None
    if ai_manager.can_chat(server_id):
        if is_hinaa_mentioned or bot.user.mentioned_in(message):
            clean_content = re.sub(r"<@!?\d+>|\bhinaa?\b", "", message.content, flags=re.IGNORECASE).strip()
            intent_data = await ai_manager.analyze_intent(clean_content or "hinaa", server_id)
            intent = intent_data["intent"]
            response = intent_data["response"]
            url = intent_data["url"]
            if intent == "play_music":
                url = url or await ai_manager.get_random_song()
                if url:
                    await play_music(message.channel, url)
            elif intent == "tell_joke":
                joke = random.choice(jokes)
                embed = discord.Embed(description=f"😄 {joke}", color=discord.Color.blue())
                await message.channel.send(embed=embed)
            else:
                await message.channel.send(response or "🎶 Hinaa nghe bạn gọi! Có gì vui kể nghe nha! 😊")
            ai_manager.update_chat_time(server_id)
    await bot.process_commands(message)

@bot.event
async def on_ready():
    logger.info(f"Hinaa đã sẵn sàng với tên {bot.user}")
    load_playlists()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="nhạc cùng mọi người! 🎶"))

@bot.event
async def on_guild_remove(guild):
    server_id = guild.id
    queues.pop(server_id, None)
    current_song.pop(server_id, None)
    autoplay_enabled.pop(server_id, None)
    votes_to_skip.pop(server_id, None)
    guess_lol_scores.pop(server_id, None)
    if server_id in bot.voice_clients:
        for vc in bot.voice_clients:
            if vc.guild.id == server_id:
                await vc.disconnect(force=True)
    logger.info(f"Đã xóa dữ liệu và ngắt kết nối voice của server {server_id}")

@bot.command()
async def join(ctx):
    try:
        if not ctx.author.voice:
            embed = discord.Embed(description="🚫 Bạn cần vào kênh voice trước nha! 😊", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        channel = ctx.author.voice.channel
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.connect and perms.speak):
            embed = discord.Embed(description="🚫 Bot không có quyền kết nối hoặc phát âm thanh! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        if ctx.voice_client:
            await ctx.voice_client.move_to(channel)
        else:
            await channel.connect()
        embed = discord.Embed(description=f"🎉 Hinaa vào **{channel.name}** rồi! 😊", color=discord.Color.blue())
        await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi join: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def leave(ctx):
    try:
        if not ctx.voice_client:
            embed = discord.Embed(description="🚫 Hinaa chưa vào kênh voice! 😊", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        server_id = ctx.guild.id
        queues.pop(server_id, None)
        current_song.pop(server_id, None)
        await ctx.voice_client.disconnect(force=True)
        embed = discord.Embed(description="👋 Hinaa rời kênh rồi! Hẹn gặp lại nha! 😊", color=discord.Color.blue())
        await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi leave: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def play(ctx, url: str):
    try:
        await play_music(ctx, url)
    except Exception as e:
        logger.exception(f"Lỗi khi chạy play: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def search(ctx, *, query):
    try:
        song_info = await fetch_song_info_async(query, is_search=True)
        if not song_info:
            embed = discord.Embed(description="🚫 Không tìm thấy bài hát nào, thử từ khóa khác nhé! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        await play_music(ctx, song_info["url"])
    except ValueError as e:
        embed = discord.Embed(description=f"🚫 {str(e)} 😅", color=discord.Color.red())
        await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi tìm kiếm: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def queue(ctx, url: str):
    try:
        server_id = ctx.guild.id
        if not await is_valid_url(url):
            embed = discord.Embed(description="🚫 URL không hợp lệ, chỉ hỗ trợ YouTube, Spotify, SoundCloud! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        song_info = await fetch_song_info_async(url)
        if not song_info:
            embed = discord.Embed(description="🚫 Bài hát này không khả dụng, thử bài khác nhé! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        if server_id not in queues:
            queues[server_id] = []
        if len(queues[server_id]) >= 50:
            embed = discord.Embed(description="🚫 Hàng đợi đã đầy (tối đa 50 bài)! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        queues[server_id].append((url, song_info["title"], song_info["artist"]))
        embed = discord.Embed(description=f"🎶 Thêm **{song_info['title']}** vào hàng đợi! 😊", color=discord.Color.blue())
        await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi thêm vào hàng đợi: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def queue_list(ctx):
    try:
        server_id = ctx.guild.id
        if server_id not in queues or not queues[server_id]:
            embed = discord.Embed(description="🎵 𝗛à𝗻𝗴 Đợ𝗶 𝗧𝗿ố𝗻𝗴! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        queue_list = [f"**{i+1}.** {title} - {artist}" for i, (_, title, artist) in enumerate(queues[server_id][:10])]
        description = "\n".join(queue_list)
        embed = discord.Embed(
            title="📜 𝗗𝗮𝗻𝗵 𝗦á𝗰𝗵 𝗛à𝗻𝗴 Đợ𝗶",
            description=description,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"✨ Tổng cộng: {len(queues[server_id])} bài ✨")
        await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi hiển thị hàng đợi: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def skip(ctx):
    try:
        if not ctx.voice_client:
            embed = discord.Embed(description="🚫 Bot chưa ở trong voice chat! 😊", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        server_id = ctx.guild.id
        votes_to_skip[server_id].add(ctx.author.id)
        required = max(1, len(ctx.voice_client.channel.members) // 2)
        if len(votes_to_skip[server_id]) >= required:
            if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                ctx.voice_client.stop()
                embed = discord.Embed(description="🎶 Đủ vote, Hinaa skip bài này! 😊", color=discord.Color.blue())
                votes_to_skip[server_id].clear()
            else:
                embed = discord.Embed(description="🎵 Không có nhạc để skip! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(description=f"🎶 Cần {required - len(votes_to_skip[server_id])} vote nữa để skip! 😊", color=discord.Color.blue())
            await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi skip: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def volume(ctx, level: int):
    try:
        if not ctx.voice_client:
            embed = discord.Embed(description="🚫 Bot chưa ở trong voice chat! 😊", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        if 0 <= level <= 100:
            ctx.voice_client.source.volume = level / 100
            embed = discord.Embed(description=f"🔊 Âm lượng: **{level}%**!", color=discord.Color.blue())
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(description="🚫 Âm lượng phải từ 0 đến 100! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi điều chỉnh volume: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@lru_cache(maxsize=100)
def get_lyrics(title: str, artist: str) -> str:
    try:
        song = genius.search_song(title, artist)
        return song.lyrics if song else "Không tìm thấy lời bài hát! 😅"
    except Exception as e:
        logger.exception(f"Lỗi khi lấy lời bài hát: {e}")
        return "Lỗi khi lấy lời bài hát, thử lại nhé! 😅"

async def clear_lyrics_cache():
    while True:
        await asyncio.sleep(3600)
        get_lyrics.cache_clear()
        logger.info("Đã xóa cache lời bài hát")

async def send_paginated_lyrics(ctx, lyrics: str):
    max_length = 1000
    pages = [lyrics[i:i + max_length] for i in range(0, len(lyrics), max_length)]
    current_page = 0
    embed = discord.Embed(
        title="🎤 �_K𝗮𝗿𝗮𝗼𝗸𝗲 𝗧𝗶𝗺𝗲!",
        description=pages[current_page][:1000],
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"✨ Trang {current_page + 1}/{len(pages)} ✨")
    message = await ctx.send(embed=embed)
    if len(pages) > 1:
        await message.add_reaction("⬅️")
        await message.add_reaction("➡️")
        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["⬅️", "➡️"] and reaction.message.id == message.id
        while True:
            try:
                reaction, user = await bot.wait_for("reaction_add", timeout=60.0, check=check)
                if str(reaction.emoji) == "➡️" and current_page < len(pages) - 1:
                    current_page += 1
                elif str(reaction.emoji) == "⬅️" and current_page > 0:
                    current_page -= 1
                else:
                    continue
                embed.description = pages[current_page][:1000]
                embed.set_footer(text=f"✨ Trang {current_page + 1}/{len(pages)} ✨")
                await message.edit(embed=embed)
                await message.remove_reaction(reaction.emoji, user)
            except asyncio.TimeoutError:
                await message.clear_reactions()
                break

@bot.command()
async def karaoke(ctx, url: str):
    try:
        if not ctx.voice_client:
            embed = discord.Embed(description="🚫 Bot chưa ở trong voice chat, dùng !join nha! 😊", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        song_info = await fetch_song_info_async(url)
        if not song_info:
            embed = discord.Embed(description="🚫 Bài hát này không khả dụng, thử bài khác nhé! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        lyrics = get_lyrics(song_info["title"], song_info["artist"])
        await send_paginated_lyrics(ctx, lyrics)
        await play_music(ctx, url)
    except ValueError as e:
        embed = discord.Embed(description=f"🚫 {str(e)} 😅", color=discord.Color.red())
        await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi chạy karaoke: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def np(ctx):
    try:
        server_id = ctx.guild.id
        if server_id not in current_song:
            embed = discord.Embed(description="🎵 𝗖𝗵ư𝗮 𝗖ó 𝗕à𝗶 𝗛á𝘁 𝗡à𝗼 Đ𝗮𝗻𝗴 𝗣𝗵á𝘁! 😅", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        song = current_song[server_id]
        elapsed = (datetime.datetime.now() - song["start_time"]).total_seconds()
        embed = discord.Embed(title="🎵 𝗡𝗼𝘄 𝗣𝗹𝗮𝘆𝗶𝗻𝗴", color=discord.Color.blue())
        embed.add_field(name="🎶 𝗕à𝗶 𝗛á𝘁", value=f"**{song['title']}**", inline=False)
        embed.add_field(name="🎤 𝗡𝗴𝗵ệ 𝗦ĩ", value=f"**{song['artist']}**", inline=False)
        embed.add_field(
            name="📊 𝗧𝗶ế𝗻 𝗧𝗿ì𝗻𝗵",
            value=create_progress_bar(elapsed, song["duration"]),
            inline=False
        )
        embed.set_image(url=song["thumbnail"])
        embed.set_footer(text="✨ Hinaa luôn sẵn sàng nè! ✨")
        await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi hiển thị now playing: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def guess_lol(ctx):
    try:
        server_id = ctx.guild.id
        if server_id not in guess_lol_scores:
            guess_lol_scores[server_id] = {}
        champions = [
            {"name": "Yasuo", "hint": "Tay kiếm bậc thầy, gió là bạn, có tornado"},
            {"name": "Lux", "hint": "Cô gái ánh sáng, bắn laser, luôn vui vẻ"},
            {"name": "Garen", "hint": "Kiếm to, xoay tít, hét to nhất Demacia"},
        ]
        champion = random.choice(champions)
        embed = discord.Embed(
            title="🎮 𝗚𝗮𝗺𝗲 Đ𝗼á𝗻 𝗧ướ𝗻𝗴",
            description=f"**Gợi ý:** {champion['hint']}\n📢 Ai trả lời đúng đầu tiên sẽ được điểm!\n⏰ Thời gian: 15 giây!",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
        def check(m):
            return m.channel == ctx.channel and not m.author.bot
        try:
            response = await bot.wait_for("message", check=check, timeout=15.0)
            if response.content.lower() == champion["name"].lower():
                user_id = str(response.author.id)
                guess_lol_scores[server_id][user_id] = guess_lol_scores[server_id].get(user_id, 0) + 1
                embed = discord.Embed(
                    title="🎉 𝗖𝗵ú𝗰 𝗠ừ𝗻𝗴!",
                    description=f"**{response.author.display_name}** đoán đúng tướng **{champion['name']}**!\n📊 Điểm hiện tại: **{guess_lol_scores[server_id][user_id]}**",
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    description=f"😅 Sai rồi! Tướng đúng là **{champion['name']}**. Chơi tiếp nha! 🎮",
                    color=discord.Color.red()
                )
            await ctx.send(embed=embed)
        except asyncio.TimeoutError:
            embed = discord.Embed(
                description=f"⏰ Hết giờ! Tướng đúng là **{champion['name']}**. Chơi lại nha! 😊",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        if guess_lol_scores[server_id]:
            leaderboard = "\n".join(
                f"**{ctx.guild.get_member(int(uid)).display_name if ctx.guild.get_member(int(uid)) else 'Unknown User'}**: {score}"
                for uid, score in sorted(guess_lol_scores[server_id].items(), key=lambda x: x[1], reverse=True)[:5]
            )
            embed = discord.Embed(
                title="🏆 𝗕ả𝗻𝗴 𝗫ế𝗽 𝗛𝗮𝗻𝗴",
                description=leaderboard or "Chưa có điểm nào!",
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi chơi guess_lol: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

def save_playlists():
    try:
        with open("playlists.json", "w", encoding="utf-8") as f:
            json.dump(playlists, f, ensure_ascii=False, indent=2)
        logger.info("Đã lưu playlist vào playlists.json")
    except Exception as e:
        logger.exception(f"Lỗi khi lưu playlist: {e}")

def load_playlists():
    global playlists
    try:
        with open("playlists.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                playlists.update({str(k): v for k, v in data.items()})
                logger.info("Đã tải playlist từ playlists.json")
            else:
                logger.warning("File playlists.json không đúng định dạng, khởi tạo playlist rỗng")
                playlists = {}
    except FileNotFoundError:
        logger.info("Không tìm thấy playlists.json, khởi tạo playlist rỗng")
        playlists = {}
    except json.JSONDecodeError:
        logger.warning("File playlists.json bị hỏng, khởi tạo playlist rỗng")
        playlists = {}
    except Exception as e:
        logger.exception(f"Lỗi khi tải playlist: {e}")
        playlists = {}

@bot.command()
async def playlist(ctx, action: str, name: str = None, url: str = None):
    user_id = str(ctx.author.id)
    try:
        if action == "create" and name:
            if user_id not in playlists:
                playlists[user_id] = {}
            if name in playlists[user_id]:
                embed = discord.Embed(description="🚫 Playlist này đã tồn tại! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            playlists[user_id][name] = []
            save_playlists()
            embed = discord.Embed(description=f"🎶 Tạo playlist **{name}** thành công! 😊", color=discord.Color.blue())
            await ctx.send(embed=embed)
        elif action == "add" and name and url:
            if user_id not in playlists or name not in playlists[user_id]:
                embed = discord.Embed(description="🚫 Playlist không tồn tại! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            if not await is_valid_url(url):
                embed = discord.Embed(description="🚫 URL không hợp lệ, chỉ hỗ trợ YouTube, Spotify, SoundCloud! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            song_info = await fetch_song_info_async(url)
            if not song_info:
                embed = discord.Embed(description="🚫 Bài hát này không khả dụng, thử bài khác nhé! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            playlists[user_id][name].append(url)
            save_playlists()
            embed = discord.Embed(description=f"🎶 Thêm **{song_info['title']}** vào **{name}**! 😊", color=discord.Color.blue())
            await ctx.send(embed=embed)
        elif action == "remove" and name and url:
            if user_id not in playlists or name not in playlists[user_id]:
                embed = discord.Embed(description="🚫 Playlist không tồn tại! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            if url not in playlists[user_id][name]:
                embed = discord.Embed(description="🚫 Bài hát không có trong playlist! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            playlists[user_id][name].remove(url)
            save_playlists()
            embed = discord.Embed(description=f"🎶 Xóa bài khỏi **{name}**! 😊", color=discord.Color.blue())
            await ctx.send(embed=embed)
        elif action == "play" and name:
            if user_id not in playlists or name not in playlists[user_id] or not playlists[user_id][name]:
                embed = discord.Embed(description="🚫 Playlist trống hoặc không tồn tại! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            server_id = ctx.guild.id
            if server_id not in queues:
                queues[server_id] = []
            valid_urls = 0
            for url in playlists[user_id][name]:
                if await is_valid_url(url):
                    song_info = await fetch_song_info_async(url)
                    if song_info:
                        queues[server_id].append((url, song_info["title"], song_info["artist"]))
                        valid_urls += 1
            embed = discord.Embed(
                description=f"🎶 Thêm **{valid_urls} bài** từ **{name}** vào hàng đợi! 😊",
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)
            if not ctx.voice_client or not (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
                if server_id in queues and queues[server_id]:
                    next_url, _, _ = queues[server_id].pop(0)
                    await play_music(ctx, next_url)
        elif action == "list":
            if user_id not in playlists or not playlists[user_id]:
                embed = discord.Embed(description="🎵 Bạn chưa có playlist nào! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            playlist_list = "\n".join(
                f"**{pname}**: {len(songs)} bài"
                for pname, songs in playlists[user_id].items()
            )
            embed = discord.Embed(
                title="📜 𝗗𝗮𝗻𝗵 𝗦á𝗰𝗵 𝗣𝗹𝗮𝘆𝗹𝗶𝘀𝘁",
                description=playlist_list,
                color=discord.Color.blue()
            )
            embed.set_footer(text="✨ Dùng !playlist để quản lý nha! ✨")
            await ctx.send(embed=embed)
        elif action == "delete" and name:
            if user_id not in playlists or name not in playlists[user_id]:
                embed = discord.Embed(description="🚫 Playlist không tồn tại! 😅", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
            del playlists[user_id][name]
            if not playlists[user_id]:
                del playlists[user_id]
            save_playlists()
            embed = discord.Embed(description=f"🎶 Xóa playlist **{name}** thành công! 😊", color=discord.Color.blue())
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="📋 𝗖á𝗰𝗵 𝗗ù𝗻𝗴 𝗣𝗹𝗮𝘆𝗹𝗶𝘀𝘁",
                description=(
                    "❓ **Lệnh hỗ trợ:**\n"
                    "`!playlist create <tên>`: Tạo playlist mới\n"
                    "`!playlist add <tên> <url>`: Thêm bài vào playlist\n"
                    "`!playlist remove <tên> <url>`: Xóa bài khỏi playlist\n"
                    "`!playlist play <tên>`: Phát toàn bộ playlist\n"
                    "`!playlist list`: Xem danh sách playlist\n"
                    "`!playlist delete <tên>`: Xóa playlist"
                ),
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi quản lý playlist: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def status(ctx):
    try:
        uptime = datetime.datetime.now() - datetime.datetime.fromtimestamp(bot.start_time)
        uptime_str = str(uptime).split(".")[0]
        guild_count = len(bot.guilds)
        memory_usage = (await asyncio.get_event_loop().run_in_executor(None, lambda: psutil.Process().memory_info().rss)) / 1024 / 1024
        embed = discord.Embed(title="📊 𝗧𝗿ạ𝗻𝗴 𝗧𝗵á𝗶 𝗖ủ𝗮 𝗛𝗶𝗻𝗮𝗮", color=discord.Color.blue())
        embed.add_field(name="⏰ 𝗧𝗵ờ𝗶 𝗚𝗶𝗮𝗻 𝗛𝗼ạ𝘁 Độ𝗻𝗴", value=uptime_str, inline=False)
        embed.add_field(name="🌐 𝗦𝗲𝗿𝘃𝗲𝗿", value=f"**{guild_count}** server", inline=False)
        embed.add_field(
            name="🎙️ 𝗧𝗿ạ𝗻𝗴 𝗧𝗵á𝗶 𝗩𝗼𝗶𝗰𝗲",
            value="Đang phát nhạc" if ctx.voice_client and ctx.voice_client.is_playing() else "Không hoạt động",
            inline=False
        )
        embed.add_field(name="💾 𝗕ộ 𝗡𝗵ớ", value=f"**{memory_usage:.2f} MB**", inline=False)
        embed.set_footer(text="✨ Hinaa luôn sẵn sàng nè! ✨")
        await ctx.send(embed=embed)
    except Exception as e:
        logger.exception(f"Lỗi khi hiển thị status: {e}")
        embed = discord.Embed(description="🚫 Ôi, có gì đó sai rồi! Thử lại nhé 😅", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="🎵 𝗖á𝗰 𝗟ệ𝗻𝗵 𝗖ủ𝗮 𝗛𝗶𝗻𝗮𝗮", color=discord.Color.blue())
    embed.add_field(
        name="🎶 𝗔𝗺 𝗡𝗵ạ𝗰",
        value=(
            "`!join` - Vào kênh voice của bạn\n"
            "`!leave` - Rời kênh voice\n"
            "`!play <url>` - Phát nhạc từ YouTube, Spotify, SoundCloud\n"
            "`!search <tên>` - Tìm và phát nhạc\n"
            "`!queue <url>` - Thêm bài vào hàng đợi (tối đa 50 bài)\n"
            "`!queue_list` - Xem danh sách hàng đợi\n"
            "`!skip` - Bỏ qua bài hiện tại (cần vote)\n"
            "`!karaoke <url>` - Hiển thị lời bài hát và phát nhạc\n"
            "`!volume <0-100>` - Điều chỉnh âm lượng\n"
            "`!np` - Xem bài đang phát\n"
            "`!playlist <hành động>` - Quản lý playlist (create/add/remove/play/list/delete)"
        ),
        inline=False
    )
    embed.add_field(name="😄 𝗚𝗶ả𝗶 𝗧𝗿í", value="`!guess_lol` - Đoán tướng LoL, thi đua điểm số!", inline=False)
    embed.add_field(
        name="⚙️ 𝗤𝘂ả𝗻 𝗟ý",
        value="`!status` - Xem trạng thái bot\n`!aichat` - Trò chuyện với Hinaa qua AI",
        inline=False
    )
    embed.set_footer(text="✨ Cần giúp đỡ? Liên hệ admin trong #hinaa-support nha! ✨")
    await ctx.send(embed=embed)

async def main():
    bot.start_time = time.time()
    logger.info("Hinaa đang khởi động...")
    async with bot:
        bot.loop.create_task(clear_lyrics_cache())
        for attempt in range(3):
            try:
                await bot.start(DISCORD_BOT_TOKEN)
                break
            except Exception as e:
                logger.exception(f"Lỗi khởi động bot lần {attempt + 1}: {e}")
                if attempt < 2:
                    await asyncio.sleep(5)
                else:
                    logger.error("Không thể khởi động bot sau 3 lần thử!")
                    raise

if __name__ == "__main__":
    asyncio.run(main())