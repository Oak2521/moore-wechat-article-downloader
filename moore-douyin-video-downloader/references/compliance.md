# Compliance & Boundaries

This tool is for **personal, local archival** of content the user is entitled to
access. It follows the same posture as the sibling WeChat downloader: capture
what the user themselves can already see, never bypass protections.

## Allowed

- Download **public** posts from **public** accounts.
- Download the user's **own** 喜欢 (likes) and 收藏 (collection) — while the user
  is logged into **their own** account and browsing **their own** lists, so the
  tool only reads the user's own traffic.
- Save HD, no-watermark files locally and organize them per account.

## Not allowed

- Bypassing private accounts, login walls, age gates, or paid content.
- Logging in on the user's behalf, or capturing/stealing credentials.
- Printing or persisting cookies, tokens, `msToken`, `X-Bogus`/`a_bogus`,
  `_signature`, `ttwid`, or any auth/session material.
- Harvesting other people's private data.
- Redistributing downloaded content, or any commercial reuse.
- Turning this into a hosted crawler / bulk-scraping service / SaaS.

## How the implementation enforces this

- **Passive capture only**: the addon reads responses the user's own client
  already fetched; it does not forge signatures or drive the account.
- **Explicit proxy consent**: enabling/disabling the system proxy requires
  `--yes`; the previous proxy state is saved and restored on `capture-finish`.
- **Scrubbing**: every stored URL (metadata, session files) is passed through
  `scrub_url`, which strips the `SENSITIVE_QUERY_KEYS` (signatures/credentials).
  The addon writes only public aweme fields plus a small status marker.
- **User-driven selection**: the list is previewed in chat and the user chooses
  what to download; nothing is auto-downloaded in bulk without confirmation.

## Respect platform terms

Douyin's Terms of Service and robots policy govern automated access. Users are
responsible for their own use. When a request would require bypassing a
protection or accessing someone else's private data, stop and tell the user
instead of proceeding.
