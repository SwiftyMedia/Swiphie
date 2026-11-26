from __future__ import annotations

import unittest

from swiphie.discovery import (
    candidate_for_hash,
    player_url_for_hash,
)
from swiphie.player_source import normalize_player_url


class DiscoveryTests(unittest.TestCase):
    def test_candidate_url_is_ias_en_gb(self) -> None:
        candidate = candidate_for_hash("16ee6936", "configured")
        self.assertEqual(
            candidate.player_url,
            "https://www.youtube.com/s/player/16ee6936/"
            "player_ias.vflset/en_GB/base.js",
        )

    def test_normalizes_es6_url(self) -> None:
        url = (
            "https://www.youtube.com/s/player/16ee6936/"
            "player_es6.vflset/en_US/base.js"
        )
        self.assertEqual(
            normalize_player_url(url),
            "https://www.youtube.com/s/player/16ee6936/"
            "player_ias.vflset/en_GB/base.js",
        )

    def test_player_url_for_hash(self) -> None:
        self.assertIn(
            "/player_ias.vflset/en_GB/base.js",
            player_url_for_hash("16ee6936"),
        )


if __name__ == "__main__":
    unittest.main()
