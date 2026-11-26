"""Run untrusted player.js through the Node cipher helper.

The helper is dependency-free and only receives the data it needs.  It runs in
a separate process with a timeout and no credentials in its environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .parser import NCLASS_RE, SIG_RE

MAX_OUTPUT_BYTES = 64 * 1024
MAX_ERROR_BYTES = 32 * 1024


class CipherError(RuntimeError):
    """Cipher evaluation could not be completed."""


@dataclass
class CipherNProbe:
    in_value: str | None = None
    out: str | None = None
    changed: bool | None = None
    valid: bool | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "in": self.in_value,
            "out": self.out,
            "changed": self.changed,
            "valid": self.valid,
            "error": self.error,
        }


@dataclass
class CipherEvaluation:
    init_error: str | None
    injection_mode: str
    sig_available: bool
    n_available: bool
    n_probe: CipherNProbe
    urls: list[str] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "initError": self.init_error,
            "injectionMode": self.injection_mode,
            "sigAvailable": self.sig_available,
            "nAvailable": self.n_available,
            "nProbe": self.n_probe.to_dict(),
            "urls": self.urls,
            "error": self.error,
        }


def evaluate_cipher_candidate(
    config: Config,
    *,
    player_js: str,
    sig_expr: str,
    n_class: str,
    signature_ciphers: list[str],
    timeout: float | None = None,
) -> CipherEvaluation:
    """Evaluate one candidate pair and deobfuscate the supplied ciphers."""

    if not SIG_RE.fullmatch(sig_expr):
        raise CipherError(f"unsafe sig expression: {sig_expr!r}")
    if not NCLASS_RE.fullmatch(n_class):
        raise CipherError(f"unsafe nClass: {n_class!r}")
    if not signature_ciphers:
        raise CipherError("at least one signatureCipher is required")

    node = shutil.which(config.node_binary)
    if node is None:
        raise CipherError(
            f"Node.js executable not found: {config.node_binary!r} "
            "(install Node 20+ or set SWIPHIE_NODE)"
        )

    runner = Path(__file__).with_name("js") / "cipher_runner.mjs"
    if not runner.exists():
        raise CipherError(f"cipher runner missing: {runner}")

    request = {
        "playerJs": player_js,
        "sigExpr": sig_expr,
        "nClass": n_class,
        "signatureCiphers": signature_ciphers,
    }
    timeout_seconds = timeout if timeout is not None else config.runtime_timeout
    # Keep credentials and unrelated host settings out of the child process
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "NODE_OPTIONS": "--max-old-space-size=256",
    }

    try:
        completed = subprocess.run(
            [node, str(runner)],
            input=json.dumps(request, separators=(",", ":")).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
            env=env,
            cwd=str(runner.parent),
        )
    except subprocess.TimeoutExpired as exc:
        raise CipherError(
            f"cipher runtime timed out after {timeout_seconds:.1f}s"
        ) from exc
    except OSError as exc:
        raise CipherError(f"could not start Node cipher runner: {exc}") from exc

    if len(completed.stdout) > MAX_OUTPUT_BYTES:
        raise CipherError(
            f"cipher output exceeded {MAX_OUTPUT_BYTES} bytes"
        )
    if completed.returncode != 0:
        stderr = completed.stderr[:MAX_ERROR_BYTES].decode("utf-8", errors="replace")
        raise CipherError(
            f"cipher process exited {completed.returncode}"
            + (f": {stderr.strip()}" if stderr.strip() else "")
        )

    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CipherError(f"cipher runner returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CipherError("cipher runner returned a non-object")

    probe = payload.get("nProbe") or {}
    return CipherEvaluation(
        init_error=_optional_string(payload.get("initError")),
        injection_mode=str(payload.get("injectionMode") or "global-fallback"),
        sig_available=bool(payload.get("sigAvailable")),
        n_available=bool(payload.get("nAvailable")),
        n_probe=CipherNProbe(
            in_value=_optional_string(probe.get("in")),
            out=_optional_string(probe.get("out")),
            changed=_optional_bool(probe.get("changed")),
            valid=_optional_bool(probe.get("valid")),
            error=_optional_string(probe.get("error")),
        ),
        urls=_optional_string_list(payload.get("urls")),
        error=_optional_string(payload.get("error")),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return list(value)
