"""Optional Innertube credentials for authenticated validation.

Guest WEB_REMIX is enough most of the time.  A cookie is only used as a
fallback when YouTube blocks the guest player response.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from .config import Config
from .http import HttpError, fetch_text

ORIGIN = "https://music.youtube.com"

LABELS = {
    "INNERTUBE COOKIE": "cookie",
    "VISITOR DATA": "visitor_data",
    "DATASYNC ID": "datasync_id",
}


@dataclass
class InnertubeCred:
    cookie: str = ""
    visitor_data: str = ""
    datasync_id: str = ""
    source: str = "env"


def parse_cookie_file(text: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\*\*\*\s*(.+?)\s*\*\*\*\s*=(.*)$", line)
        if not match:
            continue
        field = LABELS.get(match.group(1).strip().upper())
        if field:
            output[field] = match.group(2).strip()
    return output


def load_innertube_cred(config: Config) -> InnertubeCred:
    cookie_file = config.cookie_file or Path("innertube_cookie.txt")
    from_file: dict[str, str] = {}
    source = "env"
    try:
        from_file = parse_cookie_file(cookie_file.read_text(encoding="utf-8"))
        source = str(cookie_file)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"cred: failed to read {cookie_file}: {exc}")

    import os

    cookie = os.environ.get("YT_COOKIE") or from_file.get("cookie", "")
    visitor_data = (
        os.environ.get("YT_VISITOR_DATA") or from_file.get("visitor_data", "")
    )
    datasync_id = os.environ.get("YT_DATASYNC_ID") or from_file.get("datasync_id", "")

    cred_url = os.environ.get("CRED_URL")
    if not cookie and not visitor_data and cred_url:
        try:
            text = fetch_text(cred_url, timeout=config.fetch_timeout, max_bytes=64 * 1024)
            payload = json.loads(text)
            if isinstance(payload, dict):
                cookie = str(payload.get("cookie") or "")
                visitor_data = str(payload.get("visitorData") or "")
                datasync_id = str(payload.get("dataSyncId") or "")
                source = cred_url
        except (HttpError, json.JSONDecodeError) as exc:
            print(f"cred: CRED_URL fetch failed: {exc}")

    return InnertubeCred(
        cookie=cookie,
        visitor_data=decode_visitor_data(visitor_data),
        datasync_id=datasync_id,
        source=source,
    )


def decode_visitor_data(value: str) -> str:
    if value and re.search(r"%[0-9A-Fa-f]{2}", value):
        try:
            return unquote(value)
        except ValueError:
            return value
    return value


def sapisid_hash(cookie: str) -> str | None:
    match = re.search(r"(?:^|; )SAPISID=([^;]+)", cookie)
    if not match:
        return None
    timestamp = int(time.time())
    digest = hashlib.sha1(
        f"{timestamp} {match.group(1)} {ORIGIN}".encode("utf-8")
    ).hexdigest()
    return f"SAPISIDHASH {timestamp}_{digest}"


def describe_cred(cred: InnertubeCred) -> str:
    def yes_no(value: str) -> str:
        return "yes" if value else "NO"

    visitor = cred.visitor_data[:14] + "…" if cred.visitor_data else ""
    datasync = cred.datasync_id[:8] + "…" if cred.datasync_id else "(none)"
    return (
        f"cookie={yes_no(cred.cookie)} "
        f"SAPISID={'yes' if 'SAPISID=' in cred.cookie else 'NO'} "
        f"visitorData={visitor} dataSyncId={datasync} [{cred.source}]"
    )
