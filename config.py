"""
config.py — All environment variables in one place.
Copy sample.env → .env and fill in your values.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Required ──────────────────────────────────────────────────────────────────
API_ID          = int(os.environ["API_ID"])
API_HASH        = os.environ["API_HASH"]
BOT_TOKEN       = os.environ["BOT_TOKEN"]
STRING_SESSION  = os.environ["STRING_SESSION"]
MONGO_DB_URL    = os.environ["MONGO_DB_URL"]
OWNER_ID        = int(os.environ["OWNER_ID"])
OWNER_USERNAME  = os.getenv("OWNER_USERNAME", "duifioookn2")

# ── Optional ──────────────────────────────────────────────────────────────────
BOT_NAME         = os.getenv("BOT_NAME", "Zara Music")
BOT_LINK         = os.getenv("BOT_LINK", "https://t.me/ZaraMusicBot")
UPDATES_CHANNEL  = os.getenv("UPDATES_CHANNEL", "https://t.me/your_updates_channel")
SUPPORT_GROUP    = os.getenv("SUPPORT_GROUP", "https://t.me/your_support_group")
LOGGER_ID        = int(os.getenv("LOGGER_ID", "0"))
PING_IMG_URL     = os.getenv("PING_IMG_URL", "https://raw.githubusercontent.com/sujalkumar48064-byte/ZaraMusic/main/Pic/IMG_20261104_125422.jpg")
SESSION_NAME     = os.getenv("SESSION_NAME", "ZaraMusic")
PORT             = int(os.getenv("PORT", 10000))

# ── Download config (cookies first, API as fallback) ─────────────────────────
# USE_COOKIES=True  : yt-dlp + cookies first, download API is the fallback
# USE_COOKIES=False : download API first, plain yt-dlp only if the API fails
# Default: False when API_URL + API_KEY are set, otherwise True.
# No API is hard-coded here — set your own via env (API_URL / API_KEY).
API_URL   = (os.getenv("API_URL") or os.getenv("YT_API_URL") or "").strip().rstrip("/")
API_KEY   = (os.getenv("API_KEY") or os.getenv("YT_API_KEY") or "").strip()


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


USE_COOKIES = _env_bool("USE_COOKIES", not (API_URL and API_KEY))

# Free public fallback (no API key). Invidious mirrors help when yt-dlp is
# blocked by YouTube on datacenter IPs.
INVIDIOUS_APIS = [
    u.strip().rstrip("/")
    for u in os.getenv("INVIDIOUS_APIS", "https://yt.chocolatemoo53.com").replace(",", " ").split()
    if u.strip().startswith("http")
]
FREE_API_FALLBACK = _env_bool("FREE_API_FALLBACK", True)
# batbin.me links to cookie files (space / comma / newline separated)
COOKIES_URL = [
    u for u in os.getenv("COOKIES_URL", "").replace(",", " ").split()
    if "batbin.me" in u
]
COOKIES_DIR       = "cookies"
DOWNLOAD_DIR      = "downloads"
YT_TOKEN_TIMEOUT  = 10    # seconds — fetch download token
YT_STREAM_TIMEOUT = 900   # 15 min  — download / stream long songs

# ── Performance / multi-group safety ─────────────────────────────────────────
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
SEARCH_TIMEOUT           = int(os.getenv("SEARCH_TIMEOUT", "12"))
DOWNLOAD_TIMEOUT         = int(os.getenv("DOWNLOAD_TIMEOUT", "180"))
PLAY_TIMEOUT             = int(os.getenv("PLAY_TIMEOUT", "60"))
PLAY_JOIN_TIMEOUT        = int(os.getenv("PLAY_JOIN_TIMEOUT", "20"))
BOT_WORKERS              = int(os.getenv("BOT_WORKERS", "32"))
VIDEO_FPS                = int(os.getenv("VIDEO_FPS", "24"))

# Public download sources can be slow/dead. Keep each attempt bounded so one
# broken mirror cannot hold the whole /play request for minutes.
INVIDIOUS_META_TIMEOUT   = int(os.getenv("INVIDIOUS_META_TIMEOUT", "8"))
FREE_SOURCE_TIMEOUT      = int(os.getenv("FREE_SOURCE_TIMEOUT", "60"))
FREE_SOURCE_BUDGET       = int(os.getenv("FREE_SOURCE_BUDGET", "120"))
YTDLP_DOWNLOAD_TIMEOUT   = int(os.getenv("YTDLP_DOWNLOAD_TIMEOUT", "90"))

# ── NSFW Moderation API ─────────────────────────────────────────────────────
#NSFW_API_URL = os.getenv("NSFW_API_URL", "https://ai-moderation-api-khyr.onrender.com")
#NSFW_API_KEY = os.getenv("NSFW_API_KEY", "nsfwBad")

# Custom detection thresholds — sent with every /detect/upload call.
#NSFW_THRESHOLDS = {
#    "porn": float(os.getenv("NSFW_THRESHOLD_PORN", "0.7")),
#    "sexy": float(os.getenv("NSFW_THRESHOLD_SEXY", "0.8")),
#}

#── Start ───────────────────────────────────────────────────────────────────────
START_PHOTOS = [
    "https://raw.githubusercontent.com/sujalkumar48064-byte/ZaraMusic/main/Pic/IMG_20261104_125422.jpg",
    "https://raw.githubusercontent.com/sujalkumar48064-byte/ZaraMusic/main/Pic/IMG_20261104_125445.jpg",
    "https://raw.githubusercontent.com/sujalkumar48064-byte/ZaraMusic/main/Pic/IMG_20261104_125510.jpg",
]

BANNER_TAGLINE = "✧ sᴍᴀʀᴛ ᴍᴜsɪᴄ ʙᴏᴛ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ ʟᴏᴏᴋ, ғᴀsᴛ & sᴇᴄᴜʀᴇ ✧"

BOT_SHORT_BIO = "✧ sᴍᴀʀᴛ ᴍᴜsɪᴄ ʙᴏᴛ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ ʟᴏᴏᴋ, ғᴀsᴛ & sᴇᴄᴜʀᴇ ✧"

BOT_DESCRIPTION = (
    "❍ sᴍᴀʀᴛ ᴍᴜsɪᴄ ʙᴏᴛ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ ʟᴏᴏᴋ\n"
    "❍ ғᴀsᴛ, sᴛᴀʙʟᴇ & sᴇᴄᴜʀᴇ ᴘʟᴀʏʙᴀᴄᴋ\n"
    "❍ ʜɪɢʜ-ǫᴜᴀʟɪᴛʏ ᴠᴏɪᴄᴇ & sᴍᴏᴏᴛʜ ᴠɪᴅᴇᴏ\n"
    "❍ ᴍᴜʟᴛɪ-ɢʀᴏᴜᴘ sᴜᴘᴘᴏʀᴛ\n"
    "✧ ᴘᴏᴡᴇʀᴇᴅ ʙʏ ZᴀʀᴀMᴜsɪᴄ ✧"
)

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_DURATION_SECONDS = 1800   # 30 minutes
QUEUE_LIMIT          = 20
COOLDOWN             = 10     # seconds between /play per chat

# ── Playlist limits ───────────────────────────────────────────────────────────
PLAYLIST_LIMIT       = 10     # playlists per user
PLAYLIST_SONG_LIMIT  = 50     # songs per playlist


#BLOCKED_EXTENSIONS = [
#    ".zip",
#    ".rar",
#    ".7z",
#    ".apk",
#    ".exe",
#    ".py",
#    ".js",
#    ".go",
#    ".php",
#]
