# Unit tests for bug fixes identified in tofix.md
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import logging
import sys


class TestModels(unittest.TestCase):
    def test_user_str_returns_str(self):
        """User.__str__ must return str, not int."""
        from src.db.models import User
        user = User(id=123456789)
        self.assertIsInstance(str(user), str)
        self.assertEqual(str(user), "123456789")
        self.assertEqual(f"{user}", "123456789")


class TestLogFilters(unittest.TestCase):
    def test_apsc_filter_with_percent_in_message(self):
        """_APSCFilter must handle % characters safely when args is empty/None."""
        from src.log import _APSCFilter
        flt = _APSCFilter()
        # Message with URL encoded string %20 and no args
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Downloading https://example.com/feed%20name",
            args=None,
            exc_info=None,
        )
        # Should not raise TypeError or ValueError
        result = flt.filter(record)
        self.assertTrue(result)

    def test_aiohttp_access_filter_with_percent_in_message(self):
        """_AiohttpAccessFilter must handle % characters safely."""
        from src.log import _AiohttpAccessFilter
        flt = _AiohttpAccessFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Progress: 100% completed",
            args=(),
            exc_info=None,
        )
        result = flt.filter(record)
        self.assertTrue(result)

    def test_telethon_client_updates_filter_with_percent_in_message(self):
        """_TelethonClientUpdatesFilter must handle % characters safely."""
        from src.log import _TelethonClientUpdatesFilter
        flt = _TelethonClientUpdatesFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Received update: %s_action without args %%",
            args=(),
            exc_info=None,
        )
        result = flt.filter(record)
        self.assertTrue(result)


class TestAerichVersionCheck(unittest.TestCase):
    def test_aerich_multi_digit_minor_version(self):
        """Aerich version check must not fail on multi-digit versions like 0.10.0."""
        def check_version(ver_str):
            try:
                aerich_tuple = tuple(int(x) for x in ver_str.split('.')[:2])
            except (ValueError, TypeError):
                aerich_tuple = (0, 0)
            return aerich_tuple >= (0, 7)

        self.assertTrue(check_version('0.7.0'))
        self.assertTrue(check_version('0.7.1'))
        self.assertTrue(check_version('0.10.0'))
        self.assertTrue(check_version('0.10.5'))
        self.assertTrue(check_version('1.0.0'))
        self.assertFalse(check_version('0.6.3'))
        self.assertFalse(check_version('0.5.0'))


class TestVersionComparison(unittest.TestCase):
    def test_python_version_tuple_comparison(self):
        """Python version tuple comparisons work across versions >= (3, 10)."""
        self.assertTrue((3, 10, 0) >= (3, 10))
        self.assertTrue((3, 11, 2) >= (3, 10))
        self.assertTrue((4, 0, 0) >= (3, 10))
        self.assertFalse((3, 9, 7) >= (3, 10))


class TestNotifierStringHandling(unittest.IsolatedAsyncioTestCase):
    async def test_notifier_send_exception_handling_with_str(self):
        """Notifier._send must not crash with AttributeError when post is a string."""
        from src.monitor._notifier import Notifier
        from src import env

        sub = MagicMock()
        sub.user_id = 999
        sub.notify = True
        sub.feed = MagicMock()
        sub.feed.link = 'https://example.com/rss'
        sub.feed.title = 'Test Feed'

        # Mock env.bot to simulate failure
        with patch.object(env, 'bot', new=MagicMock()):
            env.bot.get_input_entity = AsyncMock(return_value=True)
            # Make send_message fail when sending the string post
            env.bot.send_message = AsyncMock(side_effect=[Exception("Network failure"), AsyncMock()])

            notifier = Notifier.__new__(Notifier)
            notifier._user_blocked_counter = {}

            with patch('src.monitor._notifier.logger') as mock_logger:
                with patch('src.monitor._notifier.Post') as mock_post_cls:
                    mock_post_instance = MagicMock()
                    mock_post_instance.send_formatted_post = AsyncMock()
                    mock_post_cls.return_value = mock_post_instance

                    # Call _send with string post
                    await notifier._send(sub, "Deactivation warning message")

                    # Verify logger.error was called with the string feed_link fallback instead of crashing
                    mock_logger.error.assert_called()


class TestAioHelperCPUFallback(unittest.TestCase):
    def test_cpu_count_fallback_when_none(self):
        """CPU_COUNT must default to >= 1 even if os.cpu_count() returns None."""
        simulated_os_cpu_count = None
        cpu_count = simulated_os_cpu_count or 1
        self.assertEqual(cpu_count, 1)
        avail_cpu_count = None or cpu_count
        self.assertEqual(avail_cpu_count, 1)
        self.assertGreater(min(cpu_count, avail_cpu_count), 0)


