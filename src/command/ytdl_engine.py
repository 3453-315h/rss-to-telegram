"""
yt-dlp asynchronous engine for metadata extraction and media downloading.
"""

from __future__ import annotations
import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Callable, Any, NamedTuple

from .. import env, log
from ..aio_helper import run_async

logger = log.getLogger('RSStT.ytdl_engine')

# Bounded semaphore to prevent server CPU/disk saturation
_ytdl_semaphore = asyncio.Semaphore(env.YTDL_CONCURRENCY)


class VideoMeta(NamedTuple):
    video_id: str
    title: str
    uploader: str
    duration: int
    duration_str: str
    thumbnail: Optional[str]
    webpage_url: str


def _format_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds < 0:
        return 'N/A'
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'


def _format_size(num_bytes: Optional[int]) -> str:
    if not num_bytes or num_bytes <= 0:
        return 'Unknown'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if num_bytes < 1024.0:
            return f'{num_bytes:.1f} {unit}'
        num_bytes /= 1024.0
    return f'{num_bytes:.1f} TB'


def _get_ydl_base_opts(proxy: Optional[str] = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': False,
        'max_filesize': env.YTDL_MAX_FILE_SIZE,
        'user_agent': env.USER_AGENT,
    }
    if proxy or env.R_PROXY:
        opts['proxy'] = proxy or env.R_PROXY
    if env.YTDL_COOKIES_PATH and os.path.isfile(env.YTDL_COOKIES_PATH):
        opts['cookiefile'] = env.YTDL_COOKIES_PATH
    return opts


def _extract_info_sync(url: str) -> VideoMeta:
    import yt_dlp
    opts = _get_ydl_base_opts()
    opts['extract_flat'] = 'in_playlist'  # Don't download entire playlists
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise ValueError(f'Could not extract information from: {url}')

        # If it's a playlist or search result, take first entry
        if 'entries' in info and info['entries']:
            info = info['entries'][0]

        video_id = info.get('id') or 'unknown'
        title = info.get('title') or 'Untitled Video'
        uploader = info.get('uploader') or info.get('channel') or 'Unknown'
        duration = int(info.get('duration') or 0)
        duration_str = _format_duration(duration)
        thumbnail = info.get('thumbnail')
        webpage_url = info.get('webpage_url') or url

        return VideoMeta(
            video_id=video_id,
            title=title,
            uploader=uploader,
            duration=duration,
            duration_str=duration_str,
            thumbnail=thumbnail,
            webpage_url=webpage_url,
        )


async def extract_info_async(url: str) -> VideoMeta:
    """Extract media metadata asynchronously without downloading."""
    return await run_async(_extract_info_sync, url)


class DownloadResult:
    def __init__(self, file_path: Path, temp_dir: str, title: str, is_audio: bool):
        self.file_path = file_path
        self.temp_dir = temp_dir
        self.title = title
        self.is_audio = is_audio

    def cleanup(self):
        """Remove temporary download directory and files."""
        try:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f'Failed to clean up temp dir {self.temp_dir}: {e}')


def _download_sync(
        url: str,
        format_choice: str,
        progress_hook: Optional[Callable[[dict], None]] = None
) -> DownloadResult:
    import yt_dlp

    temp_dir = tempfile.mkdtemp(prefix='rsstt_ytdl_')
    outtmpl = os.path.join(temp_dir, '%(title).100s_%(id)s.%(ext)s')

    opts = _get_ydl_base_opts()
    opts['outtmpl'] = outtmpl

    is_audio = (format_choice == 'audio_mp3')

    if format_choice == 'video_720':
        opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best'
        opts['merge_output_format'] = 'mp4'
    elif format_choice == 'audio_mp3':
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:  # video_best
        opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best'
        opts['merge_output_format'] = 'mp4'

    if progress_hook:
        opts['progress_hooks'] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError(f'Failed to download {url}')

            title = info.get('title') or 'Downloaded Media'

            # Find the downloaded file inside temp_dir
            downloaded_files = [
                Path(temp_dir) / f for f in os.listdir(temp_dir)
                if not f.endswith('.part') and not f.endswith('.ytdl')
            ]
            if not downloaded_files:
                raise FileNotFoundError(f'No completed files found in {temp_dir}')

            # Pick the largest file or the target file
            target_file = max(downloaded_files, key=lambda f: f.stat().st_size)

            # Check max file size
            file_size = target_file.stat().st_size
            if file_size > env.YTDL_MAX_FILE_SIZE:
                raise ValueError(
                    f'File size ({_format_size(file_size)}) exceeds the maximum allowed '
                    f'upload limit ({_format_size(env.YTDL_MAX_FILE_SIZE)})'
                )

            return DownloadResult(
                file_path=target_file,
                temp_dir=temp_dir,
                title=title,
                is_audio=is_audio
            )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


async def download_media_async(
        url: str,
        format_choice: str = 'video_best',
        progress_callback: Optional[Callable[[str], Any]] = None
) -> DownloadResult:
    """
    Asynchronously download video or audio with bounded concurrency.
    """
    last_update_time = 0.0

    def throttled_progress_hook(d: dict):
        nonlocal last_update_time
        if not progress_callback:
            return
        status = d.get('status')
        now = time.monotonic()
        # Update progress at most once every 3 seconds to prevent Telegram flood waits
        if now - last_update_time < 3.0 and status != 'finished':
            return
        last_update_time = now

        if status == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            speed = d.get('speed') or 0
            percent_str = f'{(downloaded / total * 100):.1f}%' if total else '...'
            speed_str = f'{_format_size(int(speed))}/s' if speed else ''
            msg = f'⏳ Downloading: {percent_str} ({_format_size(downloaded)} / {_format_size(total)}) {speed_str}'.strip()
            asyncio.run_coroutine_threadsafe(progress_callback(msg), env.loop)
        elif status == 'finished':
            asyncio.run_coroutine_threadsafe(progress_callback('⚙️ Merging & converting media...'), env.loop)

    async with _ytdl_semaphore:
        return await run_async(_download_sync, url, format_choice, throttled_progress_hook)
