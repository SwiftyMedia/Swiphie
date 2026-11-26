from __future__ import annotations

import unittest
from pathlib import Path

from swiphie.deriver import (
    derive_player_config,
    derived_to_entry,
    enumerate_candidate_pairs,
)

PLAYER = (Path(__file__).parent / "fixtures" / "synthetic_player.js").read_text(
    encoding="utf-8"
)


class DeriverTests(unittest.TestCase):
    def test_derives_sts_sig_and_nclass(self) -> None:
        derived = derive_player_config(PLAYER, known_hash="deadbeef")
        self.assertEqual(derived.sts, 12345)
        self.assertEqual(derived.sig, "Yo(2,7910,INPUT)")
        self.assertEqual(derived.n_class, "Xo")
        self.assertTrue(derived.n_class_confirmed)
        self.assertEqual(derived.url_hash, "deadbeef")
        self.assertRegex(derived.md5_alias, r"^[a-f0-9]{8}$")

    def test_derived_entry_contains_alias(self) -> None:
        derived = derive_player_config(PLAYER, known_hash="deadbeef")
        entry = derived_to_entry(derived, "deadbeef")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.sig, "Yo(2,7910,INPUT)")
        self.assertEqual(entry.n_class, "Xo")
        self.assertEqual(entry.sts, 12345)
        self.assertEqual(entry.aliases, (derived.md5_alias,))

    def test_enumerates_candidates(self) -> None:
        candidates = enumerate_candidate_pairs(PLAYER)
        self.assertIn("Yo(2,7910,INPUT)", candidates.sig_exprs)
        self.assertIn("Xo", candidates.n_classes)
        self.assertIn(("Yo(2,7910,INPUT)", "Xo"), [
            (pair.sig, pair.n_class) for pair in candidates.pairs
        ])


if __name__ == "__main__":
    unittest.main()
