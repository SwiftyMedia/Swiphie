"""Swiphie — a remote YouTube player configuration registry.

Swiphie discovers YouTube ``player_ias`` rotations, derives the signature
cipher and ``n``-transform configuration, validates the result against real
CDN streams, and publishes a ``player_configs.json`` table for downstream
consumers.
"""

from __future__ import annotations

__version__ = "0.0.1"

__all__ = ["__version__"]
