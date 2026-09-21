"""
Interactive Telegram command and callback handler for YouTube and media downloading via yt-dlp.
"""

from __future__ import annotations
import hashlib
import re
from contextlib import suppress
from typing import Optional
from cachetools import TTLCache
from telethon import Button
from telethon.errors import RPCError

from .. import env, log
from .utils import command_gatekeeper, escape_html, parse_command
from .types import TypeEventMsgHint, TypeEventCb
from . import ytdl_engine

logger = log.getLogger('RSStT.command.ytdl')

# Cache mapping 8-character token -> URL (TTL 1 hour)
_url_token_cache: TTLCache[str, str] = TTLCache(maxsize=1024, ttl=3600)

_URL_REGEX = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')


def _extract_url_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = _URL_REGEX.search(text)
    return match.group(0) if match else None


def _get_url_token(url: str) -> str:
    token = hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]
    _url_token_cache[token] = url
    return token


def is_youtube_url(url: Optional[str]) -> bool:
    """Check if a URL is a YouTube video or shorts link."""
    if not url:
        return False
    return bool(re.search(r'(youtube\.com/(watch\?v=|shorts/)|youtu\.be/)', url, re.I))


def get_ytdl_inline_button(url: str) -> Button:
    """Generate an inline button for administrators to trigger the download menu."""
    token = _get_url_token(url)
    return Button.inline('📥 Download (Admin)', f'ytdl:menu:{token}')


@command_gatekeeper(only_manager=True)
async def cmd_ytdl(
        event: TypeEventMsgHint,
        *_,
        lang: Optional[str] = None,
        chat_id: Optional[int] = None,
        **__,
):
    """
    Handle /ytdl <url> or /ytdl replied to a message containing a video link.
    """
    args = parse_command(event.raw_text, max_split=1)
    target_url = None
    if len(args) > 1:
        target_url = _extract_url_from_text(args[1])

    if not target_url:
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.raw_text:
            target_url = _extract_url_from_text(reply_msg.raw_text)

    if not target_url:
        await event.respond(
            '<b>Media Downloader Usage:</b>\n\n'
            '• <code>/ytdl &lt;video_url&gt;</code>\n'
            '• Or reply with <code>/ytdl</code> to any message containing a YouTube or video link.\n\n'
            '<i>Supports YouTube, Bilibili, Twitter/X, and 1000+ sites supported by yt-dlp.</i>',
            parse_mode='html'
        )
        return

    status_msg = await event.respond('🔍 <i>Fetching video metadata...</i>', parse_mode='html')

    try:
        meta = await ytdl_engine.extract_info_async(target_url)
    except Exception as e:
        logger.warning(f'Failed to extract media info for {target_url}:', exc_info=e)
        await status_msg.edit(
            f'❌ <b>Failed to fetch media info:</b>\n<code>{escape_html(str(e)[:300])}</code>',
            parse_mode='html'
        )
        return

    token = _get_url_token(meta.webpage_url)

    buttons = [
        [
            Button.inline('🎬 Best Video (MP4)', f'ytdl:dl:video_best:{token}'),
            Button.inline('🎬 720p Video (MP4)', f'ytdl:dl:video_720:{token}'),
        ],
        [
            Button.inline('🎵 Audio Only (MP3)', f'ytdl:dl:audio_mp3:{token}'),
            Button.inline('❌ Cancel', 'ytdl:cancel'),
        ]
    ]

    caption = (
        f'🎬 <b>{escape_html(meta.title)}</b>\n'
        f'👤 <b>Channel:</b> {escape_html(meta.uploader)}\n'
        f'⏱ <b>Duration:</b> {meta.duration_str}\n\n'
        f'<i>Select a download format:</i>'
    )

    await status_msg.edit(caption, parse_mode='html', buttons=buttons)


