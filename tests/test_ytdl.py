import unittest
from unittest.mock import patch, MagicMock

from src.command.ytdl import (
    is_youtube_url,
    get_ytdl_inline_button,
    _extract_url_from_text,
    _get_url_token,
    _url_token_cache,
)
from src.command.ytdl_engine import (
    VideoMeta,
    _format_duration,
    _format_size,
    extract_info_async,
)


class TestYtdlUrlParsing(unittest.TestCase):
    def test_youtube_url_detection(self):
        """Test identification of various YouTube URL formats."""
        self.assertTrue(is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(is_youtube_url("https://youtu.be/dQw4w9WgXcQ"))
        self.assertTrue(is_youtube_url("https://youtube.com/shorts/dQw4w9WgXcQ"))
        self.assertTrue(is_youtube_url("http://www.youtube.com/watch?v=12345&feature=shared"))
        self.assertFalse(is_youtube_url("https://github.com/Rongronggg9/RSS-to-Telegram-Bot"))
        self.assertFalse(is_youtube_url("https://example.com/video.mp4"))
        self.assertFalse(is_youtube_url(None))

    def test_extract_url_from_text(self):
        """Extract URLs from command arguments or chat messages."""
        text = "/ytdl https://www.youtube.com/watch?v=abc123xyz check this out"
        self.assertEqual(_extract_url_from_text(text), "https://www.youtube.com/watch?v=abc123xyz")
        self.assertIsNone(_extract_url_from_text("no link here"))
        self.assertIsNone(_extract_url_from_text(None))

    def test_url_token_cache_and_button(self):
        """Verify URL token generation and inline button formatting."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        token = _get_url_token(url)
        self.assertEqual(len(token), 8)
        self.assertEqual(_url_token_cache.get(token), url)

        btn = get_ytdl_inline_button(url)
        self.assertEqual(btn.text, "📥 Download (Admin)")
        self.assertEqual(btn.data.decode("utf-8"), f"ytdl:menu:{token}")
        # Telegram inline button data must be <= 64 bytes
        self.assertLessEqual(len(btn.data), 64)


class TestYtdlFormatting(unittest.TestCase):
    def test_format_duration(self):
        """Test seconds conversion to human-readable duration strings."""
        self.assertEqual(_format_duration(45), "0:45")
        self.assertEqual(_format_duration(125), "2:05")
        self.assertEqual(_format_duration(3665), "1:01:05")
        self.assertEqual(_format_duration(0), "N/A")
        self.assertEqual(_format_duration(None), "N/A")

    def test_format_size(self):
        """Test byte formatting to appropriate units."""
        self.assertEqual(_format_size(500), "500.0 B")
        self.assertEqual(_format_size(1024 * 1024), "1.0 MB")
        self.assertEqual(_format_size(1024 * 1024 * 1024 * 2), "2.0 GB")
        self.assertEqual(_format_size(0), "Unknown")


class TestYtdlEngine(unittest.IsolatedAsyncioTestCase):
    @patch("yt_dlp.YoutubeDL")
    async def test_extract_info_async_mock(self, mock_ydl_cls):
        """Verify metadata extraction with mocked yt_dlp."""
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "id": "dQw4w9WgXcQ",
            "title": "Never Gonna Give You Up",
            "uploader": "Rick Astley",
            "duration": 213,
            "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
            "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }
        mock_ydl_cls.return_value.__enter__.return_value = mock_instance

        meta = await extract_info_async("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIsInstance(meta, VideoMeta)
        self.assertEqual(meta.video_id, "dQw4w9WgXcQ")
        self.assertEqual(meta.title, "Never Gonna Give You Up")
        self.assertEqual(meta.uploader, "Rick Astley")
        self.assertEqual(meta.duration, 213)
        self.assertEqual(meta.duration_str, "3:33")


class TestYtdlI18n(unittest.TestCase):
    def test_manager_command_registration(self):
        """Verify ytdl is included in MANAGER_COMMANDS and has localization strings."""
        from src.i18n import MANAGER_COMMANDS, i18n
        self.assertIn("ytdl", MANAGER_COMMANDS)
        self.assertIn("cmd_description_ytdl", i18n["en"])
        self.assertIn("YouTube", i18n["en"]["cmd_description_ytdl"])
        self.assertIn("cmd_description_ytdl", i18n["zh-Hans"])


if __name__ == '__main__':
    unittest.main()
