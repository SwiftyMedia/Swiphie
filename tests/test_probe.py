from __future__ import annotations

import unittest

from swiphie.deriver import CandidatePair
from swiphie.probe import _dedupe_pairs, _merge_aliases, _registry_needs_update
from swiphie.registry import empty_registry, update_registry_entry

SHA = "b" * 64
URL = "https://www.youtube.com/s/player/16ee6936/player_ias.vflset/en_GB/base.js"


class ProbeHelperTests(unittest.TestCase):
    def test_registry_needs_update(self) -> None:
        registry = empty_registry()
        self.assertTrue(
            _registry_needs_update(
                registry,
                None,
                player_hash="16ee6936",
                player_url=URL,
                sha256=SHA,
                config_path="registry/player_configs.json",
                update_current=True,
                config_changed=False,
            )
        )

        update_registry_entry(
            registry,
            "16ee6936",
            URL,
            SHA,
            "registry/player_configs.json",
            now="2026-11-26T00:00:00Z",
        )
        existing = registry.players[0]
        self.assertFalse(
            _registry_needs_update(
                registry,
                existing,
                player_hash="16ee6936",
                player_url=URL,
                sha256=SHA,
                config_path="registry/player_configs.json",
                update_current=True,
                config_changed=False,
            )
        )
        self.assertTrue(
            _registry_needs_update(
                registry,
                existing,
                player_hash="16ee6936",
                player_url=URL,
                sha256=SHA,
                config_path="registry/player_configs.json",
                update_current=True,
                config_changed=True,
            )
        )

    def test_merge_aliases_excludes_primary(self) -> None:
        self.assertEqual(
            _merge_aliases(("aaaaaaa1",), "bbbbbbb2", "ccccccc3", "bbbbbbb2"),
            ("aaaaaaa1", "ccccccc3"),
        )

    def test_dedupe_pairs(self) -> None:
        pairs = [
            CandidatePair("A(1,2,INPUT)", "Ax"),
            CandidatePair("A(1,2,INPUT)", "Ax"),
            CandidatePair("B(1,2,INPUT)", "Bx"),
        ]
        self.assertEqual(
            _dedupe_pairs(pairs),
            [
                CandidatePair("A(1,2,INPUT)", "Ax"),
                CandidatePair("B(1,2,INPUT)", "Bx"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
