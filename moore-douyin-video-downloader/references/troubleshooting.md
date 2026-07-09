# Troubleshooting

## mitmproxy not installed

`capture-setup` / `capture-prepare` report `need_mitmproxy`. Install it:

```bash
python3 scripts/douyin_downloader.py capture-setup --install --yes
# macOS uses Homebrew when present; otherwise pip is used.
# Manual: pip install mitmproxy   (or: brew install mitmproxy)
```

## Capture gets nothing

Check:
- the system proxy actually points at `127.0.0.1:8899` (run `capture-prepare --yes`)
- the mitmproxy **root certificate is installed and trusted**
- you opened the list in the **Douyin desktop client** (not an unrelated browser)
- you scrolled the target list (each scroll loads roughly one screen)
- `capture-status <session-id>` shows the proxy process running
- you are on the right page: a creator's 作品, or your own 喜欢 / 收藏

### Windows certificate
With the proxy on, open `http://mitm.it`, download the `.p12` (or `.cer`), and
install it into **Local Machine → Trusted Root Certification Authorities**
(double-click → Install → Local Machine). Restart the Douyin client afterward.
The proxy is the per-user WinINET proxy (`Internet 选项`); run the capture as the
same Windows user whose proxy was changed.

### macOS certificate
Open `~/.mitmproxy/mitmproxy-ca-cert.pem`, add it to the keychain, and set it to
"Always Trust". `capture-prepare`/`capture-finish` save and restore the proxy on
the active network service via `networksetup`.

## Download fails / 403

- Some CDN URLs need headers or expire quickly. The tool retries across the
  video's candidate mirrors automatically.
- For 喜欢/收藏 or restricted items, provide your own login Cookie:
  `download-selected --cookie-file <file>` (do not paste Cookie into chat).
- Covers are best-effort and never fail an item; check the `.mp4` first.

## Video has a watermark

Use the default `--quality best` (or `source`). The tool selects `play_addr`
variants and never uses the watermarked `download_addr`/`playwm`. If a specific
item only exposes a watermarked source, that is a platform-side limitation.

## download-url fails (failed_recoverable)

The single-URL path reads the server-rendered page and is fragile under
anti-bot. Prefer capture mode for account/likes/collection lists, or pass a
captured JSON to `download-json`.

## Proxy left enabled after a crash

Re-run `capture-finish <session-id> --yes` to restore saved proxy settings. On
Windows you can also fix it in `Internet 选项 → 连接 → 局域网设置`; on macOS in
System Settings → Network → Proxies.

## Nothing captured but scrolling worked

Douyin occasionally changes endpoint paths. The matched endpoints are listed in
`references/backend-design.md`; if a new path appears, capture will resume once
the matcher includes it. As a fallback, export the response JSON and use
`download-json`.
