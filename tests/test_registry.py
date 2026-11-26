from __future__ import annotations

import unittest

from swiphie.registry import (
    empty_registry,
    extract_player_hash,
    update_registry_entry,
    validate_player_registry,
)

SHA = "a" * 64
URL = "https://www.youtube.com/s/player/16ee6936/player_ias.vflset/en_GB/base.js"


class RegistryTests(unittest.TestCase):
    def test_extracts_hash(self) -> None:
        self.assertEqual(extract_player_hash(URL), "16ee6936")

    def test_round_trip(self) -> None:
        registry = empty_registry()
        update_registry_entry(
            registry,
            "16ee6936",
            URL,
            SHA,
            "registry/player_configs.json",
            now="2026-11-26T00:00:00Z",
        )
        parsed = validate_player_registry(registry.to_dict())
        self.assertEqual(len(parsed.players), 1)
        self.assertEqual(parsed.current["playerHash"], "16ee6936")
        self.assertEqual(parsed.players[0].release_tag, "player-16ee6936")

    def test_rejects_current_missing_from_players(self) -> None:
        registry = empty_registry()
        registry.updated_at = "2026-11-26T00:00:00Z"
        registry.current = {
            "playerHash": "16ee6936",
            "playerUrl": URL,
            "discoveredAt": "2026-11-26T00:00:00Z",
        }
        with self.assertRaises(ValueError):
            validate_player_registry(registry.to_dict())

    def test_rejects_bad_player_url(self) -> None:
        registry = empty_registry()
        update_registry_entry(
            registry,
            "16ee6936",
            URL,
            SHA,
            "registry/player_configs.json",
            now="2026-11-26T00:00:00Z",
        )
        data = registry.to_dict()
        data["players"][0]["playerUrl"] = "https://example.com/base.js"
        with self.assertRaises(ValueError):
            validate_player_registry(data)


if __name__ == "__main__":
    unittest.main()
