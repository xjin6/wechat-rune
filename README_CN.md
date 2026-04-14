[English](README.md) | [中文](README_CN.md)

# wechat-export-bot

将微信聊天记录导出为 Markdown（含语音转文字），还可以跑一个 AI 自动回复机器人——全部在 Mac 本地完成。

> **仅支持 macOS**，需要微信 Mac 桌面客户端。

---

## 功能

### 聊天记录导出
- 导出任意私聊或群聊为 Markdown
- 支持 **16 种消息类型**：文字、图片、语音、视频、表情包、公众号、小程序、文件、转账、红包、拍一拍、引用回复（嵌套）、通话、系统消息
- 语音消息自动转文字（Whisper，直接从本地数据库提取，不需要网络）
- 同音错字 LLM 纠正，HTML 注释标注（渲染不可见）
- 发送者统一显示微信昵称
- 跨数据库 `server_id` 去重（全局唯一）

### AI 机器人
- 监听指定对话的触发词
- 通过 Claude API 自动回复
- AppleScript 发送——不使用任何非官方微信协议
- 每个对话独立记忆，可配置历史窗口
- RAG 向量搜索支持长期上下文

---

## 原理

**聊天导出：**
```
加密数据库 → SQLCipher 解密 → 解析消息 → 格式化 Markdown
语音：media_0.db BLOB → SILK 解码 → ffmpeg → Whisper → 转录文字
```

**AI 机器人：**
```
FSEvents 监测 DB 变化 → SQLCipher 查询 → 触发词匹配 → Claude API → AppleScript 发送
```

---

## 快速开始

### 前置安装

```bash
brew install sqlcipher ffmpeg
pip install -r requirements.txt

# SILK 解码器（语音转录用）
git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder
cd /tmp/silk-v3-decoder/silk && make
```

### 1. 提取加密密钥

微信数据库是加密的，需要提取一次密钥（大版本更新后重新提取）：

```bash
# 重签名微信（允许调试器附加）
sudo codesign --force --deep --sign - /Applications/WeChat.app

# 提取（微信必须在运行中）
lldb -p $(pgrep -x WeChat) \
     -o "script exec(open('scripts/keys/extract_key2.py').read())" \
     -o "quit"
```

### 2. 导出聊天记录

```bash
python3 scripts/export_chat.py --name "昵称"
```

### 3. 语音转文字（可选）

```bash
python3 scripts/transcribe_voices.py --name "昵称"
python3 scripts/export_chat.py --name "昵称" --voice-json 名字_voice_map.json
```

### 4. 启动 AI 机器人（可选）

```bash
# 配置
echo "wxid_xxx" > .watch
echo "sk-ant-xxx" > .apikey

# 启动
python3 scripts/start.py
```

---

## 支持的消息类型

| 类型 | 输出示例 |
|------|---------|
| 文字 | `hi你好。` |
| 图片 | `[图片]` |
| 语音 | `[语音 9s] 转录文字` |
| 视频 | `[视频 23s]` |
| 表情包 | `[表情包]` |
| 公众号 | `[公众号 \| 来源名] 标题` |
| 小程序 | `[小程序 \| 应用名] 标题` |
| 文件 | `[文件] 文件名.pdf` |
| 聊天记录 | `[聊天记录] 标题` |
| 转账 | `[转账 ¥0.68]` |
| 红包 | `[红包 备注]` |
| 拍一拍 | `[互动] I tickled ...` |
| 通话 | `[语音通话 120s]` |
| 引用回复 | 正文 + `> 被引内容`（嵌套） |
| 系统消息 | *斜体* |

---

## 项目结构

```
wechat-ai-bot/
├── scripts/
│   ├── export_chat.py          # 聊天记录 → Markdown
│   ├── transcribe_voices.py    # 语音 → Whisper 转录
│   ├── start.py                # 机器人启动器
│   ├── bot.py                  # 机器人主进程
│   ├── config.py               # 配置（环境变量）
│   ├── dashboard.py            # Web 监控面板（localhost:7788）
│   ├── keys/
│   │   └── extract_key2.py     # LLDB 密钥提取
│   └── core/                   # 内部模块（AI、联系人、RAG 等）
├── SKILL.md                    # Claude Code skill 定义
├── requirements.txt
└── .gitignore
```

---

## 安全

- `keys/wechat_keys.json` — 已 gitignore，绝不提交
- 导出的聊天文件 — 已 gitignore
- API 密钥 — 仅通过环境变量读取
- 重签名微信仅用于密钥提取，完成后恢复原始签名

---

## 限制

- 仅支持 macOS（FSEvents + AppleScript）
- 微信 Mac 客户端必须在运行
- 大版本更新后需重新提取密钥
- 语音转录约 2.8 秒/条（Whisper `small` 模型）
