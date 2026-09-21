import unittest
from unittest.mock import patch
from aiohttp.test_utils import make_mocked_request

from src import env, db
from src.errors_collection import SSRFBlockedError
from src.web.utils import is_private_ip
from src.web import req
from src.redirect_server import healthz, readyz
from src.command.utils import CommandRateLimiter


class TestDatabasePragmas(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def tearDownClass(cls):
        import os
        from urllib.parse import urlparse
        if env.DATABASE_URL.startswith("sqlite://"):
            parsed = urlparse(env.DATABASE_URL)
            path = parsed.netloc or parsed.path.lstrip("/")
            if path and path != ":memory:" and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    async def test_sqlite_pragmas_applied(self):
        """Verify that SQLite connection applies concurrency and timeout PRAGMAs."""
        await db.init()
        from tortoise import Tortoise
        conn = Tortoise.get_connection("default")
        # Check busy_timeout (should be 15000 ms)
        busy_timeout = await conn.execute_query("PRAGMA busy_timeout;")
        self.assertEqual(busy_timeout[1][0]["timeout"], 15000)
        # Check synchronous mode (NORMAL is 1)
        sync_mode = await conn.execute_query("PRAGMA synchronous;")
        self.assertEqual(sync_mode[1][0]["synchronous"], 1)
        await db.close()


class TestHealthEndpoints(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def tearDownClass(cls):
        import os
        from urllib.parse import urlparse
        if env.DATABASE_URL.startswith("sqlite://"):
            parsed = urlparse(env.DATABASE_URL)
            path = parsed.netloc or parsed.path.lstrip("/")
            if path and path != ":memory:" and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    async def test_healthz_endpoint(self):
        """Test /healthz returns HTTP 200 with status ok."""
        mock_req = make_mocked_request("GET", "/healthz")
        resp = await healthz(mock_req)
        self.assertEqual(resp.status, 200)
        import json
        data = json.loads(resp.text)
        self.assertEqual(data.get("status"), "ok")

    async def test_readyz_endpoint(self):
        """Test /readyz reports database status correctly."""
        await db.init()
        mock_req = make_mocked_request("GET", "/readyz")
        resp = await readyz(mock_req)
        import json
        data = json.loads(resp.text)
        self.assertIn(resp.status, (200, 503))
        self.assertIn("database", data.get("checks", {}))
        self.assertEqual(data["checks"]["database"], "ok")
        await db.close()


class TestSSRFProtection(unittest.IsolatedAsyncioTestCase):
    def test_is_private_ip_detection(self):
        """Test detection of private, loopback, and cloud metadata addresses."""
        self.assertTrue(is_private_ip("127.0.0.1"))
        self.assertTrue(is_private_ip("localhost"))
        self.assertTrue(is_private_ip("localhost.localdomain"))
        self.assertTrue(is_private_ip("::1"))
        self.assertTrue(is_private_ip("10.0.0.1"))
        self.assertTrue(is_private_ip("172.16.0.1"))
        self.assertTrue(is_private_ip("192.168.1.1"))
        self.assertTrue(is_private_ip("169.254.169.254"))  # Cloud metadata IP
        self.assertTrue(is_private_ip("100.64.0.1"))      # CGNAT
        self.assertFalse(is_private_ip("8.8.8.8"))
        self.assertFalse(is_private_ip("1.1.1.1"))
        self.assertFalse(is_private_ip("github.com"))

    async def test_check_ssrf_blocks_private(self):
        """_check_ssrf must raise SSRFBlockedError on private IP when ALLOW_PRIVATE_NETWORKS is False."""
        with patch.object(env, 'ALLOW_PRIVATE_NETWORKS', False):
            with self.assertRaises(SSRFBlockedError):
                await req._check_ssrf("127.0.0.1")
            with self.assertRaises(SSRFBlockedError):
                await req._check_ssrf("169.254.169.254")
            with self.assertRaises(SSRFBlockedError):
                await req._check_ssrf("localhost")

    async def test_check_ssrf_allows_when_enabled(self):
        """_check_ssrf must permit private IP when ALLOW_PRIVATE_NETWORKS is True."""
        with patch.object(env, 'ALLOW_PRIVATE_NETWORKS', True):
            # Should not raise exception
            await req._check_ssrf("127.0.0.1")
            await req._check_ssrf("169.254.169.254")


class TestCommandRateLimiter(unittest.TestCase):
    def test_rate_limiter_allows_up_to_limit(self):
        """Limiter permits calls up to the limit and throttles subsequent requests."""
        limiter = CommandRateLimiter(limit=3, window_seconds=2.0)
        chat_id, user_id = 12345, 67890

        self.assertTrue(limiter.is_allowed(chat_id, user_id))
        self.assertTrue(limiter.is_allowed(chat_id, user_id))
        self.assertTrue(limiter.is_allowed(chat_id, user_id))
        # 4th call within window should be throttled
        self.assertFalse(limiter.is_allowed(chat_id, user_id))

        # Different user should not be throttled
        self.assertTrue(limiter.is_allowed(chat_id, 99999))

    def test_cleanup_idle_entries(self):
        """cleanup_idle removes expired user records."""
        limiter = CommandRateLimiter(limit=2, window_seconds=0.01)
        limiter.is_allowed(1, 1)
        import time
        time.sleep(0.02)
        limiter.cleanup_idle()
        self.assertEqual(len(limiter._records), 0)


if __name__ == '__main__':
    unittest.main()
