#!/usr/bin/env python3
"""Local Douyin (抖音) video downloader runtime.

Goals:
- batch download a creator's public posts, or the user's own 喜欢 / 收藏 lists,
  in HD and without the visible watermark
- deliver clean local files: mp4/images + cover + metadata + index.csv
- prefer passive capture (mitmproxy reads the user's own client traffic) over
  forging Douyin's anti-bot signatures

This script intentionally uses only the Python standard library. The proxy
capture mode additionally needs `mitmproxy` installed on the machine.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import random
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

APP_DIR = Path.home() / ".moore" / "douyin-video-downloader"
DEFAULT_DELIVERY_DIR = Path.home() / "Downloads" / "douyin-videos"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

MAX_MEDIA_BYTES = 500 * 1024 * 1024  # 500 MB per asset safety cap

# Query keys that can carry credentials / anti-bot signatures. We never persist
# these in metadata, session files, or share links.
SENSITIVE_QUERY_KEYS = {
    "mstoken",
    "mston",
    "x-bogus",
    "a_bogus",
    "_signature",
    "sec_signature",
    "ttwid",
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "uid_tt",
    "passport_csrf_token",
    "odin_tt",
    "cookie",
    "token",
    "ticket",
    "sec_user_id_token",
    "verifyfp",
    "s_v_web_id",
}

# (source_tag, url_substring) — endpoints whose JSON responses carry aweme lists.
ENDPOINT_PATTERNS: list[tuple[str, str]] = [
    ("post", "/aweme/v1/web/aweme/post/"),
    ("post", "/aweme/v1/aweme/post/"),
    ("favorite", "/aweme/v1/web/aweme/favorite/"),
    ("favorite", "/aweme/v1/aweme/favorite/"),
    ("collection", "/aweme/v1/web/aweme/listcollection/"),
    ("collection", "/aweme/v1/web/aweme/collection/"),
    ("collection", "/aweme/v1/web/aweme/list/collection/"),
    ("mix", "/aweme/v1/web/mix/aweme/"),
    ("mix", "/aweme/v1/web/mix/aweme"),
    ("detail", "/aweme/v1/web/aweme/detail/"),
]

VIDEO_ID_RE = re.compile(r"(?:/video/|/note/|modal_id=|/share/video/)(\d+)")
SHORT_LINK_RE = re.compile(r"https?://v\.douyin\.com/[\w-]+", re.I)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def runtime_dir(explicit: str = "") -> Path:
    base = Path(explicit).expanduser() if explicit else Path(
        os.environ.get("MOORE_DOUYIN_RUNTIME_DIR", str(APP_DIR))
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def make_session_id() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"dy-{stamp}-{random.randint(1000, 9999)}"


def safe_name(text: str, limit: int = 60) -> str:
    text = str(text or "").strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    if not text:
        text = "untitled"
    return text[:limit].strip()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def write_json_response(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def scrub_url(url: str) -> str:
    """Remove credential/signature query params from a URL for safe storage."""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return ""
    if not parts.scheme:
        return str(url or "")
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in SENSITIVE_QUERY_KEYS
    ]
    query = urllib.parse.urlencode(kept)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def clean_share_url(aweme_id: str) -> str:
    aweme_id = str(aweme_id or "").strip()
    if not aweme_id:
        return ""
    return f"https://www.douyin.com/video/{aweme_id}"


# --------------------------------------------------------------------------
# aweme parsing (pure, offline-testable)
# --------------------------------------------------------------------------


def extract_aweme_items(payload: Any) -> list[dict[str, Any]]:
    """Pull raw aweme objects out of any known Douyin response shape."""
    items: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for entry in payload:
            items.extend(extract_aweme_items(entry))
        return items
    if not isinstance(payload, dict):
        return items
    for key in ("aweme_list", "aweme_details", "aweme_detail_list", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    # some list wrappers put the aweme under "aweme_info"
                    if "aweme_id" in entry or "video" in entry or "images" in entry:
                        items.append(entry)
                    elif isinstance(entry.get("aweme_info"), dict):
                        items.append(entry["aweme_info"])
    for key in ("aweme_detail", "aweme_info"):
        value = payload.get(key)
        if isinstance(value, dict):
            items.append(value)
    return items


def _first_url(url_obj: Any) -> str:
    if isinstance(url_obj, dict):
        url_list = url_obj.get("url_list")
        if isinstance(url_list, list):
            for u in url_list:
                if isinstance(u, str) and u.startswith("http"):
                    return u
    return ""


def best_video_url(video: Any, quality: str = "best") -> tuple[str, list[str]]:
    """Return (best_url, ordered_candidates), watermark-free play addresses."""
    if not isinstance(video, dict):
        return "", []
    candidates: list[str] = []

    bit_rates = video.get("bit_rate")
    if quality == "best" and isinstance(bit_rates, list) and bit_rates:
        ranked = sorted(
            [b for b in bit_rates if isinstance(b, dict)],
            key=lambda b: (b.get("bit_rate") or 0),
            reverse=True,
        )
        for entry in ranked:
            candidates.append(_first_url(entry.get("play_addr")))

    # play_addr variants (all are non-watermark playback addresses)
    for key in ("play_addr", "play_addr_h264", "play_addr_265", "play_addr_lowbr"):
        candidates.append(_first_url(video.get(key)))

    seen: set[str] = set()
    ordered: list[str] = []
    for url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return (ordered[0] if ordered else ""), ordered


def extract_image_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    images = item.get("images")
    if isinstance(images, list):
        for img in images:
            if not isinstance(img, dict):
                continue
            url = _first_url(img)
            if url:
                urls.append(url)
    return urls


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_aweme(item: dict[str, Any], source: str = "", quality: str = "best") -> dict[str, Any]:
    """Normalize one raw aweme object into a stable, scrubbed record."""
    if not isinstance(item, dict):
        item = {}
    aweme_id = str(item.get("aweme_id") or item.get("awemeId") or "").strip()
    desc = str(item.get("desc") or "").strip()
    create_time = _to_int(item.get("create_time"))
    create_date = ""
    if create_time:
        create_date = dt.datetime.fromtimestamp(create_time, dt.timezone.utc).strftime("%Y-%m-%d")

    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    music = item.get("music") if isinstance(item.get("music"), dict) else {}

    image_urls = extract_image_urls(item)
    is_image = bool(image_urls) or _to_int(item.get("aweme_type")) == 68
    video_url, video_candidates = ("", [])
    cover_url = ""
    if not is_image:
        video_url, video_candidates = best_video_url(video, quality)
        cover_url = _first_url(video.get("cover")) or _first_url(video.get("origin_cover")) or _first_url(video.get("dynamic_cover"))
    else:
        cover_url = _first_url(video.get("cover")) if video else ""

    return {
        "aweme_id": aweme_id,
        "desc": desc,
        "create_time": create_time,
        "create_date": create_date,
        "aweme_type": "image" if is_image else "video",
        "author_nickname": str(author.get("nickname") or "").strip(),
        "author_sec_uid": str(author.get("sec_uid") or "").strip(),
        "author_unique_id": str(author.get("unique_id") or author.get("short_id") or "").strip(),
        "video_url": video_url,
        "video_candidates": video_candidates,
        "cover_url": cover_url,
        "image_urls": image_urls,
        "duration_ms": _to_int(video.get("duration")) if video else 0,
        "statistics": {
            "digg_count": _to_int(stats.get("digg_count")),
            "comment_count": _to_int(stats.get("comment_count")),
            "share_count": _to_int(stats.get("share_count")),
            "collect_count": _to_int(stats.get("collect_count")),
            "play_count": _to_int(stats.get("play_count")),
        },
        "music": {
            "title": str(music.get("title") or "").strip(),
            "author": str(music.get("author") or "").strip(),
        },
        "source": source,
        "share_url": clean_share_url(aweme_id),
    }


def parse_aweme_payload(payload: Any, source: str = "", quality: str = "best") -> list[dict[str, Any]]:
    records = []
    for raw in extract_aweme_items(payload):
        record = normalize_aweme(raw, source=source, quality=quality)
        if record["aweme_id"]:
            records.append(record)
    return records


def match_endpoint(url: str) -> str | None:
    lowered = str(url or "").lower()
    for source, needle in ENDPOINT_PATTERNS:
        if needle in lowered:
            return source
    return None


# --------------------------------------------------------------------------
# capture session storage
# --------------------------------------------------------------------------


def sessions_dir(base: Path) -> Path:
    return base / "sessions"


def captures_dir(base: Path) -> Path:
    return base / "captures"


def session_path(base: Path, session_id: str) -> Path:
    return sessions_dir(base) / f"{session_id}.json"


def capture_path(base: Path, session_id: str) -> Path:
    return captures_dir(base) / f"{session_id}.jsonl"


def create_session(base: Path, label: str, source: str = "auto") -> dict[str, Any]:
    session_id = make_session_id()
    session = {
        "session_id": session_id,
        "label": label,
        "source": source,
        "created_at": utc_now(),
        "capture": str(capture_path(base, session_id)),
    }
    write_json(session_path(base, session_id), session)
    capture_path(base, session_id).parent.mkdir(parents=True, exist_ok=True)
    capture_path(base, session_id).touch()
    return session


def load_session(base: Path, session_id: str) -> dict[str, Any]:
    path = session_path(base, session_id)
    if not path.exists():
        raise FileNotFoundError(f"session not found: {session_id}")
    return read_json(path)


def append_captured(base: Path, session_id: str, records: list[dict[str, Any]]) -> int:
    """Append newly captured records, de-duped by aweme_id. Returns added count."""
    path = capture_path(base, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids = {rec["aweme_id"] for rec in load_captured(base, session_id)}
    added = 0
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            aweme_id = record.get("aweme_id")
            if not aweme_id or aweme_id in existing_ids:
                continue
            existing_ids.add(aweme_id)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            added += 1
    return added


def load_captured(base: Path, session_id: str) -> list[dict[str, Any]]:
    path = capture_path(base, session_id)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            aweme_id = record.get("aweme_id")
            if not aweme_id or aweme_id in seen:
                continue
            seen.add(aweme_id)
            records.append(record)
    return records


def save_selection(base: Path, session_id: str, records: list[dict[str, Any]]) -> Path:
    path = sessions_dir(base) / f"{session_id}.selected.json"
    write_json(path, records)
    return path


def load_selection(base: Path, session_id: str) -> list[dict[str, Any]]:
    path = sessions_dir(base) / f"{session_id}.selected.json"
    if not path.exists():
        return []
    return read_json(path)


# --------------------------------------------------------------------------
# selection contract
# --------------------------------------------------------------------------


def parse_indices(spec: str) -> list[int]:
    result: list[int] = []
    for chunk in str(spec or "").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            result.append(int(chunk))
    return result


def parse_range(spec: str) -> list[int]:
    spec = str(spec or "").strip()
    match = re.match(r"^(\d+)\s*-\s*(\d+)$", spec)
    if not match:
        return []
    start, end = int(match.group(1)), int(match.group(2))
    if start > end:
        start, end = end, start
    return list(range(start, end + 1))


def apply_selection(
    records: list[dict[str, Any]],
    latest: int = 0,
    indices: str = "",
    ranges: str = "",
    contains: str = "",
) -> list[dict[str, Any]]:
    """Filter records by the selection contract. 1-based indices/ranges."""
    selected = list(records)
    if contains:
        needle = contains.lower()
        selected = [r for r in selected if needle in (r.get("desc") or "").lower()]

    picked_positions = set(parse_indices(indices)) | set(parse_range(ranges))
    if picked_positions:
        selected = [records[i - 1] for i in sorted(picked_positions) if 1 <= i <= len(records)]

    if latest and latest > 0:
        selected = selected[:latest]
    return selected


# --------------------------------------------------------------------------
# HTTP + download pipeline
# --------------------------------------------------------------------------


class DownloadThrottle:
    """Simple jittered pacing between downloads to avoid hammering the CDN."""

    def __init__(self, min_gap: float = 0.4, max_gap: float = 1.2) -> None:
        self.min_gap = min_gap
        self.max_gap = max_gap
        self._last = 0.0

    def wait(self) -> None:
        now = time.time()
        gap = random.uniform(self.min_gap, self.max_gap)
        elapsed = now - self._last
        if self._last and elapsed < gap:
            time.sleep(gap - elapsed)
        self._last = time.time()


def http_get_bytes(url: str, cookie: str = "", timeout: int = 60, max_bytes: int = MAX_MEDIA_BYTES) -> bytes:
    headers = dict(DEFAULT_HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        chunks = []
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"asset exceeds max size ({max_bytes} bytes)")
            chunks.append(chunk)
        return b"".join(chunks)


def http_get_text(url: str, cookie: str = "", timeout: int = 30) -> str:
    return http_get_bytes(url, cookie=cookie, timeout=timeout, max_bytes=20 * 1024 * 1024).decode(
        "utf-8", errors="replace"
    )


def _download_to(path: Path, url: str, cookie: str, candidates: list[str] | None = None) -> tuple[bool, str]:
    urls = [url] + [u for u in (candidates or []) if u and u != url]
    last_error = "no url"
    for candidate in urls:
        if not candidate:
            continue
        try:
            data = http_get_bytes(candidate, cookie=cookie)
            if not data:
                last_error = "empty response"
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return True, candidate
        except Exception as exc:  # noqa: BLE001 - best effort across mirrors
            last_error = str(exc)
            continue
    return False, last_error


def delivery_dir(output_dir: str, label: str) -> Path:
    if output_dir:
        target = Path(output_dir).expanduser()
    else:
        target = DEFAULT_DELIVERY_DIR / safe_name(label or "download", 80)
    target.mkdir(parents=True, exist_ok=True)
    return target


INDEX_FIELDS = [
    "seq",
    "aweme_id",
    "aweme_type",
    "desc",
    "author_nickname",
    "create_date",
    "source",
    "digg_count",
    "share_url",
    "file",
    "status",
]


def download_records(
    records: list[dict[str, Any]],
    output_dir: Path,
    quality: str = "best",
    cookie: str = "",
    throttle: DownloadThrottle | None = None,
) -> dict[str, Any]:
    throttle = throttle or DownloadThrottle()
    rows: list[dict[str, Any]] = []
    success = 0
    failed: list[dict[str, Any]] = []

    for seq, record in enumerate(records, 1):
        throttle.wait()
        aweme_id = record.get("aweme_id") or f"item{seq}"
        safe = safe_name(record.get("desc") or aweme_id)
        status = "success"
        rel_file = ""

        if record.get("aweme_type") == "image":
            image_urls = record.get("image_urls") or []
            img_dir = output_dir / "images" / f"{seq:03d}"
            ok_any = False
            for idx, img_url in enumerate(image_urls, 1):
                ok, _ = _download_to(img_dir / f"{idx:02d}.jpg", img_url, cookie)
                ok_any = ok_any or ok
            if ok_any:
                rel_file = str((Path("images") / f"{seq:03d}"))
            else:
                status = "failed"
        else:
            video_url = record.get("video_url") or ""
            candidates = record.get("video_candidates") or []
            if not video_url and candidates:
                video_url = candidates[0]
            filename = f"{seq:03d}-{aweme_id}-{safe}.mp4"
            ok, used = _download_to(output_dir / "videos" / filename, video_url, cookie, candidates)
            if ok:
                rel_file = str(Path("videos") / filename)
            else:
                status = "failed"

        # cover (best-effort, does not affect success)
        cover_url = record.get("cover_url")
        if cover_url:
            _download_to(output_dir / "covers" / f"{seq:03d}.jpg", cover_url, cookie)

        # scrubbed metadata
        meta = dict(record)
        meta["video_url"] = scrub_url(meta.get("video_url", ""))
        meta["video_candidates"] = [scrub_url(u) for u in meta.get("video_candidates", [])]
        meta["cover_url"] = scrub_url(meta.get("cover_url", ""))
        meta["image_urls"] = [scrub_url(u) for u in meta.get("image_urls", [])]
        write_json(output_dir / "meta" / f"{seq:03d}.json", meta)

        if status == "success":
            success += 1
        else:
            failed.append({"aweme_id": aweme_id, "desc": record.get("desc", "")})

        rows.append(
            {
                "seq": seq,
                "aweme_id": aweme_id,
                "aweme_type": record.get("aweme_type", ""),
                "desc": record.get("desc", ""),
                "author_nickname": record.get("author_nickname", ""),
                "create_date": record.get("create_date", ""),
                "source": record.get("source", ""),
                "digg_count": record.get("statistics", {}).get("digg_count", 0),
                "share_url": record.get("share_url", ""),
                "file": rel_file,
                "status": status,
            }
        )

    write_index_csv(output_dir, rows)
    return {
        "ok": True,
        "output_dir": str(output_dir),
        "index_csv": str(output_dir / "index.csv"),
        "total": len(records),
        "success_count": success,
        "failure_count": len(failed),
        "failed": failed,
    }


def write_index_csv(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "index.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# --------------------------------------------------------------------------
# best-effort share-URL resolution (secondary path)
# --------------------------------------------------------------------------


def resolve_aweme_id(url_or_id: str, cookie: str = "") -> str:
    value = str(url_or_id or "").strip()
    if value.isdigit():
        return value
    match = VIDEO_ID_RE.search(value)
    if match:
        return match.group(1)
    short = SHORT_LINK_RE.search(value)
    if short:
        try:
            req = urllib.request.Request(short.group(0), headers=DEFAULT_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                final = resp.geturl()
            match = VIDEO_ID_RE.search(final)
            if match:
                return match.group(1)
        except Exception:  # noqa: BLE001 - best effort
            return ""
    return ""


def extract_router_data(html: str) -> dict[str, Any] | None:
    match = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\});", html, re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def fetch_aweme_from_web(aweme_id: str, cookie: str = "", quality: str = "best") -> dict[str, Any] | None:
    """Best-effort: read the server-rendered detail page for one aweme."""
    if not aweme_id:
        return None
    try:
        html = http_get_text(f"https://www.douyin.com/video/{aweme_id}", cookie=cookie)
    except Exception:  # noqa: BLE001
        return None
    data = extract_router_data(html)
    if not isinstance(data, dict):
        return None
    # Search the loader data for an object that looks like an aweme detail.
    records = parse_aweme_payload(data, source="url", quality=quality)
    for record in records:
        if record.get("aweme_id") == aweme_id and (record.get("video_url") or record.get("image_urls")):
            return record
    return records[0] if records else None


# --------------------------------------------------------------------------
# cross-platform process + system proxy control (capture mode)
# --------------------------------------------------------------------------


def process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_process(pid: int) -> None:
    if pid <= 0:
        return
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    deadline = time.time() + 5
    while time.time() < deadline and process_running(pid):
        time.sleep(0.2)
    if process_running(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


def open_path_or_url(target: str) -> tuple[bool, str]:
    try:
        if IS_MACOS:
            subprocess.run(["open", target], check=True)
            return True, "open"
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]
            return True, "os.startfile"
        import webbrowser

        if webbrowser.open(target):
            return True, "webbrowser"
        subprocess.run(["xdg-open", target], check=True)
        return True, "xdg-open"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ---- Windows WinINET proxy ----

WINDOWS_INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def parse_windows_proxy_server(value: str) -> tuple[str, str]:
    value = str(value or "").strip()
    if not value:
        return "", ""
    if "=" in value:
        parts: dict[str, str] = {}
        for chunk in value.split(";"):
            if "=" in chunk:
                proto, addr = chunk.split("=", 1)
                parts[proto.strip().lower()] = addr.strip()
        value = parts.get("http") or parts.get("https") or next(iter(parts.values()), "")
    if ":" in value:
        host, _, port = value.rpartition(":")
        return host.strip(), port.strip()
    return value, ""


def get_windows_proxy_state() -> dict[str, Any]:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_INTERNET_SETTINGS) as key:
        def read(name: str, default: Any = "") -> Any:
            try:
                return winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                return default

        return {
            "enabled": bool(int(read("ProxyEnable", 0) or 0)),
            "server": str(read("ProxyServer", "") or ""),
            "override": str(read("ProxyOverride", "") or ""),
        }


def refresh_wininet() -> None:
    import ctypes

    wininet = ctypes.windll.wininet
    wininet.InternetSetOptionW(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
    wininet.InternetSetOptionW(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH


def apply_windows_proxy(enabled: bool, server: str | None = None, override: str | None = None) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enabled else 0)
        if server is not None:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
        if override is not None:
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, override)
    refresh_wininet()


# ---- macOS networksetup proxy ----


def run_networksetup(args: list[str]) -> subprocess.CompletedProcess[str]:
    command = shutil.which("networksetup")
    if not command:
        raise RuntimeError("networksetup not found; system-proxy control needs macOS")
    return subprocess.run([command, *args], text=True, capture_output=True, check=True)


def list_network_services() -> list[str]:
    services: list[str] = []
    for line in run_networksetup(["-listallnetworkservices"]).stdout.splitlines():
        value = line.strip()
        if not value or value.startswith("An asterisk") or value.startswith("*"):
            continue
        services.append(value)
    return services


def choose_network_service(service: str = "") -> str:
    if service:
        return service
    services = list_network_services()
    for candidate in ["Wi-Fi", "Ethernet", "USB 10/100/1000 LAN", "Thunderbolt Bridge"]:
        if candidate in services:
            return candidate
    if not services:
        raise RuntimeError("no active network services found")
    return services[0]


def parse_networksetup_proxy(output: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip().lower().replace(" ", "_")] = value.strip()
    parsed["enabled_bool"] = str(parsed.get("enabled", "")).lower() in {"yes", "on", "1", "true"}
    return parsed


def get_network_proxy_state(service: str) -> dict[str, Any]:
    return {
        "service": service,
        "web": parse_networksetup_proxy(run_networksetup(["-getwebproxy", service]).stdout),
        "secure_web": parse_networksetup_proxy(run_networksetup(["-getsecurewebproxy", service]).stdout),
    }


def set_proxy_from_state(service: str, kind: str, state: dict[str, Any]) -> None:
    getter = "-setwebproxy" if kind == "web" else "-setsecurewebproxy"
    state_flag = "-setwebproxystate" if kind == "web" else "-setsecurewebproxystate"
    if state.get("enabled_bool") and state.get("server") and str(state.get("port")) not in {"", "0"}:
        run_networksetup([getter, service, str(state["server"]), str(state["port"])])
        run_networksetup([state_flag, service, "on"])
    else:
        run_networksetup([state_flag, service, "off"])


def system_proxy_state_path(base: Path) -> Path:
    return base / "system_proxy_state.json"


def enable_system_proxy(base: Path, host: str, port: int, yes: bool, service: str = "") -> dict[str, Any]:
    if IS_WINDOWS:
        return _enable_system_proxy_windows(base, host, port, yes)
    if IS_MACOS:
        return _enable_system_proxy_macos(base, service, host, port, yes)
    return {
        "ok": False,
        "manual": True,
        "next_step": f"Set your system HTTP/HTTPS proxy to {host}:{port} manually, then scroll the target list in the Douyin client.",
    }


def disable_system_proxy(base: Path, yes: bool = False, service: str = "") -> dict[str, Any]:
    if IS_WINDOWS:
        return _disable_system_proxy_windows(base, yes)
    if IS_MACOS:
        return _disable_system_proxy_macos(base, service, yes)
    return {"ok": True, "manual": True, "next_step": "Turn off the system HTTP/HTTPS proxy manually."}


def _enable_system_proxy_windows(base: Path, host: str, port: int, yes: bool) -> dict[str, Any]:
    server = f"{host}:{port}"
    if not yes:
        return {"ok": False, "requires_confirmation": True, "proxy": server,
                "next_step": "Rerun with --yes to set the Windows system proxy and save the previous state."}
    previous = get_windows_proxy_state()
    write_json(system_proxy_state_path(base), {"saved_at": utc_now(), "platform": "windows", "previous": previous, "new": {"host": host, "port": port}})
    apply_windows_proxy(True, server=server)
    return {"ok": True, "proxy": server, "state": str(system_proxy_state_path(base)),
            "next_step": "Open the target list in the Douyin desktop client and scroll. Run capture-finish when done."}


def _disable_system_proxy_windows(base: Path, yes: bool) -> dict[str, Any]:
    state_path = system_proxy_state_path(base)
    if not state_path.exists():
        if not yes:
            return {"ok": False, "requires_confirmation": True, "next_step": "No saved state. Rerun with --yes to turn the system proxy off."}
        apply_windows_proxy(False)
        return {"ok": True, "restored": False, "message": "proxy disabled; no saved state"}
    if not yes:
        return {"ok": False, "requires_confirmation": True, "next_step": "Rerun with --yes to restore saved proxy settings."}
    saved = read_json(state_path)
    previous = saved.get("previous") if isinstance(saved.get("previous"), dict) else {}
    apply_windows_proxy(bool(previous.get("enabled")), server=str(previous.get("server") or ""), override=str(previous.get("override") or ""))
    saved["restored_at"] = utc_now()
    write_json(state_path, saved)
    return {"ok": True, "restored": True, "state": str(state_path)}


def _enable_system_proxy_macos(base: Path, service: str, host: str, port: int, yes: bool) -> dict[str, Any]:
    selected = choose_network_service(service)
    if not yes:
        return {"ok": False, "requires_confirmation": True, "service": selected, "proxy": f"{host}:{port}",
                "next_step": "Rerun with --yes to modify macOS proxy settings and save the previous state."}
    previous = get_network_proxy_state(selected)
    write_json(system_proxy_state_path(base), {"saved_at": utc_now(), "service": selected, "previous": previous, "new": {"host": host, "port": port}})
    run_networksetup(["-setwebproxy", selected, host, str(port)])
    run_networksetup(["-setsecurewebproxy", selected, host, str(port)])
    run_networksetup(["-setwebproxystate", selected, "on"])
    run_networksetup(["-setsecurewebproxystate", selected, "on"])
    return {"ok": True, "service": selected, "proxy": f"{host}:{port}", "state": str(system_proxy_state_path(base)),
            "next_step": "Open the target list in the Douyin desktop client and scroll. Run capture-finish when done."}


def _disable_system_proxy_macos(base: Path, service: str, yes: bool) -> dict[str, Any]:
    state_path = system_proxy_state_path(base)
    if not state_path.exists():
        selected = choose_network_service(service)
        if not yes:
            return {"ok": False, "requires_confirmation": True, "service": selected, "next_step": "No saved state. Rerun with --yes to turn proxy off."}
        run_networksetup(["-setwebproxystate", selected, "off"])
        run_networksetup(["-setsecurewebproxystate", selected, "off"])
        return {"ok": True, "service": selected, "restored": False}
    saved = read_json(state_path)
    selected = service or str(saved.get("service") or "") or choose_network_service("")
    if not yes:
        return {"ok": False, "requires_confirmation": True, "service": selected, "next_step": "Rerun with --yes to restore saved proxy settings."}
    previous = saved.get("previous") if isinstance(saved.get("previous"), dict) else {}
    set_proxy_from_state(selected, "web", previous.get("web") if isinstance(previous.get("web"), dict) else {})
    set_proxy_from_state(selected, "secure_web", previous.get("secure_web") if isinstance(previous.get("secure_web"), dict) else {})
    saved["restored_at"] = utc_now()
    write_json(state_path, saved)
    return {"ok": True, "service": selected, "restored": True}


# --------------------------------------------------------------------------
# mitmproxy capture orchestration
# --------------------------------------------------------------------------


def capture_addon_path() -> Path:
    return Path(__file__).resolve().parent / "douyin_capture_addon.py"


def mitmproxy_install_hint() -> str:
    if IS_MACOS and shutil.which("brew"):
        return "brew install mitmproxy"
    return f"{Path(sys.executable).name} -m pip install --user mitmproxy"


def install_mitmproxy(yes: bool) -> dict[str, Any]:
    if shutil.which("mitmdump"):
        return {"ok": True, "installed": False, "message": "mitmdump already available"}
    brew = shutil.which("brew") if IS_MACOS else None
    if brew:
        cmd, hint = [brew, "install", "mitmproxy"], "brew install mitmproxy"
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--user", "mitmproxy"]
        hint = f"{Path(sys.executable).name} -m pip install --user mitmproxy"
    if not yes:
        return {"ok": False, "requires_confirmation": True, "command": hint,
                "next_step": "Rerun capture-setup with --install --yes to install mitmproxy."}
    result = subprocess.run(cmd, text=True, capture_output=True)
    return {
        "ok": result.returncode == 0 and bool(shutil.which("mitmdump")),
        "installed": result.returncode == 0,
        "command": hint,
        "returncode": result.returncode,
        "stderr_tail": result.stderr[-1500:],
    }


def capture_setup(port: int, install: bool, yes: bool, open_cert: bool) -> dict[str, Any]:
    install_result = install_mitmproxy(yes) if (install and not shutil.which("mitmdump")) else None
    mitmdump = shutil.which("mitmdump")
    mitm_dir = Path.home() / ".mitmproxy"
    cert_files = {
        "pem": str(mitm_dir / "mitmproxy-ca-cert.pem"),
        "cer": str(mitm_dir / "mitmproxy-ca-cert.cer"),
        "p12": str(mitm_dir / "mitmproxy-ca-cert.p12"),
    }
    opened = False
    open_error = ""
    if open_cert:
        opened, method = open_path_or_url("http://mitm.it")
        if not opened:
            open_error = method
    return {
        "ok": bool(mitmdump),
        "mitmdump": mitmdump or "",
        "install": "" if mitmdump else f"Install mitmproxy first, for example: {mitmproxy_install_hint()}",
        "install_result": install_result,
        "proxy": f"127.0.0.1:{port}",
        "cert_page": "http://mitm.it",
        "cert_files": cert_files,
        "cert_files_exist": {name: Path(path).exists() for name, path in cert_files.items()},
        "opened_cert_page": opened,
        "open_error": open_error,
        "next_step": (
            "Run capture-prepare, confirm the system proxy, trust the mitmproxy cert at http://mitm.it, then scroll the target list."
            if mitmdump
            else "Install mitmproxy (capture-setup --install --yes), then rerun capture-setup."
        ),
    }


def proxy_state_path(base: Path, session_id: str) -> Path:
    return base / "proxy" / f"{session_id}.state.json"


def start_capture_proxy(base: Path, session_id: str, port: int, limit: int) -> dict[str, Any]:
    mitmdump = shutil.which("mitmdump")
    if not mitmdump:
        return {"ok": False, "error": "mitmdump not found",
                "install": f"Install mitmproxy first, for example: {mitmproxy_install_hint()}",
                "next_step": "After installing mitmproxy and trusting its cert, rerun capture-prepare."}
    addon = capture_addon_path()
    log_path = base / "proxy" / f"{session_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "MOORE_DOUYIN_RUNTIME_DIR": str(base),
            "MOORE_DOUYIN_SESSION_ID": session_id,
            "MOORE_DOUYIN_CAPTURE_LIMIT": str(max(limit, 1)),
        }
    )
    log_fh = log_path.open("a", encoding="utf-8")
    cmd = [mitmdump, "--listen-host", "127.0.0.1", "--listen-port", str(port), "--set", "block_global=false", "-s", str(addon)]
    if IS_WINDOWS:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, env=env, creationflags=creationflags)
    else:
        proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    log_fh.close()
    state = {
        "ok": True,
        "status": "running",
        "session_id": session_id,
        "pid": proc.pid,
        "port": port,
        "proxy": f"127.0.0.1:{port}",
        "started_at": utc_now(),
        "log": str(log_path),
    }
    write_json(proxy_state_path(base, session_id), state)
    return state


def stop_capture_proxy(base: Path, session_id: str) -> dict[str, Any]:
    path = proxy_state_path(base, session_id)
    if not path.exists():
        return {"ok": True, "stopped": False, "message": "no proxy state"}
    state = read_json(path)
    pid = _to_int(state.get("pid"))
    stopped = False
    if process_running(pid):
        terminate_process(pid)
        stopped = True
    state["status"] = "stopped"
    state["stopped_at"] = utc_now()
    write_json(path, state)
    return {"ok": True, "stopped": stopped, "pid": pid}


# --------------------------------------------------------------------------
# command handlers
# --------------------------------------------------------------------------


def command_capture_setup(args: argparse.Namespace) -> int:
    write_json_response(capture_setup(args.port, args.install, args.yes, args.open_cert_page))
    return 0


def command_capture_prepare(args: argparse.Namespace) -> int:
    base = runtime_dir(args.runtime_dir)
    if not shutil.which("mitmdump"):
        write_json_response({
            "ok": False,
            "state": "need_mitmproxy",
            "install": f"Install mitmproxy first, for example: {mitmproxy_install_hint()}",
            "next_step": "Run capture-setup --install --yes, then retry.",
        })
        return 1
    session = create_session(base, args.label, args.source)
    proxy = start_capture_proxy(base, session["session_id"], args.port, args.limit)
    if not proxy.get("ok"):
        write_json_response({**proxy, "session_id": session["session_id"]})
        return 1
    enable = enable_system_proxy(base, "127.0.0.1", args.port, args.yes, args.service)
    result = {
        "ok": True,
        "session_id": session["session_id"],
        "label": args.label,
        "source": args.source,
        "proxy": f"127.0.0.1:{args.port}",
        "proxy_process": {"pid": proxy.get("pid"), "log": proxy.get("log")},
        "system_proxy": enable,
        "instructions": [
            "确认已安装并信任 mitmproxy 根证书（http://mitm.it）。",
            "在抖音桌面客户端打开目标列表：某账号「作品」页，或「我」→「喜欢」/「收藏」。",
            "向下滚动加载你想要的范围（每次滚动约加载一屏）。",
            "滚完后运行 capture-status 查看已捕获数量，再 preview / select / download-selected。",
        ],
    }
    if not enable.get("ok") and enable.get("requires_confirmation"):
        result["state"] = "need_proxy_confirm"
        result["next_step"] = "向用户确认开启系统代理后，重跑 capture-prepare --yes（会复用当前代理进程）。"
    write_json_response(result)
    return 0


def command_capture_status(args: argparse.Namespace) -> int:
    base = runtime_dir(args.runtime_dir)
    session = load_session(base, args.session_id)
    captured = load_captured(base, args.session_id)
    by_source: dict[str, int] = {}
    for rec in captured:
        by_source[rec.get("source", "")] = by_source.get(rec.get("source", ""), 0) + 1
    proxy_path = proxy_state_path(base, args.session_id)
    proxy_state = read_json(proxy_path) if proxy_path.exists() else {}
    running = process_running(_to_int(proxy_state.get("pid"))) if proxy_state else False
    write_json_response({
        "ok": True,
        "session_id": args.session_id,
        "label": session.get("label", ""),
        "captured_count": len(captured),
        "by_source": by_source,
        "proxy_running": running,
    })
    return 0


def command_capture_finish(args: argparse.Namespace) -> int:
    base = runtime_dir(args.runtime_dir)
    stopped = stop_capture_proxy(base, args.session_id)
    restored = disable_system_proxy(base, args.yes, args.service)
    write_json_response({"ok": True, "proxy": stopped, "system_proxy": restored})
    return 0 if restored.get("ok") else 1


def _print_preview(records: list[dict[str, Any]], limit: int) -> None:
    preview = []
    for idx, rec in enumerate(records[:limit] if limit else records, 1):
        preview.append({
            "index": idx,
            "date": rec.get("create_date", ""),
            "desc": rec.get("desc", "")[:80],
            "type": rec.get("aweme_type", ""),
            "source": rec.get("source", ""),
            "digg_count": rec.get("statistics", {}).get("digg_count", 0),
        })
    write_json_response({"ok": True, "count": len(records), "items": preview})


def command_preview(args: argparse.Namespace) -> int:
    base = runtime_dir(args.runtime_dir)
    _print_preview(load_captured(base, args.session_id), args.limit)
    return 0


def command_select(args: argparse.Namespace) -> int:
    base = runtime_dir(args.runtime_dir)
    records = load_captured(base, args.session_id)
    selected = apply_selection(records, args.latest, args.indices, args.range, args.contains)
    save_selection(base, args.session_id, selected)
    _print_preview(selected, 0)
    return 0


def command_download_selected(args: argparse.Namespace) -> int:
    base = runtime_dir(args.runtime_dir)
    session = load_session(base, args.session_id)
    selected = load_selection(base, args.session_id)
    if not selected:
        selected = load_captured(base, args.session_id)
        if args.latest:
            selected = selected[: args.latest]
    if not selected:
        write_json_response({"ok": False, "error": "no records selected or captured"})
        return 1
    label = args.label or session.get("label") or selected[0].get("author_nickname") or args.session_id
    out = delivery_dir(args.output_dir, label)
    cookie = _read_cookie(args.cookie_file)
    result = download_records(selected, out, quality=args.quality, cookie=cookie)
    write_json_response(result)
    return 0 if result.get("ok") else 1


def command_download_json(args: argparse.Namespace) -> int:
    payload = read_json(Path(args.json_file).expanduser())
    records = parse_aweme_payload(payload, source="json", quality=args.quality)
    if args.latest:
        records = records[: args.latest]
    if not records:
        write_json_response({"ok": False, "error": "no aweme records found in JSON"})
        return 1
    label = args.label or records[0].get("author_nickname") or "douyin-json"
    out = delivery_dir(args.output_dir, label)
    cookie = _read_cookie(args.cookie_file)
    result = download_records(records, out, quality=args.quality, cookie=cookie)
    write_json_response(result)
    return 0 if result.get("ok") else 1


def command_download_url(args: argparse.Namespace) -> int:
    cookie = _read_cookie(args.cookie_file)
    aweme_id = resolve_aweme_id(args.url, cookie=cookie)
    if not aweme_id:
        write_json_response({"ok": False, "state": "unresolved",
                             "next_step": "Could not resolve an aweme id. Use capture mode for account/likes/collection lists."})
        return 1
    record = fetch_aweme_from_web(aweme_id, cookie=cookie, quality=args.quality)
    if not record or not (record.get("video_url") or record.get("image_urls")):
        write_json_response({"ok": False, "state": "failed_recoverable", "aweme_id": aweme_id,
                             "next_step": "Web extraction failed (anti-bot). Use capture mode, or pass a captured JSON to download-json."})
        return 1
    label = args.label or record.get("author_nickname") or aweme_id
    out = delivery_dir(args.output_dir, label)
    result = download_records([record], out, quality=args.quality, cookie=cookie)
    write_json_response(result)
    return 0 if result.get("ok") else 1


def command_list(args: argparse.Namespace) -> int:
    base_dir = Path(args.output_dir).expanduser() if args.output_dir else DEFAULT_DELIVERY_DIR
    accounts = []
    if base_dir.exists():
        for child in sorted(base_dir.iterdir()):
            index = child / "index.csv"
            if index.exists():
                with index.open("r", encoding="utf-8") as fh:
                    rows = list(csv.DictReader(fh))
                accounts.append({"dir": str(child), "count": len(rows)})
    write_json_response({"ok": True, "base_dir": str(base_dir), "accounts": accounts})
    return 0


def _read_cookie(cookie_file: str) -> str:
    if not cookie_file:
        return ""
    path = Path(cookie_file).expanduser()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Douyin video downloader runtime")
    parser.add_argument("--runtime-dir", default="", help="override internal runtime directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capture-setup", help="Check/install mitmproxy and show cert steps")
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--install", action="store_true")
    p.add_argument("--yes", action="store_true", help="Actually install mitmproxy (brew on macOS, pip otherwise)")
    p.add_argument("--open-cert-page", action="store_true", help="Open http://mitm.it in the default browser")
    p.set_defaults(func=command_capture_setup)

    p = sub.add_parser("capture-prepare", help="Start proxy + enable system proxy for a capture session")
    p.add_argument("label", help="account name or a short description for this session")
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--limit", type=int, default=1000, help="max items to capture in this session")
    p.add_argument("--source", default="auto", choices=["auto", "post", "favorite", "collection", "mix"])
    p.add_argument("--service", default="", help="macOS network service (auto-detected if empty)")
    p.add_argument("--yes", action="store_true", help="confirm enabling the system proxy")
    p.set_defaults(func=command_capture_prepare)

    p = sub.add_parser("capture-status", help="Show captured counts for a session")
    p.add_argument("session_id")
    p.set_defaults(func=command_capture_status)

    p = sub.add_parser("capture-finish", help="Stop proxy + restore system proxy")
    p.add_argument("session_id")
    p.add_argument("--service", default="")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=command_capture_finish)

    p = sub.add_parser("preview", help="Preview captured items (numbered, with dates)")
    p.add_argument("--session-id", required=True)
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=command_preview)

    p = sub.add_parser("select", help="Select captured items to download")
    p.add_argument("--session-id", required=True)
    p.add_argument("--latest", type=int, default=0)
    p.add_argument("--indices", default="")
    p.add_argument("--range", default="")
    p.add_argument("--contains", default="")
    p.set_defaults(func=command_select)

    p = sub.add_parser("download-selected", help="Download the current selection (or --latest)")
    p.add_argument("--session-id", required=True)
    p.add_argument("--output-dir", default="")
    p.add_argument("--quality", default="best", choices=["best", "source"])
    p.add_argument("--cookie-file", default="")
    p.add_argument("--latest", type=int, default=0)
    p.add_argument("--label", default="")
    p.set_defaults(func=command_download_selected)

    p = sub.add_parser("download-json", help="Download videos from an exported aweme JSON file")
    p.add_argument("json_file")
    p.add_argument("--output-dir", default="")
    p.add_argument("--quality", default="best", choices=["best", "source"])
    p.add_argument("--cookie-file", default="")
    p.add_argument("--latest", type=int, default=0)
    p.add_argument("--label", default="")
    p.set_defaults(func=command_download_json)

    p = sub.add_parser("download-url", help="Best-effort single share link / video id download")
    p.add_argument("url")
    p.add_argument("--output-dir", default="")
    p.add_argument("--quality", default="best", choices=["best", "source"])
    p.add_argument("--cookie-file", default="")
    p.add_argument("--label", default="")
    p.set_defaults(func=command_download_url)

    p = sub.add_parser("list", help="List downloaded accounts")
    p.add_argument("--output-dir", default="")
    p.set_defaults(func=command_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        write_json_response({"ok": False, "error": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001 - surface a clean JSON error
        write_json_response({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
