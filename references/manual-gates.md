# Manual Gates

Actions only a human can perform. The assistant must **stop and ask** at each
gate using the confirmation templates below (verbatim wording keeps requests
consistent and transparent), then continue only after explicit approval.

## The gates

| Gate | Why it's human-only |
|------|---------------------|
| Scan the QR to log in (Exporter) | the user's own WeChat Official Account credentials |
| Choose among same-name accounts | only the user knows which account is theirs |
| Choose which articles to download | the user decides scope; never auto-download everything |
| Paste an exporter auth-key | credential material |
| Install mitmproxy (proxy mode) | changes the machine; needs consent |
| Enable the system proxy (proxy mode) | reroutes HTTP(S); must be reverted afterward |
| Install & trust the mitmproxy cert | security-sensitive; never done silently |
| Open the history page in WeChat desktop and scroll | only the user can drive their own client |

## Confirmation templates (copy-paste)

**Exporter QR login**
> 我会生成一个二维码,请用手机微信扫码,并在微信里**选择你的公众号/服务号**(不要选小程序)确认登录。
> 登录凭证约 4 天有效,期间不用重复扫码。扫完告诉我一声。

**Same-name account choice**
> 搜到多个同名公众号,请选一个(我在聊天里列出了昵称/fakeid):

**Article selection**
> 这是抓到的文章「标题 + 日期」,你想下载哪些?可以说「最新 20 篇」「1-30」或按标题关键词。
> (历史/Exporter 模式不会自动全量下载,先由你选。)

**Paste auth-key (fallback)**
> 如果二维码流程不可用,请从 exporter 的 API 页复制 auth-key 贴给我。我不会在聊天里回显完整 auth-key,会优先存入系统凭据库(macOS Keychain / Windows DPAPI)。

**Enable system proxy (proxy mode)**
> 代理模式需要临时把系统 HTTP/HTTPS 代理指向 `127.0.0.1:8899`。结束时我会自动还原你原来的设置。是否允许?(是/否)

**Install / trust the mitmproxy certificate**
> 代理抓取 HTTPS 需要你**手动**安装并信任 mitmproxy 根证书(`http://mitm.it` 下载,
> Windows 装到「本地计算机 → 受信任的根证书颁发机构」,macOS 钥匙串设「始终信任」),然后重启。
> 我不会替你安装证书。装好后告诉我。

**Open history page + scroll (proxy mode)**
> 把这个旧版历史入口发到微信文件传输助手,用**微信桌面客户端内置浏览器**打开,
> 看到历史文章列表后**向下滚动**加载你要的范围。滚够了告诉我。

**Restore the proxy when finished**
> 抓取完成,我现在关闭本地代理并还原你的系统代理设置。

## Rules

- Never scan-login, paste auth-key, enable the proxy, or install/trust a cert on
  the user's behalf without an explicit "yes" to the templates above.
- Always restore the proxy when done, even on error.
- Never print, log, or persist auth-key / cookie / token / pass_ticket.
