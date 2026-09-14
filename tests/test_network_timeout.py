from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from kill5 import network
from kill5.errors import CrawlError, ErrorCode


class SlowResponse:
    def __init__(self) -> None:
        self.reads = 0

    def __enter__(self) -> "SlowResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def geturl(self) -> str:
        return "https://example.test/page"

    def read(self, _size: int = -1) -> bytes:
        self.reads += 1
        time.sleep(0.02)
        return b"still streaming" if self.reads == 1 else b""


class NetworkTimeoutTests(unittest.TestCase):
    def test_streaming_response_obeys_target_deadline(self) -> None:
        with (
            patch.object(network, "wait_for_host_slot"),
            patch.object(network, "urlopen", return_value=SlowResponse()),
            self.assertRaises(CrawlError) as raised,
        ):
            with network.target_deadline(0.01):
                network.fetch_bytes("https://example.test/page")

        self.assertEqual(raised.exception.code, ErrorCode.NETWORK_TIMEOUT)
        self.assertFalse(raised.exception.retryable)


if __name__ == "__main__":
    unittest.main()
