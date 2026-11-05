import asyncio
import logging
import os
import random
import re
import shutil
import sys
from typing import Optional, Union

import aiofiles
import aiohttp
import yt_dlp
from py_yt import Playlist, VideosSearch
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from config import *
from ZaraMusic.utils.formatters import sec_to_iso

logger = logging.getLogger(__name__)

_file_cache: dict[str, str] = {}
_video_cache: dict[str, str] = {}


# ═════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _extract_video_id(url: str) -> str:
    """Extract raw video ID from any YouTube URL format."""
    if "v=" in url:
        return url.split("v=")[-1].split("&")[0]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[-1].split("?")[0]
    return url


# Public alias — used by the autoplay engine to pull the video id back out
extract_video_id = _extract_video_id


def _cleanup(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def time_to_seconds(time) -> int:
    """Convert M:SS or H:MM:SS string to total seconds."""
    stringt = str(time)
    return sum(int(x) * 60 ** i for i, x in enumerate(reversed(stringt.split(":"))))


# ═════════════════════════════════════════════════════════════════════════════
# COOKIES  (batbin.me links in COOKIES_URL  +  any *.txt already in cookies/)
# ═════════════════════════════════════════════════════════════════════════════

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
_cookie_warned = False


def _get_cookie_file() -> Optional[str]:
    """Random cookie file from COOKIES_DIR, or None when there is none."""
    global _cookie_warned
    try:
        files = [
            os.path.join(COOKIES_DIR, f)
            for f in os.listdir(COOKIES_DIR)
            if f.endswith(".txt")
        ]
    except OSError:
        files = []
    if not files:
        if not _cookie_warned:
            _cookie_warned = True
            logger.warning("[cookies] No cookie files found; downloads might fail.")
        return None
    return random.choice(files)


async def save_cookies() -> int:
    """Fetch every COOKIES_URL (batbin.me) into COOKIES_DIR. Returns files saved."""
    if not COOKIES_URL:
        return 0
    os.makedirs(COOKIES_DIR, exist_ok=True)
    saved = 0
    async with aiohttp.ClientSession() as session:
        for url in COOKIES_URL:
            name = url.rstrip("/").split("/")[-1]
            # never use a user-supplied string as a path unless it is plain
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                logger.warning(f"[cookies] Skipping invalid link: {url}")
                continue
            try:
                async with session.get(
                    f"https://batbin.me/raw/{name}",
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[cookies] Failed to fetch {name}: HTTP {resp.status}")
                        continue
                    text = await resp.text()
                if not text.strip():
                    logger.warning(f"[cookies] {name} is empty")
                    continue
                async with aiofiles.open(os.path.join(COOKIES_DIR, f"{name}.txt"), "w") as f:
                    await f.write(text)
                saved += 1
            except Exception as e:
                logger.warning(f"[cookies] Failed to fetch {name}: {e}")
    logger.info(f"[cookies] Saved {saved}/{len(COOKIES_URL)} cookie file(s) in {COOKIES_DIR}/")
    return saved


# ═════════════════════════════════════════════════════════════════════════════
# DOWNLOAD HELPERS  (USE_COOKIES=True  → yt-dlp+cookies first, API fallback)
#                   (USE_COOKIES=False → API first, plain yt-dlp fallback)
# ═════════════════════════════════════════════════════════════════════════════

def _cached(video_id: str, video: bool) -> Optional[str]:
    for ext in (("mp4", "webm") if video else ("m4a", "mp3", "webm")):
        path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    return None


async def _download_via_api(video_id: str, video: bool) -> Optional[str]:
    """Download through API_URL/download?url=<id>&type=audio|video&api_key=..."""
    if not (API_URL and API_KEY):
        return None
    path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{'mp4' if video else 'mp3'}")
    tmp = path + ".part"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_URL}/download",
                params={"url": video_id, "type": "video" if video else "audio", "api_key": API_KEY},
                timeout=aiohttp.ClientTimeout(total=YT_STREAM_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[api] Download failed: HTTP {resp.status}")
                    return None
                async with aiofiles.open(tmp, "wb") as f:
                    async for chunk in resp.content.iter_chunked(131072):
                        await f.write(chunk)

        size = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        bad = size == 0
        if not bad and size < 4096:  # tiny file: probably a JSON/HTML error body
            with open(tmp, "rb") as fh:
                bad = fh.read(1) in (b"{", b"<", b"[")
        if bad:
            logger.warning("[api] No media returned (check API_URL / API_KEY)")
            _cleanup(tmp)
            return None
        os.replace(tmp, path)
        logger.info(f"[api] Downloaded {video_id} ({size // 1024} KB)")
        return path
    except Exception as e:
        logger.error(f"[api] download error: {e}")
        _cleanup(tmp)
        return None


# ═════════════════════════════════════════════════════════════════════════════
# FREE PUBLIC FALLBACKS  (no API key — Invidious + backup mirrors)
# ═════════════════════════════════════════════════════════════════════════════

def _bitrate(fmt: dict) -> int:
    try:
        return int(fmt.get("bitrate") or 0)
    except (TypeError, ValueError):
        return 0


def _height(fmt: dict) -> int:
    try:
        if fmt.get("height"):
            return int(fmt["height"])
    except (TypeError, ValueError):
        pass
    label = str(fmt.get("qualityLabel") or fmt.get("resolution") or "")
    match = re.search(r"(\d{2,4})p", label)
    if match:
        return int(match.group(1))
    match = re.search(r"x(\d{2,4})", label)
    return int(match.group(1)) if match else 0


def _pick_audio(data: dict):
    formats = [
        f for f in (data.get("adaptiveFormats") or [])
        if "audio" in (f.get("type") or "")
    ]
    if not formats:
        return None
    # Prefer m4a/AAC: it is directly playable and avoids slow re-encoding.
    m4a = [f for f in formats if "mp4" in (f.get("container") or f.get("type") or "")]
    webm = [f for f in formats if "webm" in (f.get("container") or f.get("type") or "")]
    best = max(m4a or webm or formats, key=_bitrate)
    url = best.get("url")
    if not url:
        return None
    kind = best.get("container") or best.get("type") or ""
    ext = ".m4a" if "mp4" in kind else ".webm" if "webm" in kind else ".m4a"
    return url, ext


def _pick_video(data: dict):
    muxed = data.get("formatStreams") or []
    if muxed:
        best = max(muxed, key=_height)
        url = best.get("url")
        if url:
            ext = ".webm" if "webm" in (best.get("container") or best.get("type") or "") else ".mp4"
            return url, ext, None, None

    formats = [
        f for f in (data.get("adaptiveFormats") or [])
        if "video" in (f.get("type") or "") and _height(f) <= 720
    ]
    mp4 = [f for f in formats if "mp4" in (f.get("type") or "")]
    if not mp4:
        return None
    best = max(mp4, key=lambda f: (_height(f), _bitrate(f)))

    audio = [
        f for f in (data.get("adaptiveFormats") or [])
        if "audio" in (f.get("type") or "") and "mp4" in (f.get("type") or "")
    ]
    if not audio:
        return None
    abest = max(audio, key=_bitrate)
    if not best.get("url") or not abest.get("url"):
        return None
    return best["url"], ".mp4", abest["url"], ".m4a"


async def _save_stream(resp, path: str) -> Optional[str]:
    tmp = path + ".part"
    try:
        async with aiofiles.open(tmp, "wb") as handle:
            async for chunk in resp.content.iter_chunked(131072):
                await handle.write(chunk)
        size = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        bad = size < 1024
        if not bad:
            with open(tmp, "rb") as handle:
                bad = handle.read(64).lstrip()[:1] in (b"{", b"<", b"[")
        if bad:
            _cleanup(tmp)
            return None
        os.replace(tmp, path)
        return path
    except Exception as e:
        logger.warning(f"[free] save failed: {e}")
        _cleanup(tmp)
        return None


async def _download_url(session, url: str, path: str, attempts: int = 2) -> Optional[str]:
    for attempt in range(1, attempts + 1):
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=YT_STREAM_TIMEOUT)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[free] HTTP {resp.status} (attempt {attempt}/{attempts})")
                    continue
                got = await _save_stream(resp, path)
            if got:
                return got
        except Exception as e:
            logger.warning(f"[free] download error (attempt {attempt}/{attempts}): {e}")
    return None


async def _merge_av(video_path: str, audio_path: str, out_path: str) -> Optional[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("[invidious] ffmpeg not found; cannot merge video")
        return None
    tmp = out_path + ".render.mp4"
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", video_path, "-i", audio_path,
        "-c", "copy", "-movflags", "+faststart", "-f", "mp4", tmp,
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            tail = (err or b"").decode(errors="ignore")[-300:]
            logger.warning(f"[invidious] ffmpeg failed: {tail}")
            _cleanup(tmp)
            return None
        if not os.path.exists(tmp) or os.path.getsize(tmp) < 1024:
            _cleanup(tmp)
            return None
        os.replace(tmp, out_path)
        return out_path
    except Exception as e:
        logger.warning(f"[invidious] merge error: {e}")
        if proc and proc.returncode is None:
            proc.kill()
        _cleanup(tmp)
        return None


async def _fetch_invidious(session, video_id: str):
    for base in INVIDIOUS_APIS:
        try:
            async with session.get(
                f"{base}/api/v1/videos/{video_id}",
                params={"local": "true"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[invidious] {base}: HTTP {resp.status}")
                    continue
                data = await resp.json(content_type=None)
            if not isinstance(data, dict) or data.get("error"):
                logger.warning(f"[invidious] {base}: {str((data or {}).get('error'))[:120]}")
                continue
            return data
        except Exception as e:
            logger.warning(f"[invidious] {base}: {e}")
    return None


async def _download_via_invidious(video_id: str, video: bool) -> Optional[str]:
    if not INVIDIOUS_APIS:
        return None
    async with aiohttp.ClientSession() as session:
        data = await _fetch_invidious(session, video_id)
        if not data:
            return None

        if not video:
            picked = _pick_audio(data)
            if not picked:
                logger.warning(f"[invidious] no audio stream for {video_id}")
                return None
            url, ext = picked
            path = os.path.join(DOWNLOAD_DIR, f"{video_id}{ext}")
            got = await _download_url(session, url, path)
            if got:
                logger.info(f"[invidious] Downloaded audio {video_id}")
            return got

        picked = _pick_video(data)
        if not picked:
            logger.warning(f"[invidious] no video stream for {video_id}")
            return None
        vurl, vext, aurl, aext = picked

        if not aurl:
            path = os.path.join(DOWNLOAD_DIR, f"{video_id}{vext}")
            got = await _download_url(session, vurl, path)
            if got:
                logger.info(f"[invidious] Downloaded video {video_id}")
            return got

        path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")
        vpath = os.path.join(DOWNLOAD_DIR, f".{video_id}.video{vext}")
        apath = os.path.join(DOWNLOAD_DIR, f".{video_id}.audio{aext}")
        try:
            if not await _download_url(session, vurl, vpath):
                return None
            if not await _download_url(session, aurl, apath):
                return None
            if not await _merge_av(vpath, apath, path):
                return None
            logger.info(f"[invidious] Downloaded + merged video {video_id}")
            return path
        finally:
            _cleanup(vpath)
            _cleanup(apath)


async def _download_via_multimp3(video_id: str) -> Optional[str]:
    path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
    payload = {"url": f"https://www.youtube.com/watch?v={video_id}", "quality": "128"}
    try:
        async with aiohttp.ClientSession() as session:
            for attempt in range(1, 3):
                try:
                    async with session.post(
                        "https://multimp3.vercel.app/api/download",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=YT_STREAM_TIMEOUT),
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(
                                f"[free:multimp3] HTTP {resp.status} (attempt {attempt}/2)"
                            )
                            continue
                        got = await _save_stream(resp, path)
                    if got:
                        logger.info(f"[free:multimp3] Downloaded {video_id}")
                        return got
                except Exception as e:
                    logger.warning(f"[free:multimp3] attempt {attempt}/2: {e}")
    except Exception as e:
        logger.warning(f"[free:multimp3] {e}")
    return None


async def _download_via_samplecut(video_id: str) -> Optional[str]:
    path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
    payload = {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "mp3"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.samplecut.app/v1/downloads",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                if resp.status not in (200, 201):
                    logger.warning(f"[free:samplecut] HTTP {resp.status}")
                    return None
                data = await resp.json(content_type=None)
            link = (data or {}).get("download_url")
            if not link:
                logger.warning("[free:samplecut] no download_url")
                return None
            got = await _download_url(session, link, path)
            if got:
                logger.info(f"[free:samplecut] Downloaded {video_id}")
            return got
    except Exception as e:
        logger.warning(f"[free:samplecut] {e}")
        return None


async def _download_via_freeapi(video_id: str, video: bool) -> Optional[str]:
    ext = ".mp4" if video else ".mp3"
    path = os.path.join(DOWNLOAD_DIR, f"{video_id}{ext}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://freeapi.pro/api/alldl",
                params={"url": f"https://www.youtube.com/watch?v={video_id}"},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[free:freeapi] HTTP {resp.status}")
                    return None
                data = await resp.json(content_type=None)
            if not (data or {}).get("success"):
                logger.warning(f"[free:freeapi] {str((data or {}).get('error'))[:120]}")
                return None
            media = (data.get("mediaInfo") or {}).get("videoUrl" if video else "audioUrl")
            if not media:
                logger.warning("[free:freeapi] no media url")
                return None
            got = await _download_url(session, media, path)
            if got:
                logger.info(f"[free:freeapi] Downloaded {video_id}")
            return got
    except Exception as e:
        logger.warning(f"[free:freeapi] {e}")
        return None


async def _download_via_free(video_id: str, video: bool) -> Optional[str]:
    if not FREE_API_FALLBACK:
        return None

    got = await _download_via_invidious(video_id, video)
    if got:
        return got

    if not video:
        got = await _download_via_multimp3(video_id)
        if got:
            return got
        got = await _download_via_samplecut(video_id)
        if got:
            return got

    return await _download_via_freeapi(video_id, video)


async def _download_ytdlp(video_id: str, video: bool) -> Optional[str]:
    """Download with yt-dlp, using a random cookie file when USE_COOKIES is on."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--quiet", "--no-warnings", "--no-playlist", "--geo-bypass",
        "--no-overwrites", "--no-check-certificates",
        "-o", os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s"),
    ]
    cookie_copy = None
    if USE_COOKIES:
        cookie = _get_cookie_file()
        if cookie:
            # yt-dlp rewrites its cookie file; give it a private copy
            cookie_copy = os.path.join(COOKIES_DIR, f".tmp_{video_id}_{os.getpid()}")
            try:
                shutil.copyfile(cookie, cookie_copy)
                cmd += ["--cookies", cookie_copy]
            except OSError:
                cookie_copy = None
    if video:
        cmd += ["-f", "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio)",
                "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestaudio[ext=webm][acodec=opus]/bestaudio"]
    cmd += ["--", f"https://www.youtube.com/watch?v={video_id}"]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=YT_STREAM_TIMEOUT)
        if proc.returncode != 0:
            tail = (err or b"").decode(errors="ignore").strip().splitlines()[-1:] or [""]
            logger.warning(f"[yt-dlp] failed for {video_id} (exit {proc.returncode}): {tail[0]}")
            return None
        found = _cached(video_id, video)
        if found:
            logger.info(f"[yt-dlp] Downloaded {video_id}")
        return found
    except asyncio.TimeoutError:
        logger.warning(f"[yt-dlp] timed out for {video_id}")
        if proc and proc.returncode is None:
            proc.kill()
        return None
    except Exception as e:
        logger.error(f"[yt-dlp] error: {e}")
        return None
    finally:
        if cookie_copy:
            _cleanup(cookie_copy)


async def _download(link: str, video: bool) -> Optional[str]:
    video_id = _extract_video_id(link)
    if not _ID_RE.match(video_id or ""):
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    have = _cached(video_id, video)
    if have:
        return have

    api_set = bool(API_URL and API_KEY)
    cookie_ready = bool(USE_COOKIES and _get_cookie_file())

    if not cookie_ready:                       # free/API first (fast start)
        if api_set:
            got = await _download_via_api(video_id, video)
            if got:
                return got
            logger.info(f"[yt] API failed, trying free fallback for {video_id}")
        got = await _download_via_free(video_id, video)
        if got:
            return got
        logger.info(f"[yt] free fallback failed, trying yt-dlp for {video_id}")
        return await _download_ytdlp(video_id, video)

    got = await _download_ytdlp(video_id, video)
    if got:
        return got

    logger.info(f"[yt] yt-dlp failed, trying free fallback for {video_id}")
    got = await _download_via_free(video_id, video)
    if got:
        return got
    if api_set:
        logger.info(f"[yt] free fallback failed, trying API for {video_id}")
        return await _download_via_api(video_id, video)
    return None


async def _to_mp3(path: str) -> str:
    if not path or path.endswith((".mp3", ".m4a")):
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return path
    out = os.path.splitext(path)[0] + ".mp3"
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    tmp = out + ".part.mp3"
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-threads", "0", "-i", path,
        "-vn", "-acodec", "libmp3lame", "-b:a", "128k",
        "-compression_level", "0", "-f", "mp3", tmp,
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1024:
            tail = (err or b"").decode(errors="ignore")[-200:]
            logger.warning(f"[audio] mp3 convert failed: {tail}")
            _cleanup(tmp)
            return path
        os.replace(tmp, out)
        _cleanup(path)
        logger.info(f"[audio] Converted to mp3: {out}")
        return out
    except Exception as e:
        logger.warning(f"[audio] mp3 convert error: {e}")
        if proc and proc.returncode is None:
            proc.kill()
        _cleanup(tmp)
        return path


async def download_song(link: str) -> Optional[str]:
    """Download audio. Returns local file path or None on failure."""
    path = await _download(link, video=False)
    if path:
        path = await _to_mp3(path)
    return path


async def download_video(link: str) -> Optional[str]:
    """Download video (mp4). Returns local file path or None on failure."""
    return await _download(link, video=True)


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC — STREAM RESOLVER (backward-compatible)
# ═════════════════════════════════════════════════════════════════════════════

async def resolve_stream(url: str) -> str:
    """Resolve a YouTube URL or video ID to a local audio file path."""
    # Already a local file (e.g. Telegram audio download)
    if os.path.exists(url) and os.path.isfile(url):
        return url

    if url in _file_cache and os.path.exists(_file_cache[url]):
        logger.info("[yt] Cache hit")
        return _file_cache[url]

    downloaded = await download_song(url)
    if downloaded:
        _file_cache[url] = downloaded
        return downloaded

    raise Exception("Download failed (cookies/API). Please try again.")


async def resolve_video_stream(url: str) -> str:
    """Resolve a YouTube URL or video ID to a local MP4 (audio + video) file path."""
    if os.path.exists(url) and os.path.isfile(url):
        return url

    if url in _video_cache and os.path.exists(_video_cache[url]):
        logger.info("[yt] Video cache hit")
        return _video_cache[url]

    downloaded = await download_video(url)
    if downloaded:
        _video_cache[url] = downloaded
        return downloaded

    raise Exception("Video download failed (cookies/API). Please try again.")


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC — YOUTUBE SEARCH / METADATA
# ═════════════════════════════════════════════════════════════════════════════

async def search_yt(query: str):
    """Search YouTube for a video or playlist. Returns metadata tuple or playlist dict."""

    # ── Playlist ──────────────────────────────────────────────────────────────
    if "playlist?list=" in query or "&list=" in query:
        pl   = await Playlist.get(query)
        vids = pl.get("videos") or []
        if not vids:
            raise Exception("ᴩʟᴀʏʟɪsᴛ ɪs ᴇᴍᴩᴛʏ")

        items = []
        for v in vids:
            raw = v.get("duration", {})
            if isinstance(raw, dict):
                try:
                    secs = int(raw.get("secondsText", 0))
                except Exception:
                    secs = 0
            else:
                try:
                    secs = int(raw)
                except Exception:
                    secs = 0

            thumbs = v.get("thumbnails") or []
            thumb  = thumbs[0].get("url", "").split("?")[0] if thumbs else ""
            items.append({
                "link":      f"https://www.youtube.com/watch?v={v['id']}",
                "title":     v.get("title", "Unknown"),
                "duration":  sec_to_iso(secs),
                "thumbnail": thumb,
            })
        return {"playlist": items}

    # ── Single video search ───────────────────────────────────────────────────
    search  = VideosSearch(query, limit=1)
    results = await search.next()
    lst     = results.get("result", [])
    if not lst:
        raise Exception("ɴᴏ ʀᴇsᴜʟᴛs ғᴏᴜɴᴅ")

    r     = lst[0]
    url   = r.get("link") or f"https://www.youtube.com/watch?v={r['id']}"
    title = r.get("title", "Unknown")
    thumb = (r.get("thumbnails") or [{}])[0].get("url", "").split("?")[0]
    dur   = r.get("duration") or "0:00"

    parts = [int(x) for x in dur.split(":")]
    secs  = (
        parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 3
        else parts[0] * 60 + parts[1]
    )
    return (url, title, sec_to_iso(secs), thumb)


# ═════════════════════════════════════════════════════════════════════════════
# "UP NEXT" / RELATED VIDEOS  (used by AutoPlay)
# ═════════════════════════════════════════════════════════════════════════════
_INNERTUBE_NEXT = (
    "https://www.youtube.com/youtubei/v1/next"
    "?prettyPrint=false&key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
)
_INNERTUBE_CLIENT_VERSION = "2.20250101.00.00"
_DURATION_RE = re.compile(r"^\d{1,2}(?::\d{2}){1,2}$")


def _yt_text(node) -> str:
    """Plain text of a YouTube text object (simpleText / runs / content)."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if "simpleText" in node:
            return str(node["simpleText"])
        if "runs" in node:
            return "".join(str(r.get("text", "")) for r in node["runs"] if isinstance(r, dict))
        if "content" in node:
            return str(node["content"])
    return ""


def _find_duration(node):
    """First 'm:ss' / 'h:mm:ss' string anywhere inside node."""
    if isinstance(node, str):
        return node if _DURATION_RE.match(node.strip()) else None
    if isinstance(node, dict):
        for v in node.values():
            found = _find_duration(v)
            if found:
                return found.strip()
    elif isinstance(node, list):
        for v in node:
            found = _find_duration(v)
            if found:
                return found.strip()
    return None


def _first_content(node):
    """First 'content' string inside node (used for the channel name)."""
    if isinstance(node, dict):
        if isinstance(node.get("content"), str) and node["content"].strip():
            return node["content"]
        for v in node.values():
            found = _first_content(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _first_content(v)
            if found:
                return found
    return None


def parse_related(data, exclude_id=None, limit: int = 30):
    """
    Pull the recommended videos out of a youtubei /next response.
    Works with both the old (compactVideoRenderer) and the new
    (lockupViewModel) layouts, so a small YouTube change won't break it.
    """
    out, seen = [], set()

    def add(vid, title, duration, channel):
        if not vid or not title or vid == exclude_id or vid in seen:
            return
        seen.add(vid)
        out.append({"id": vid, "title": title, "duration": duration or "", "channel": channel or ""})

    def walk(node):
        if isinstance(node, dict):
            cvr = node.get("compactVideoRenderer")
            if isinstance(cvr, dict):
                add(
                    cvr.get("videoId"),
                    _yt_text(cvr.get("title")),
                    _yt_text(cvr.get("lengthText")) or _find_duration(cvr.get("thumbnailOverlays")),
                    _yt_text(cvr.get("longBylineText") or cvr.get("shortBylineText")),
                )
            lvm = node.get("lockupViewModel")
            if isinstance(lvm, dict) and lvm.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
                meta = (lvm.get("metadata") or {}).get("lockupMetadataViewModel") or {}
                add(
                    lvm.get("contentId"),
                    _yt_text(meta.get("title")),
                    _find_duration(lvm.get("contentImage")),
                    _first_content(meta.get("metadata")),
                )
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out[:limit]


async def related_videos(video_id: str, limit: int = 30) -> list:
    """Videos YouTube itself suggests after `video_id` (best source for autoplay)."""
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": _INNERTUBE_CLIENT_VERSION,
                "hl": "en",
                "gl": "US",
            }
        },
        "videoId": video_id,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
        "Origin": "https://www.youtube.com",
        "X-YouTube-Client-Name": "1",
        "X-YouTube-Client-Version": _INNERTUBE_CLIENT_VERSION,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _INNERTUBE_NEXT,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
        return parse_related(data, exclude_id=video_id, limit=limit)
    except Exception as e:
        logger.warning(f"[related_videos] {video_id}: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC — YouTubeAPI CLASS (full-featured, from Youtube5 style)
# ═════════════════════════════════════════════════════════════════════════════

class YouTubeAPI:
    def __init__(self):
        self.base     = "https://www.youtube.com/watch?v="
        self.regex    = r"(?:youtube\.com|youtu\.be)"
        self.status   = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg      = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_link(self, link: str, videoid) -> str:
        return (self.base + link) if videoid else link

    def _strip_extra(self, link: str) -> str:
        return link.split("&")[0] if "&" in link else link

    # ── Public methods ────────────────────────────────────────────────────────

    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        for message in messages:
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        return text[entity.offset: entity.offset + entity.length]
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    async def details(self, link: str, videoid: Union[bool, str] = None):
        link = self._strip_extra(self._build_link(link, videoid))
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title        = result["title"]
            duration_min = result["duration"]
            thumbnail    = result["thumbnails"][0]["url"].split("?")[0]
            vidid        = result["id"]
            duration_sec = int(time_to_seconds(duration_min)) if duration_min else 0
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: Union[bool, str] = None) -> str:
        link = self._strip_extra(self._build_link(link, videoid))
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return result["title"]

    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str:
        link = self._strip_extra(self._build_link(link, videoid))
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return result["duration"]

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str:
        link = self._strip_extra(self._build_link(link, videoid))
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return result["thumbnails"][0]["url"].split("?")[0]

    async def video(self, link: str, videoid: Union[bool, str] = None):
        link = self._strip_extra(self._build_link(link, videoid))
        try:
            downloaded_file = await download_video(link)
            if downloaded_file:
                return 1, downloaded_file
            return 0, "Video download failed"
        except Exception as e:
            return 0, f"Video download error: {e}"

    async def playlist(
        self, link: str, limit: int, user_id, videoid: Union[bool, str] = None
    ) -> list:
        if videoid:
            link = self.listbase + link
        link = self._strip_extra(link)
        try:
            plist = await Playlist.get(link)
        except Exception:
            return []
        videos = plist.get("videos") or []
        ids = []
        for data in videos[:limit]:
            if not data:
                continue
            vid = data.get("id")
            if not vid:
                continue
            ids.append(vid)
        return ids

    async def track(self, link: str, videoid: Union[bool, str] = None):
        link = self._strip_extra(self._build_link(link, videoid))
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title        = result["title"]
            duration_min = result["duration"]
            vidid        = result["id"]
            yturl        = result["link"]
            thumbnail    = result["thumbnails"][0]["url"].split("?")[0]
        track_details = {
            "title":        title,
            "link":         yturl,
            "vidid":        vidid,
            "duration_min": duration_min,
            "thumb":        thumbnail,
        }
        return track_details, vidid

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        link = self._strip_extra(self._build_link(link, videoid))
        ytdl_opts = {"quiet": True}
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for fmt in r["formats"]:
                try:
                    if "dash" not in str(fmt["format"]).lower():
                        formats_available.append(
                            {
                                "format":      fmt["format"],
                                "filesize":    fmt.get("filesize"),
                                "format_id":   fmt["format_id"],
                                "ext":         fmt["ext"],
                                "format_note": fmt["format_note"],
                                "yturl":       link,
                            }
                        )
                except Exception:
                    continue
        return formats_available, link

    async def slider(
        self, link: str, query_type: int, videoid: Union[bool, str] = None
    ):
        link = self._strip_extra(self._build_link(link, videoid))
        a      = VideosSearch(link, limit=10)
        result = (await a.next()).get("result")
        title        = result[query_type]["title"]
        duration_min = result[query_type]["duration"]
        vidid        = result[query_type]["id"]
        thumbnail    = result[query_type]["thumbnails"][0]["url"].split("?")[0]
        return title, duration_min, thumbnail, vidid

    async def download(
        self,
        link: str,
        mystic,
        video:     Union[bool, str] = None,
        videoid:   Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title:     Union[bool, str] = None,
    ):
        if videoid:
            link = self.base + link
        try:
            if video:
                downloaded_file = await download_video(link)
            else:
                downloaded_file = await download_song(link)
            if downloaded_file:
                return downloaded_file, True
            return None, False
        except Exception:
            return None, False
