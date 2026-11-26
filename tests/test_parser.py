from __future__ import annotations

import unittest
from pathlib import Path

from swiphie.parser import (
    build_n_js_expression,
    find_noncanonical_json_number,
    parse_player_configs,
    validate_player_configs_text,
)

FIXTURES = Path(__file__).parent / "fixtures" / "config-parity"


class ParserParityTests(unittest.TestCase):
    def test_accepts_empty_players(self) -> None:
        result = parse_player_configs(
            (FIXTURES / "accept-empty-players.json").read_text(encoding="utf-8")
        )
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.configs, {})

    def test_accepts_entry_with_alias(self) -> None:
        text = (FIXTURES / "accept-entry-with-alias.json").read_text(encoding="utf-8")
        result = parse_player_configs(text)
        self.assertTrue(result.ok, result.reason)
        entry = result.configs["16ee6936"]
        self.assertEqual(entry.sig, "mP(4,155,INPUT)")
        self.assertEqual(entry.n_class, "Yx")
        self.assertEqual(entry.sts, 20613)
        self.assertEqual(entry.aliases, ("ca366632",))
        self.assertIs(result.configs["ca366632"], entry)

    def test_rejects_alias_collision(self) -> None:
        result = parse_player_configs(
            (FIXTURES / "reject-alias-collision.json").read_text(encoding="utf-8")
        )
        self.assertFalse(result.ok)
        self.assertIn("duplicate", result.reason or "")

    def test_rejects_bad_documents(self) -> None:
        names = [
            "reject-malformed.json",
            "reject-players-missing.json",
            "reject-root-array.json",
            "reject-schema-version-future.json",
            "reject-schema-version-missing.json",
            "reject-schema-version-string.json",
            "reject-schema-version-zero.json",
        ]
        for name in names:
            with self.subTest(name=name):
                result = parse_player_configs(
                    (FIXTURES / name).read_text(encoding="utf-8")
                )
                self.assertFalse(result.ok)

    def test_skips_invalid_entry(self) -> None:
        result = parse_player_configs(
            '{"schemaVersion":1,"players":{"deadbeef":{"sig":"bad","nClass":"Yx","sts":1}}}'
        )
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.skipped, ["deadbeef"])

    def test_n_template_matches_golden(self) -> None:
        expected = (FIXTURES / "n-template-Yx.golden").read_text(encoding="utf-8").strip()
        self.assertEqual(build_n_js_expression("Yx"), expected)

    def test_noncanonical_numbers(self) -> None:
        self.assertIsNone(find_noncanonical_json_number('{"sts": 20613}'))
        self.assertEqual(
            find_noncanonical_json_number('{"sts": 1.5}'),
            "1.5",
        )
        self.assertEqual(
            find_noncanonical_json_number('{"sts": 99999999999999999999}'),
            "99999999999999999999",
        )
        self.assertIsNone(find_noncanonical_json_number('{"note": "1.5"}'))

    def test_validate_rejects_skipped_entries(self) -> None:
        with self.assertRaises(ValueError):
            validate_player_configs_text(
                '{"schemaVersion":1,"players":{"deadbeef":{"sig":"bad","nClass":"Yx","sts":1}}}'
            )


if __name__ == "__main__":
    unittest.main()
