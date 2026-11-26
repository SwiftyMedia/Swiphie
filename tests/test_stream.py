from __future__ import annotations

import unittest

from swiphie.credentials import InnertubeCred, sapisid_hash
from swiphie.stream import (
    StreamSettings,
    build_player_headers,
    build_player_request,
    is_google_video_playback_url,
    probe_cdn_stream,
)


class StreamTests(unittest.TestCase):
    def test_google_video_urls(self) -> None:
        self.assertTrue(
            is_google_video_playback_url(
                "https://rr1.googlevideo.com/videoplayback?n=abc"
            )
        )
        self.assertFalse(
            is_google_video_playback_url(
                "https://rr1.googlevideo.com/watch?n=abc"
            )
        )
        self.assertFalse(
            is_google_video_playback_url("http://rr1.googlevideo.com/videoplayback")
        )

    def test_builds_player_request(self) -> None:
        request = build_player_request(12345, "dQw4w9WgXcQ", None, "1.2.3")
        self.assertEqual(request["videoId"], "dQw4w9WgXcQ")
        self.assertEqual(
            request["playbackContext"]["contentPlaybackContext"]["signatureTimestamp"],
            12345,
        )

    def test_sapisid_hash(self) -> None:
        self.assertIsNone(sapisid_hash(""))
        value = sapisid_hash("SAPISID=abc; other=1")
        self.assertIsNotNone(value)
        assert value is not None
        self.assertTrue(value.startswith("SAPISIDHASH "))

    def test_headers_include_auth(self) -> None:
        cred = InnertubeCred(cookie="SAPISID=abc", visitor_data="visitor")
        headers = build_player_headers(cred, StreamSettings())
        self.assertEqual(headers["X-Goog-Visitor-Id"], "visitor")
        self.assertIn("Authorization", headers)

    def test_cdn_rejects_non_google_url_without_network(self) -> None:
        result = probe_cdn_stream("https://example.com/videoplayback")
        self.assertFalse(result.valid)
        self.assertEqual(result.status, "ERR:invalid CDN URL")


if __name__ == "__main__":
    unittest.main()
