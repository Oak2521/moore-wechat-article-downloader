#!/usr/bin/env python3
"""mitmproxy addon: passively capture Douyin aweme lists from the user's own
client traffic.

Loaded via: mitmdump -s douyin_capture_addon.py

It matches only the known aweme list/detail endpoints, parses the JSON the
client already received, and appends normalized, scrubbed records to the
session capture file. It never writes or logs cookies, tokens, msToken, or
signatures — only public aweme fields and a small safe status marker.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from douyin_downloader import (  # noqa: E402
    append_captured,
    load_captured,
    match_endpoint,
    parse_aweme_payload,
    runtime_dir,
    utc_now,
    write_json,
)


class DouyinCaptureAddon:
    def __init__(self) -> None:
        self.base = runtime_dir(os.environ.get("MOORE_DOUYIN_RUNTIME_DIR", ""))
        self.session_id = os.environ.get("MOORE_DOUYIN_SESSION_ID", "")
        try:
            self.limit = int(os.environ.get("MOORE_DOUYIN_CAPTURE_LIMIT", "1000"))
        except ValueError:
            self.limit = 1000

    def _status_path(self) -> Path:
        return self.base / "proxy" / f"{self.session_id}.capture-status.json"

    def _write_status(self, total: int, last_added: int, last_source: str) -> None:
        write_json(
            self._status_path(),
            {
                "session_id": self.session_id,
                "captured_count": total,
                "last_added": last_added,
                "last_source": last_source,
                "updated_at": utc_now(),
                "ready": total > 0,
            },
        )

    def response(self, flow) -> None:  # noqa: ANN001 - mitmproxy flow object
        if not self.session_id:
            return
        url = flow.request.pretty_url
        source = match_endpoint(url)
        if not source:
            return
        content_type = flow.response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            return
        try:
            payload = json.loads(flow.response.get_text())
        except (ValueError, TypeError):
            return
        records = parse_aweme_payload(payload, source=source)
        if not records:
            return
        added = append_captured(self.base, self.session_id, records)
        total = len(load_captured(self.base, self.session_id))
        self._write_status(total, added, source)


addons = [DouyinCaptureAddon()]