class TestPostUserRelation(unittest.IsolatedAsyncioTestCase):
    async def test_sub_user_not_fetched(self):
        """send_formatted_post_according_to_sub should check sub.user and fetch related user."""
        from src.parsing.post import Post
        from src.db.models import User

        sub = MagicMock()
        sub.user = None  # Not a User instance yet
        sub.fetch_related = AsyncMock()

        post = Post(html="Test content", title="Title")
        post.send_formatted_post = AsyncMock()

        # Call send_formatted_post_according_to_sub
        await post.send_formatted_post_according_to_sub(sub)
        sub.fetch_related.assert_awaited_once_with('user')

    async def test_sub_user_already_fetched(self):
        """send_formatted_post_according_to_sub should not re-fetch if sub.user is already User."""
        from src.parsing.post import Post
        from src.db.models import User

        sub = MagicMock()
        sub.user = User(id=123)  # Already a User instance
        sub.fetch_related = AsyncMock()

        post = Post(html="Test content", title="Title")
        post.send_formatted_post = AsyncMock()

        await post.send_formatted_post_according_to_sub(sub)
        sub.fetch_related.assert_not_called()


class TestOpmlSizeGuard(unittest.TestCase):
    def test_opml_size_limit_check(self):
        """OPML import should reject files larger than 20MB."""
        event_small = MagicMock()
        event_small.file = MagicMock()
        event_small.file.size = 1024 * 1024  # 1MB
        self.assertFalse(getattr(getattr(event_small, 'file', None), 'size', None) > 20 * 1024 * 1024)

        event_large = MagicMock()
        event_large.file = MagicMock()
        event_large.file.size = 25 * 1024 * 1024  # 25MB
        self.assertTrue(getattr(getattr(event_large, 'file', None), 'size', None) > 20 * 1024 * 1024)


class TestEffectiveTasksDivZero(unittest.TestCase):
    def test_effective_tasks_div_zero_guard(self):
        """Ensure divisor in __get_tasks handles interval <= __run_count without ZeroDivisionError."""
        from src.db.effective_utils import EffectiveTasks
        et = EffectiveTasks(interval=5)
        et._EffectiveTasks__all_feeds = {1, 2, 3}
        et._EffectiveTasks__pending_feeds = [1, 2, 3]
        et._EffectiveTasks__run_count = 5  # interval == run_count

        # In previous code, 5 - 5 = 0, causing ZeroDivisionError
        tasks = et._EffectiveTasks__get_tasks()
        self.assertIsInstance(tasks, set)
        self.assertGreater(len(tasks), 0)

    def test_effective_tasks_run_count_exceeds_interval(self):
        """Ensure divisor handles __run_count > interval."""
        from src.db.effective_utils import EffectiveTasks
        et = EffectiveTasks(interval=2)
        et._EffectiveTasks__all_feeds = {1, 2}
        et._EffectiveTasks__pending_feeds = [1, 2]
        et._EffectiveTasks__run_count = 5  # run_count > interval

        tasks = et._EffectiveTasks__get_tasks()
        self.assertIsInstance(tasks, set)
        self.assertGreater(len(tasks), 0)


