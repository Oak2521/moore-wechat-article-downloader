---
name: moore-douyin-video-downloader
description: 当用户要批量下载抖音视频时使用本技能。支持三类目标：某个账号的作品、用户自己的「喜欢」列表、用户自己的「收藏」列表，高清无水印下载到本地。主路径是代理捕获（mitmproxy 被动读取用户自己客户端的接口返回，绕开抖音签名）；备选是从导出的 JSON 或单条分享链接下载。优先本地运行；只下载公开或用户自己有权访问的内容；不要绕过私密/登录/付费内容，不打印或落盘 cookie/token/签名，不扩展成转载、二创、SaaS 或代抓服务。
---

# Moore 抖音视频下载器

用自然语言驱动，批量把抖音视频高清无水印下载到本地。

## 场景判断

| 用户意图 | 场景 | 入口 |
|---------|------|------|
| 下载某账号作品 / 我的喜欢 / 我的收藏（列表） | 场景 1：代理捕获（默认） | `capture-prepare` → 滚动 → `preview`/`select`/`download-selected` |
| 已有导出的接口 JSON | 场景 2：JSON 下载 | `download-json <file>` |
| 单条分享链接或视频 ID | 场景 3：单条下载（尽力而为） | `download-url <url>` |

统一优先代理捕获。命令返回以下 gate 时按表处理，不要立刻降级：

| Gate | 处理 |
|------|------|
| `need_mitmproxy` | 引导 `capture-setup --install --yes` 安装 mitmproxy |
| `need_cert_trust` | 引导安装并信任 mitmproxy 根证书（`http://mitm.it`） |
| `need_proxy_confirm` | 向用户确认开启系统代理 → 确认后 `capture-prepare --yes` |
| `need_scroll` | 让用户在客户端滚动目标列表，滚够后继续 |
| `need_selection` | 聊天中列出作品（描述+日期），让用户选后再下载 |

## 范围

可以做：下载**公开账号的公开作品**；下载**用户登录自己账号后**可见的「喜欢」「收藏」；高清无水印、按账号归档到本地。

不要做：绕过私密账号 / 登录墙 / 付费内容；代替用户登录或窃取凭证；打印或落盘 cookie/token/msToken/签名；抓取他人私密数据；把下载内容用于二次分发或商用；扩展成云服务或代抓服务。

## 场景 1：代理捕获（默认首选）

**原理**：用户在自己的抖音**桌面客户端**里正常浏览目标列表并向下滚动，本地 mitmproxy 被动读取客户端已经拿到的接口返回，解析出作品列表。绕开抖音的运行时签名，稳定且合规。

三类目标统一到同一套捕获，区别只是**用户打开哪个页面**：
- 账号作品：该账号主页「作品」
- 我的喜欢：「我」→「喜欢」
- 我的收藏：「我」→「收藏」

### 步骤

```bash
# 1) 首次：检查/安装 mitmproxy，并提示信任证书
python3 {baseDir}/scripts/douyin_downloader.py capture-setup --install --yes

# 2) 启动捕获会话（会请求确认开启系统代理）
python3 {baseDir}/scripts/douyin_downloader.py capture-prepare "<账号名或说明>" --port 8899 --yes
#   返回 session_id；--source 可选 auto|post|favorite|collection|mix（默认 auto，自动识别）

# 3) 让用户在抖音桌面客户端打开目标列表并向下滚动，随时查看已捕获数量
python3 {baseDir}/scripts/douyin_downloader.py capture-status "<session-id>"

# 4) 预览并选择（强制：先在聊天中列出标题+日期让用户选）
python3 {baseDir}/scripts/douyin_downloader.py preview --session-id "<session-id>" --limit 30
python3 {baseDir}/scripts/douyin_downloader.py select --session-id "<session-id>" --latest 20
#   或 --indices "1,3,5" / --range "1-20" / --contains "关键词"

# 5) 下载（默认最高码率无水印）
python3 {baseDir}/scripts/douyin_downloader.py download-selected --session-id "<session-id>"

# 6) 结束：停代理并还原系统代理
python3 {baseDir}/scripts/douyin_downloader.py capture-finish "<session-id>" --yes
```

**作品列表展示（强制要求）**：捕获后必须在聊天中直接罗列，格式：

```text
- **YYYY-MM-DD**：作品描述（视频/图文）
```

让用户按关键词、日期、最新 N 条或编号范围选择后再下载，不要自动全量下载。

**硬性规则**：
- 开启系统代理前必须向用户确认；结束时必须 `capture-finish` 还原代理。
- 引导用户用**抖音桌面客户端**打开列表（不是随便的系统浏览器）。
- 喜欢/收藏必须是用户登录**自己**的账号浏览自己的列表。
- 只捕获用户实际滚过的作品，建议滚完目标范围。

## 场景 2：从 JSON 下载

用户已经有导出的接口返回 JSON（含 `aweme_list`/`aweme_detail`）时：

```bash
python3 {baseDir}/scripts/douyin_downloader.py download-json "<file.json>" [--latest N]
```

## 场景 3：单条分享链接（尽力而为）

```bash
python3 {baseDir}/scripts/douyin_downloader.py download-url "<分享链接或视频ID>"
```

失败（`failed_recoverable`）时说明抖音反爬拦截，改走代理捕获。

## 清晰度与水印

- 默认 `--quality best`：从多档码率里取最高的无水印播放地址。
- `--quality source`：取默认播放地址。
- 一律避开带水印的 `download_addr`/`playwm`。

## Cookie（可选）

下载喜欢/收藏或部分受限内容时可能需要用户自己的登录 Cookie：把 Cookie 存到一个文件，`download-selected --cookie-file <file>`。不要在聊天中打印 Cookie 内容。

## 路径和输出

`{baseDir}` = 本 `SKILL.md` 所在目录，脚本在 `{baseDir}/scripts/`。

默认下载目录：`~/Downloads/douyin-videos/<账号名>/`

```text
index.csv
videos/<seq>-<aweme_id>-<safe_desc>.mp4
covers/<seq>.jpg
images/<seq>/<n>.jpg      # 图文作品
meta/<seq>.json           # 脱敏元数据
```

内部会话数据：`~/.moore/douyin-video-downloader/`

## 输出约定

每次结束报告：使用的模式、成功/失败数量、失败项、`output_dir`、`index.csv`。
保持回复简洁，详细内容由文件承载。

## 参考文件

- `references/backend-design.md`：架构与 CLI 契约
- `references/capture-flow.md`：代理捕获操作流程
- `references/output-formats.md`：输出文件结构
- `references/compliance.md`：合规与权限边界
- `references/troubleshooting.md`：常见失败与处理
