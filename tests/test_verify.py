from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from swiphie.config import Config
from swiphie.parser import PlayerConfigEntry, merge_player_config_entry
from swiphie.registry import (
    empty_registry,
    update_registry_entry,
    write_registry_atomic,
    write_release_record,
)
from swiphie.verify import verify_artifacts

SHA = "c" * 64
URL = "https://www.youtube.com/s/player/16ee6936/player_ias.vflset/en_GB/base.js"


class VerifyTests(unittest.TestCase):
    def _config(self, root: Path) -> Config:
        return Config(
            player_configs_path=root / "player_configs.json",
            player_registry_path=root / "player_registry.json",
            releases_dir=root / "releases",
        )

    def _seed(self, root: Path) -> Config:
        config = self._config(root)
        merge_player_config_entry(
            config.player_configs_path,
            "16ee6936",
            PlayerConfigEntry("mP(4,155,INPUT)", "Yx", 20613, ("ca366632",)),
        )
        registry = empty_registry()
        update_registry_entry(
            registry,
            "16ee6936",
            URL,
            SHA,
            str(config.player_configs_path),
            now="2026-11-26T00:00:00Z",
        )
        write_release_record(
            config.releases_dir,
            "16ee6936",
            URL,
            SHA,
            generated_at="2026-11-26T00:00:00Z",
        )
        write_registry_atomic(config.player_registry_path, registry)
        return config

    def test_accepts_consistent_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = self._seed(Path(temp))
            self.assertEqual(verify_artifacts(config), [])

    def test_reports_missing_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._seed(root)
            (root / "releases" / "16ee6936.json").unlink()
            errors = verify_artifacts(config)
            self.assertTrue(any("release record" in error for error in errors))

    def test_reports_registry_entry_missing_from_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._seed(root)
            merge_player_config_entry(
                config.player_configs_path,
                "deadbeef",
                PlayerConfigEntry("mP(4,155,INPUT)", "Yx", 20613),
            )
            # Replace the table with one that no longer has the registry hash.
            config.player_configs_path.write_text(
                '{"schemaVersion":1,"players":{"deadbeef":'
                '{"sig":"mP(4,155,INPUT)","nClass":"Yx","sts":20613}}}',
                encoding="utf-8",
            )
            errors = verify_artifacts(config)
            self.assertTrue(any("missing from" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
