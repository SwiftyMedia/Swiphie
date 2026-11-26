from __future__ import annotations

import unittest
from unittest.mock import patch

from swiphie.config import Config, ConfigError


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            config = Config.from_env()
        self.assertGreaterEqual(len(config.validation_video_ids), 2)
        self.assertEqual(config.player_configs_path.as_posix(), "registry/player_configs.json")

    def test_rejects_single_validation_video(self) -> None:
        with patch.dict(
            "os.environ",
            {"SWIPHIE_VALIDATION_VIDEO_IDS": "dQw4w9WgXcQ"},
            clear=True,
        ):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_rejects_bad_hash(self) -> None:
        with patch.dict(
            "os.environ",
            {"SWIPHIE_PLAYER_HASHES": "not-a-hash"},
            clear=True,
        ):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_public_dict_hides_webhook(self) -> None:
        config = Config(discord_webhook="https://secret")
        self.assertNotIn("https://secret", str(config.as_public_dict()))


if __name__ == "__main__":
    unittest.main()