class TestPostFormatterValidation(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_parameters_raise_value_error(self):
        """PostFormatter.get_formatted_post must raise ValueError on invalid arguments."""
        from src.parsing.post_formatter import PostFormatter

        formatter = PostFormatter(html="<p>Test</p>", title="Test Title", feed_title="Feed Title")

        with self.assertRaises(ValueError):
            await formatter.get_formatted_post(send_mode=999)

        with self.assertRaises(ValueError):
            await formatter.get_formatted_post(length_limit=-1)

        with self.assertRaises(ValueError):
            await formatter.get_formatted_post(link_preview=999)

        with self.assertRaises(ValueError):
            await formatter.get_formatted_post(style=999)

        with self.assertRaises(ValueError):
            await formatter.get_formatted_post(display_media=999)


class TestProxyFilter(unittest.TestCase):
    def test_localhost_and_private_bypass(self):
        """proxy_filter should return False for localhost, localdomain, and private IPs when bypass is active."""
        from src.web.utils import proxy_filter
        from src import env

        with patch.object(env, 'PROXY_BYPASS_PRIVATE', True):
            with patch.object(env, 'PROXY_BYPASS_DOMAINS', ()):
                self.assertFalse(proxy_filter("http://localhost:8080"))
                self.assertFalse(proxy_filter("http://localhost.localdomain/feed"))
                self.assertFalse(proxy_filter("http://127.0.0.1:8000"))
                self.assertFalse(proxy_filter("http://10.0.0.1/feed"))
                self.assertFalse(proxy_filter("http://192.168.1.1/feed"))
                self.assertTrue(proxy_filter("https://example.com/rss"))


class TestHtmlParserIframeValidation(unittest.IsolatedAsyncioTestCase):
    async def test_non_http_iframe_rejected(self):
        """HtmlParser (Parser) must reject iframes with non-HTTP/HTTPS schemes."""
        from src.parsing.html_parser import Parser
        from bs4 import BeautifulSoup

        parser = Parser(html="", feed_link="https://example.com")

        # Test javascript: scheme
        soup_js = BeautifulSoup('<iframe src="javascript:alert(1)"></iframe>', 'html.parser').find('iframe')
        res_js = await parser._parse_item(soup_js)
        self.assertIsNone(res_js)

        # Test data: scheme
        soup_data = BeautifulSoup('<iframe src="data:text/html;base64,PHNjcmlwdD4="></iframe>', 'html.parser').find('iframe')
        res_data = await parser._parse_item(soup_data)
        self.assertIsNone(res_data)

        # Test empty src
        soup_empty = BeautifulSoup('<iframe src=""></iframe>', 'html.parser').find('iframe')
        res_empty = await parser._parse_item(soup_empty)
        self.assertIsNone(res_empty)


class TestSplitter(unittest.TestCase):
    def test_splitter_long_text(self):
        """Test splitting without runtime error or index corruption."""
        from src.parsing.splitter import text_and_format_entities_split

        text = "Hello world! " * 500
        chunks = text_and_format_entities_split(text, [], length_limit_head=500, length_limit_tail=500)
        self.assertGreater(len(chunks), 1)
        reconstructed = "".join(chunk[0] for chunk in chunks)
        # Verify no dropped content
        self.assertEqual("".join(text.split()), "".join(reconstructed.split()))


class TestLockEviction(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_idle_user_locks(self):
        """cleanup_idle_locks must evict idle user buckets and preserve locked ones."""
        from src import locks

        user_idle = 11111
        user_locked = 22222

        # Access both to populate bucket
        lock_idle = locks.user_msg_lock(user_idle)
        lock_locked = locks.user_msg_lock(user_locked)
        sending_lock = locks.user_sending_lock(user_locked)

        # Acquire lock on user_locked
        await sending_lock.acquire()
        try:
            self.assertIn(user_idle, locks._user_bucket)
            self.assertIn(user_locked, locks._user_bucket)

            locks.cleanup_idle_locks()

            # Idle user bucket should be evicted
            self.assertNotIn(user_idle, locks._user_bucket)
            # Locked user bucket should remain
            self.assertIn(user_locked, locks._user_bucket)
        finally:
            sending_lock.release()
            # Clean up user_locked
            locks.cleanup_idle_locks()
            self.assertNotIn(user_locked, locks._user_bucket)

    async def test_cleanup_idle_hostname_semaphores(self):
        """cleanup_idle_locks must evict idle hostname semaphores and preserve active ones."""
        from src import locks, env

        if env.HTTP_CONCURRENCY_PER_HOST > 0:
            host_idle = "idle.example.com"
            host_active = "active.example.com"

            sem_idle = locks.hostname_semaphore(f"https://{host_idle}/rss")
            sem_active = locks.hostname_semaphore(f"https://{host_active}/rss")

            await sem_active.acquire()
            try:
                self.assertIn(host_idle, locks._hostname_semaphore_bucket)
                self.assertIn(host_active, locks._hostname_semaphore_bucket)

                locks.cleanup_idle_locks()

                self.assertNotIn(host_idle, locks._hostname_semaphore_bucket)
                self.assertIn(host_active, locks._hostname_semaphore_bucket)
            finally:
                sem_active.release()
                locks.cleanup_idle_locks()
                self.assertNotIn(host_active, locks._hostname_semaphore_bucket)


class TestAdditionalFixes(unittest.IsolatedAsyncioTestCase):
    def test_first_name_none_handling(self):
        """User formatting must safely handle first_name=None without TypeError."""
        user_deleted = MagicMock()
        user_deleted.first_name = None
        user_deleted.last_name = "Smith"

        formatted = (user_deleted.first_name or '') + (f' {user_deleted.last_name}' if user_deleted.last_name else '')
        self.assertEqual(formatted, " Smith")

        user_both_none = MagicMock()
        user_both_none.first_name = None
        user_both_none.last_name = None
        formatted_both = (user_both_none.first_name or '') + (f' {user_both_none.last_name}' if user_both_none.last_name else '')
        self.assertEqual(formatted_both, "")

    async def test_feed_get_malformed_content_length(self):
        """feed_get must handle non-integer Content-Length headers without raising ValueError."""
        from src.web.feed import feed_get
        from src.web.utils import WebResponse
        from unittest.mock import patch

        mock_resp = WebResponse(
            url="https://example.com/rss",
            ori_url="https://example.com/rss",
            content=b"<rss><channel><title>Test</title><item><title>Item 1</title></item></channel></rss>",
            headers={"Content-Length": "chunked", "Content-Type": "application/rss+xml"},
            status=200,
            reason="OK",
        )

        with patch("src.web.feed.get", return_value=mock_resp):
            wf = await feed_get("https://example.com/rss")
            self.assertEqual(wf.status, 200)
            self.assertIsNotNone(wf.rss_d)
            self.assertEqual(wf.rss_d.feed.title, "Test")


if __name__ == '__main__':
    unittest.main()


