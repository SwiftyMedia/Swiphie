"""Optional Discord failure notifications for CI."""

from __future__ import annotations

import json

from .config import Config
from .http import HttpError, request
from .probe import ProbeFailure


def notify_probe_failure(config: Config, failure: ProbeFailure) -> bool:
    webhook = config.discord_webhook
    if not webhook:
        return False

    fields = [
        {"name": "Stage", "value": failure.stage, "inline": True},
        {"name": "Player hash", "value": failure.player_hash, "inline": True},
        {"name": "Message", "value": failure.message[:1024]},
    ]
    body = {
        "embeds": [
            {
                "title": "Swiphie player probe failed",
                "color": 0xED4245,
                "fields": fields,
            }
        ]
    }
    try:
        response = request(
            webhook,
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body).encode("utf-8"),
            timeout=5.0,
            max_bytes=64 * 1024,
        )
        return 200 <= response.status < 300
    except HttpError:
        return False
