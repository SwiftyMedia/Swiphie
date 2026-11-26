"""Hashing helpers shared by discovery, derivation and the registry."""

from __future__ import annotations

import hashlib


def sha256_hex(value: str | bytes) -> str:
    """Return the SHA-256 digest of ``value`` as lowercase hex."""

    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def content_alias(player_js: str) -> str:
    """Return the 8-hex content alias.

    The first 10,000 characters of player JS are hashed with MD5 and the first
    four bytes are kept. Player files are ASCII in practice, so
    slicing Python text is equivalent to JavaScript's UTF-16 ``slice``.
    """

    content = player_js[:10_000].encode("utf-8", errors="surrogatepass")
    try:
        digest = hashlib.md5(content, usedforsecurity=False).digest()
    except TypeError:  # pragma: no cover - very old/odd OpenSSL builds
        digest = hashlib.md5(content).digest()
    return digest[:4].hex()
