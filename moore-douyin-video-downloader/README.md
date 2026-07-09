# Moore 抖音视频下载器

用自然语言驱动的抖音视频批量下载 Skill —— 高清、无水印，批量下载某个账号的作品、你自己的「喜欢」列表、你自己的「收藏」列表。

## 亮点

- **说话即操作** — 直接告诉 AI 你想下哪个账号 / 哪个列表，无需记命令
- **高清无水印** — 自动挑最高码率的无水印播放地址,避开带水印的下载源
- **被动捕获、绕开签名** — 你在自己客户端里正常浏览,工具通过本地代理读取接口返回,不去逆向抖音的反爬签名,稳定且合规
- **三类目标统一** — 账号作品 / 我的喜欢 / 我的收藏,同一套流程
- **输出干净可用** — 视频 + 封面 + 元数据 + `index.csv`,按账号归档
- **纯 Python 标准库** — 无需 `pip install`,Python 3.10+ 即可(仅捕获模式需要 mitmproxy)
- **跨平台** — Windows / macOS 自动开关系统代理并还原;Linux 手动设代理

## 你可以这样说

> 「下载抖音账号〈XXX〉的作品」
> 「把我抖音的喜欢列表批量下载下来」
> 「下载我收藏的这些视频」
> 「这条抖音帮我存下来 https://v.douyin.com/xxx」

## 工作方式（代理捕获,默认）

1. 首次:安装 mitmproxy 并信任其根证书(用于解密 HTTPS)
2. AI 启动本地代理(8899 端口),确认后临时开启系统代理
3. 你在**抖音桌面客户端**打开目标列表:某账号「作品」页,或「我」→「喜欢」/「收藏」
4. 向下滚动加载你想要的范围
5. AI 列出捕获到的作品(描述+日期),你选择要下载哪些
6. 高清无水印下载完成,存入 `~/Downloads/douyin-videos/〈账号名〉/`
7. AI 关闭代理并还原系统设置

## 输出格式

```
~/Downloads/douyin-videos/
└── 〈账号名〉/
    ├── index.csv                     所有作品元数据索引
    ├── videos/001-<id>-<描述>.mp4     视频
    ├── covers/001.jpg                封面
    ├── images/001/01.jpg             图文作品的图片
    └── meta/001.json                 脱敏元数据
```

## 安装

```bash
python3 --version           # 3.10+ 即可
pip install mitmproxy       # 仅代理捕获模式需要;也可用 capture-setup --install --yes
```

作为本地 skill 安装(以自己电脑为例):

```bash
git clone <this-repo> ~/.claude/skills/moore-douyin-video-downloader
```

## 平台支持

| 功能 | Windows | macOS | Linux |
|------|:---:|:---:|:---:|
| 代理捕获批量下载 | ✅ | ✅ | ✅ |
| 自动开关系统代理 | ✅ 注册表+WinINET | ✅ networksetup | ⚠️ 需手动设代理 |
| JSON / 单条下载 | ✅ | ✅ | ✅ |

## 合规

只下载公开或你自己有权访问的内容;不绕过私密/登录/付费内容;不打印或落盘凭证;不用于二次分发。详见 [`references/compliance.md`](references/compliance.md)。

## 参考文档

- [`SKILL.md`](SKILL.md) — 意图路由与场景定义
- [`docs/plans/2026-07-09-design.md`](docs/plans/2026-07-09-design.md) — 设计方案
- [`references/backend-design.md`](references/backend-design.md) — 架构与 CLI 契约
- [`references/capture-flow.md`](references/capture-flow.md) — 代理捕获流程
- [`references/output-formats.md`](references/output-formats.md) — 输出格式
- [`references/compliance.md`](references/compliance.md) — 合规与权限
- [`references/troubleshooting.md`](references/troubleshooting.md) — 排错

## License

MIT. See [`LICENSE`](LICENSE).
