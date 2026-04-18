# wechat-rune

两个 Claude Code skill，用来处理你本地的微信（WeChat）数据，**macOS** 与 **Windows** 共用：

| Skill | 用途 |
|---|---|
| [`wechat-rune-export`](skills/wechat-rune-export/) | 解密导出聊天记录为 Markdown，带 Whisper 语音转写 + AI 同音字纠错 |
| [`wechat-rune-bot`](skills/wechat-rune-bot/) | 由 Claude 驱动的自动回复机器人——用你的语气起草回复，通过微信 UI 发送 |

两个 skill 共享同一套密钥提取与解密管线。先跑过 `export`，再启动 `bot` 时会自动复用
已经生成的 `scripts/keys/wechat_keys.json`，直接跳到 bot 配置环节。

## 为什么叫 "rune"（符文）

每个微信 SQLCipher 数据库背后都锁着一个 64 位十六进制 key，形如
`aa713385968bb2d953fdb6b1f79b83f1e25aac99bd9bb78ea7a31219e274322a`——这就是现代意义
上的符文：一串短小隐晦的符号，却拥有解开背后一切的魔力。这套 skill 的工作，就是从你
自己的微信进程内存里找到这些符文，然后用它们读回你自己的数据。

## 环境要求

| | Mac | Windows |
|---|---|---|
| 操作系统 | macOS 13+（Apple Silicon 或 Intel） | Windows 10+ |
| 运行时 | Python 3.10+、Xcode 命令行工具（需要 `cc`） | Python 3.10+ |
| 微信版本 | 4.x（Weixin） | 4.x（Weixin） |
| 额外依赖 | `brew install sqlcipher ffmpeg`、编译 `silk-v3-decoder` | `pip install -r requirements.txt`（一次搞定） |
| 所需权限 | `sudo`（重签 1 次 + 每次抓 key 1 次） | 无需 Admin |

## 安装

clone，然后 symlink 两个 skill 到 Claude Code 的 skill 目录：

```bash
git clone https://github.com/xjin6/wechat-rune.git ~/Desktop/wechat-rune
cd ~/Desktop/wechat-rune
pip install -r requirements.txt

ln -s "$PWD/skills/wechat-rune-export" ~/.claude/skills/wechat-rune-export
ln -s "$PWD/skills/wechat-rune-bot"    ~/.claude/skills/wechat-rune-bot
```

重启 Claude Code。之后：
- "帮我导出微信聊天记录" → 触发 `wechat-rune-export`
- "帮我设置微信 AI 自动回复" → 触发 `wechat-rune-bot`

## 项目结构

```
wechat-rune/
├── skills/
│   ├── wechat-rune-export/
│   │   ├── SKILL.md           # Claude Code skill 清单
│   │   └── README.md
│   └── wechat-rune-bot/
│       ├── SKILL.md
│       └── README.md
├── scripts/                    # 两个 skill 共享的代码
│   ├── keys/
│   │   ├── extract_key_macos.c      # Mac：编译一次，sudo 运行
│   │   └── extract_key_windows.py   # Windows：Python + ctypes
│   ├── export_chat.py
│   ├── transcribe_voices.py
│   ├── start.py                     # bot 启动器
│   ├── bot.py                       # bot 主循环
│   ├── dashboard.py                 # 实时 web 面板
│   ├── config.py
│   └── core/                        # RAG、向量检索、发送器、解密、联系人
├── requirements.txt
├── README.md
└── README_CN.md                     # 本文件
```

## 隐私与合法性

- 一切操作本地进行，对你自己机器上你自己的微信数据进行解密。
- 除了 bot 运行时调用 Anthropic API（只传最近的消息窗口，不传全部历史），不上传任何
  第三方服务器。
- 读取自己进程内存（用来拿 key）是 macOS/Windows 对"你拥有的进程"显式允许的操作。
  没有绕过任何服务端安全机制——只是读取你自己的微信客户端正在自己使用的那串 key。
- 不要用 bot 在会误导他人的场景中冒充他人。

## 许可证

MIT，详见 [LICENSE](LICENSE)。

## 致谢

- C 版 key scanner 改编自社区对 WeChat 4.x SQLCipher 存储的逆向分析
- SILK 音频解码：[kn007/silk-v3-decoder](https://github.com/kn007/silk-v3-decoder)
- Whisper：[openai/whisper](https://github.com/openai/whisper)
