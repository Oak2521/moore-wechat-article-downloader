# Third-Party Notices

This project uses the Python standard library only. It vendors **no** third-party
code. The items below are either a runtime dependency the user installs
separately, or an upstream project whose **approach** informed the design
(referenced only — no code copied).

## Runtime dependency (installed by the user, not bundled)

- **mitmproxy** — https://github.com/mitmproxy/mitmproxy — BSD-3-Clause.
  Used only by the proxy history mode to read the user's own WeChat desktop
  traffic. Installed separately (`pip install mitmproxy` / Homebrew); no
  mitmproxy code is included in this repository.

## Referenced workflows (no code vendored)

The two-mode design was informed by public projects. We studied their techniques;
**no source code from them is included**:

- **wechat-article-exporter** —
  https://github.com/wechat-article/wechat-article-exporter — referenced for the
  Official Account backend session + article-list sync approach used by Exporter
  mode.
- **qiye45/wechatDownload** — https://github.com/qiye45/wechatDownload —
  referenced for the WeChat-desktop history-list capture approach used by proxy
  mode.

## Scope

Downloaded content belongs to its respective authors and the platform. This tool
is for personal, local archival of public content only; see
`references/compliance.md`.
