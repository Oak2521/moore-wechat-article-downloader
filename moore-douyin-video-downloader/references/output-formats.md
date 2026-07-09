# Output Formats

## Delivery directory

Default: `~/Downloads/douyin-videos/<label>/` where `<label>` is the account
nickname (or the session label / provided `--output-dir`).

```text
<label>/
├── index.csv                          metadata index for all items
├── videos/<seq>-<aweme_id>-<desc>.mp4 one video file per video post
├── covers/<seq>.jpg                   cover image (best-effort)
├── images/<seq>/<n>.jpg               image-post ("图文") pictures
└── meta/<seq>.json                    full scrubbed metadata per item
```

- `<seq>` is a 1-based, zero-padded sequence (`001`, `002`, ...).
- Video posts write under `videos/`; image posts write under `images/<seq>/`.
- `covers/` is best-effort and never fails an item.

## index.csv columns

```text
seq, aweme_id, aweme_type, desc, author_nickname, create_date,
source, digg_count, share_url, file, status
```

- `aweme_type`: `video` or `image`.
- `source`: `post` / `favorite` / `collection` / `mix` / `detail` / `json` / `url`.
- `file`: path (relative to the delivery dir) of the primary asset.
- `status`: `success` or `failed`.

## meta/<seq>.json

The normalized record with all media URLs scrubbed of signatures/credentials
(`SENSITIVE_QUERY_KEYS`). Fields include `aweme_id`, `desc`, `create_time`,
`create_date`, `aweme_type`, author info, `statistics` (digg/comment/share/
collect/play counts), `music`, `source`, and `share_url`
(`https://www.douyin.com/video/<aweme_id>`).

## Internal runtime

`~/.moore/douyin-video-downloader/`
```text
sessions/<session-id>.json              session state
sessions/<session-id>.selected.json     saved selection
captures/<session-id>.jsonl             captured records (deduped by aweme_id)
proxy/<session-id>.state.json           proxy process state
proxy/<session-id>.capture-status.json  live capture counter
system_proxy_state.json                 saved system proxy (for restore)
```

## Quality

- `--quality best` (default): highest `bit_rate[].play_addr` (HD, no watermark).
- `--quality source`: plain `play_addr`.
- Watermarked `download_addr`/`playwm` sources are never used.
