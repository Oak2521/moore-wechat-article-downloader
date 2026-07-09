# Backend Design

## Scope

Three modes, one Markdown-clean delivery shape:

1. **Capture mode (default)** — mitmproxy passively reads the user's own Douyin
   client traffic while they scroll a target list (a creator's posts, or the
   user's own 喜欢 / 收藏). The addon parses the aweme JSON the client already
   received and materializes a capture file. This avoids forging Douyin's
   runtime anti-bot signatures.
2. **JSON mode** — download from an exported aweme JSON file the user already has.
3. **URL mode** — best-effort single share link / video id via the server-rendered
   detail page. Fragile under anti-bot; capture mode is the reliable path.

Out of scope: forging signatures at scale, logging in on the user's behalf,
capturing other people's private data, redistribution, cloud/SaaS.

## Layers

### Skill layer
Classifies intent, runs CLI commands, handles gates (mitmproxy install, cert
trust, proxy confirm, scroll, selection), previews the captured list in chat,
asks the user to choose, reports paths.

### Core library / CLI (`douyin_downloader.py`)
Pure, offline-testable logic plus the download pipeline and OS control:
- `extract_aweme_items` / `normalize_aweme` / `parse_aweme_payload` — robust
  parsing across `aweme_list` / `aweme_detail` / `data` shapes.
- `best_video_url(video, quality)` — HD/no-watermark selection: highest
  `bit_rate[].play_addr` for `best`, plain `play_addr` for `source`; never
  `download_addr`/`playwm`.
- `extract_image_urls` — 图文 posts.
- `scrub_url` / `SENSITIVE_QUERY_KEYS` — strip signatures/credentials before
  persisting to metadata or session files.
- `apply_selection` — latest / indices / range / contains.
- `download_records` — per item: video (with candidate fallback) or images,
  cover, scrubbed `meta/<seq>.json`, then `index.csv`.
- Sessions: JSON session + JSONL capture with dedupe by `aweme_id`.
- Cross-platform system proxy (Windows registry+WinINET, macOS networksetup),
  Windows-safe process liveness/termination, mitmproxy install/orchestration.

### Capture addon (`douyin_capture_addon.py`)
Thin mitmproxy addon. On each JSON response whose URL matches a known endpoint,
it parses aweme items and appends them (deduped, scrubbed) to the session
capture file, and writes a small safe status marker. It never writes or logs
cookies, tokens, msToken, or signatures.

## Endpoints matched (tagged by source)

| source | URL substring |
|--------|---------------|
| post | `/aweme/v1/web/aweme/post/` |
| favorite | `/aweme/v1/web/aweme/favorite/` |
| collection | `/aweme/v1/web/aweme/listcollection/`, `.../collection/` |
| mix | `/aweme/v1/web/mix/aweme/` |
| detail | `/aweme/v1/web/aweme/detail/` |

## CLI contract

```bash
# capture
douyin_downloader.py capture-setup [--install] [--yes] [--open-cert-page] [--port 8899]
douyin_downloader.py capture-prepare "<label>" [--port 8899] [--source auto|post|favorite|collection|mix] [--yes] [--service <macOS svc>]
douyin_downloader.py capture-status "<session-id>"
douyin_downloader.py capture-finish "<session-id>" [--yes] [--service <macOS svc>]

# list + select
douyin_downloader.py preview --session-id "<id>" [--limit N]
douyin_downloader.py select --session-id "<id>" (--latest N | --indices "1,3" | --range "1-20" | --contains "kw")
douyin_downloader.py download-selected --session-id "<id>" [--output-dir DIR] [--quality best|source] [--cookie-file F] [--latest N] [--label NAME]

# secondary
douyin_downloader.py download-json "<file.json>" [--output-dir DIR] [--quality ...] [--latest N] [--label NAME]
douyin_downloader.py download-url "<share-url-or-id>" [--output-dir DIR] [--quality ...] [--cookie-file F]
douyin_downloader.py download-user "<profile-url-or-sec_uid>" [--latest N] [--output-dir DIR] [--quality ...] [--cookie-file F]

# utility
douyin_downloader.py list [--output-dir DIR]
validate_outputs.py "<dir>"

# smoke test (real sockets; --self-test needs no Douyin)
smoke_download.py --self-test | --account "<url>" [--latest N] [--cookie-file F] | --session-id "<id>"
```

All commands print a single JSON object to stdout and use exit code 0/1.

## Runtime storage

User-facing: `~/Downloads/douyin-videos/<label>/`

Internal: `~/.moore/douyin-video-downloader/`
```text
sessions/<session-id>.json
sessions/<session-id>.selected.json
captures/<session-id>.jsonl
proxy/<session-id>.state.json
proxy/<session-id>.capture-status.json
system_proxy_state.json
```

## Security rules

- Enable/disable the system proxy only with `--yes`; save & restore previous state.
- Never print or persist cookies/tokens/msToken/signatures.
- Do not bypass private accounts, login walls, or paid content.
- 喜欢/收藏require the user browsing their own logged-in account (own traffic only).
