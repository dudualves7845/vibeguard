"""Focused tests for dashboard request throttling."""

import unittest
from unittest.mock import patch

from vibeguard.server import RateLimiter


class TestRateLimiter(unittest.TestCase):
    def test_sliding_window_expires_requests_using_monotonic_time(self):
        now = [100.0]
        limiter = RateLimiter(max_requests=2, window_secs=60)

        with patch("vibeguard.server.time.monotonic", side_effect=lambda: now[0]):
            self.assertTrue(limiter.is_allowed("127.0.0.1"))
            self.assertTrue(limiter.is_allowed("127.0.0.1"))
            self.assertFalse(limiter.is_allowed("127.0.0.1"))
            now[0] = 161.0
            self.assertTrue(limiter.is_allowed("127.0.0.1"))

    def test_client_tracking_is_bounded(self):
        limiter = RateLimiter(max_requests=2, window_secs=60, max_clients=2)
        for client in ("client-a", "client-b", "client-c"):
            self.assertTrue(limiter.is_allowed(client))
        self.assertLessEqual(len(limiter.requests), 2)


if __name__ == "__main__":
    unittest.main()
