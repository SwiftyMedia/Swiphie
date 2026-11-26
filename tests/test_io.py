from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from swiphie.parser import (
    PlayerConfigEntry,
    format_paste_ready_entry,
    merge_player_config_entry,
    read_player_configs_file,
)


class ConfigIOTests(unittest.TestCase):
    def test_merge_sorts_by_sts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "player_configs.json"
            merge_player_config_entry(
                path,
                "bbbbbbbb",
                PlayerConfigEntry("B(2,3,INPUT)", "Bx", 300),
            )
            merge_player_config_entry(
                path,
                "aaaaaaaa",
                PlayerConfigEntry("A(1,2,INPUT)", "Ax", 100),
            )
            file = read_player_configs_file(path)
            self.assertEqual(list(file.players), ["aaaaaaaa", "bbbbbbbb"])

    def test_paste_ready_entry(self) -> None:
        entry = PlayerConfigEntry(
            "mP(4,155,INPUT)",
            "Yx",
            20613,
            ("ca366632",),
        )
        self.assertEqual(
            format_paste_ready_entry("16ee6936", entry),
            '"16ee6936": { "sig": "mP(4,155,INPUT)", "nClass": "Yx", '
            '"sts": 20613, "aliases": ["ca366632"] }',
        )


if __name__ == "__main__":
    unittest.main()