async def callback_ytdl(event: TypeEventCb):
    """
    Handle ytdl inline button callbacks.
    """
    sender = await event.get_sender()
    sender_id = sender.id if sender else None
    if sender_id not in env.MANAGER:
        await event.answer('⛔ This action is only available to bot administrators.', alert=True)
        return

    data: str = event.data.decode('utf-8')

    if data == 'ytdl:cancel':
        with suppress(RPCError):
            await event.edit('❌ <i>Download cancelled.</i>', parse_mode='html', buttons=None)
        await event.answer('Cancelled')
        return

    if data.startswith('ytdl:menu:'):
        parts = data.split(':')
        if len(parts) < 3:
            await event.answer('Invalid action')
            return
        token = parts[2]
        url = _url_token_cache.get(token)
        if not url:
            await event.answer('⚠️ Session expired. Please send the link again.', alert=True)
            return
        await event.answer('Fetching video formats...')
        try:
            meta = await ytdl_engine.extract_info_async(url)
            buttons = [
                [
                    Button.inline('🎬 Best Video (MP4)', f'ytdl:dl:video_best:{token}'),
                    Button.inline('🎬 720p Video (MP4)', f'ytdl:dl:video_720:{token}'),
                ],
                [
                    Button.inline('🎵 Audio Only (MP3)', f'ytdl:dl:audio_mp3:{token}'),
                    Button.inline('❌ Cancel', 'ytdl:cancel'),
                ]
            ]
            caption = (
                f'🎬 <b>{escape_html(meta.title)}</b>\n'
                f'👤 <b>Channel:</b> {escape_html(meta.uploader)}\n'
                f'⏱ <b>Duration:</b> {meta.duration_str}\n\n'
                f'<i>Select a download format:</i>'
            )
            await event.respond(caption, parse_mode='html', buttons=buttons)
        except Exception as e:
            logger.warning(f'Failed to extract media info for {url}:', exc_info=e)
            await event.respond(
                f'❌ <b>Failed to fetch media info:</b>\n<code>{escape_html(str(e)[:300])}</code>',
                parse_mode='html'
            )
        return

    if not data.startswith('ytdl:dl:'):
        await event.answer('Invalid action')
        return

    parts = data.split(':')
    if len(parts) != 4:
        await event.answer('Malformed action data')
        return

    _, _, format_choice, token = parts
    url = _url_token_cache.get(token)
    if not url:
        await event.answer('⚠️ Session expired. Please send the link again.', alert=True)
        with suppress(RPCError):
            await event.edit('⚠️ <i>Session expired. Please send the link again.</i>', parse_mode='html', buttons=None)
        return

    await event.answer('Starting download...')
    with suppress(RPCError):
        await event.edit('⏳ <b>Initializing background download...</b>', parse_mode='html', buttons=None)

    async def progress_updater(status_text: str):
        with suppress(Exception):
            await event.edit(f'<b>{escape_html(status_text)}</b>', parse_mode='html')

    try:
        result = await ytdl_engine.download_media_async(
            url=url,
            format_choice=format_choice,
            progress_callback=progress_updater
        )
    except Exception as e:
        logger.error(f'Download failed for {url}:', exc_info=e)
        with suppress(RPCError):
            await event.edit(
                f'❌ <b>Download failed:</b>\n<code>{escape_html(str(e)[:300])}</code>',
                parse_mode='html'
            )
        return

    # Upload file via Telethon MTProto (supports up to 2GB)
    try:
        with suppress(RPCError):
            await event.edit('⬆️ <b>Uploading media to Telegram...</b>', parse_mode='html')

        await env.bot.send_file(
            event.chat_id,
            file=str(result.file_path),
            caption=f'🎬 <b>{escape_html(result.title)}</b>',
            parse_mode='html',
            supports_streaming=not result.is_audio,
        )

        with suppress(RPCError):
            await event.edit(f'✅ <b>Sent:</b> {escape_html(result.title)}', parse_mode='html')
    except Exception as e:
        logger.error(f'Failed to upload downloaded media for {url}:', exc_info=e)
        with suppress(RPCError):
            await event.edit(
                f'❌ <b>Failed to upload file to Telegram:</b>\n<code>{escape_html(str(e)[:300])}</code>',
                parse_mode='html'
            )
    finally:
        result.cleanup()
