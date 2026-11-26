from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from swiphie.cipher import CipherError, evaluate_cipher_candidate
from swiphie.config import Config

PLAYER = (Path(__file__).parent / "fixtures" / "synthetic_player.js").read_text(
    encoding="utf-8"
)
CIPHER = (
    "s=YWJj&sp=sig&url=https%3A%2F%2Frr1.googlevideo.com"
    "%2Fvideoplayback%3Fn%3Dxyz%26foo%3D1"
)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for cipher tests")
class CipherTests(unittest.TestCase):
    def test_evaluates_and_deobfuscates(self) -> None:
        evaluation = evaluate_cipher_candidate(
            Config(),
            player_js=PLAYER,
            sig_expr="Yo(2,7910,INPUT)",
            n_class="Xo",
            signature_ciphers=[CIPHER],
        )
        self.assertIsNone(evaluation.init_error)
        self.assertTrue(evaluation.sig_available)
        self.assertTrue(evaluation.n_available)
        self.assertTrue(evaluation.n_probe.valid)
        self.assertIsNotNone(evaluation.urls)
        assert evaluation.urls is not None
        self.assertIn("sig=jJWY", evaluation.urls[0])
        self.assertIn("n=zyx", evaluation.urls[0])

    def test_rejects_unsafe_expressions(self) -> None:
        with self.assertRaises(CipherError):
            evaluate_cipher_candidate(
                Config(),
                player_js=PLAYER,
                sig_expr="alert(1)",
                n_class="Xo",
                signature_ciphers=[CIPHER],
            )
        with self.assertRaises(CipherError):
            evaluate_cipher_candidate(
                Config(),
                player_js=PLAYER,
                sig_expr="Yo(2,7910,INPUT)",
                n_class="g.evil()",
                signature_ciphers=[CIPHER],
            )


if __name__ == "__main__":
    unittest.main()
