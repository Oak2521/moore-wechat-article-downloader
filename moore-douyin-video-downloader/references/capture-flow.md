# Capture Flow

The capture mode reads the user's **own** Douyin client traffic through a local
mitmproxy. It never forges signatures and never logs credentials.

## Principle

The user opens the target list in the Douyin desktop client and scrolls. The
client makes signed API calls (`aweme/post`, `aweme/favorite`,
`aweme/listcollection`, ...) and receives JSON. mitmproxy — routed as the
system proxy — hands each response to the addon, which parses the aweme items
and appends them to the session capture file.

Same flow for all three targets; only the page the user opens differs:

| Target | Open in the Douyin client | Endpoint captured |
|--------|---------------------------|-------------------|
| A creator's posts | that account's 作品 page | `aweme/post` |
| My likes | 我 → 喜欢 | `aweme/favorite` |
| My collection | 我 → 收藏 | `aweme/listcollection` / `collection` |
| A mix/合集 | the mix page | `mix/aweme` |

## Steps

```bash
# 0) once: install mitmproxy + show cert steps
douyin_downloader.py capture-setup --install --yes
```

Install and **trust** the mitmproxy root certificate (HTTPS capture needs it):
- open `http://mitm.it` with the proxy on, download the cert for your OS
- **Windows**: install the `.p12`/`.cer` into `Local Machine → Trusted Root
  Certification Authorities`, then restart the Douyin client
- **macOS**: open the `.pem`, add to login/System keychain, set to "Always Trust"

```bash
# 1) start a capture session (asks to confirm enabling the system proxy)
douyin_downloader.py capture-prepare "<账号名或说明>" --port 8899 --yes
# -> returns session_id; system proxy now points at 127.0.0.1:8899

# 2) user opens the target list in the Douyin DESKTOP client and scrolls down
douyin_downloader.py capture-status "<session-id>"     # captured_count grows as they scroll

# 3) preview + select (show titles+dates in chat first)
douyin_downloader.py preview --session-id "<session-id>" --limit 30
douyin_downloader.py select  --session-id "<session-id>" --latest 20

# 4) download HD/no-watermark
douyin_downloader.py download-selected --session-id "<session-id>"

# 5) stop proxy + restore system settings
douyin_downloader.py capture-finish "<session-id>" --yes
```

## Selection contract

- `--latest N` — first N of the current list
- `--indices "1,3,5"` — specific 1-based rows
- `--range "1-20"` — contiguous rows
- `--contains "kw"` — description contains keyword

Filter order: `--contains` → `--indices`/`--range` → `--latest`.

## Platform notes for the system proxy

- **Windows**: reads/writes the per-user WinINET proxy (the `Internet 选项`
  proxy the Douyin client honors) via the registry, then refreshes WinINET.
  Previous `ProxyEnable/ProxyServer/ProxyOverride` are saved and restored.
- **macOS**: uses `networksetup` on the active service; previous state saved and
  restored.
- **Linux**: set the system/HTTP(S) proxy to `127.0.0.1:8899` manually, then
  restore it yourself after `capture-finish`.

## Adapter boundary

The addon writes only aweme fields and a small status marker. It does not write
or print cookies, tokens, pass tickets, msToken, or signatures. It captures only
what the user actually scrolled past — scroll the full range you want.
