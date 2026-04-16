[English](README.md) | [中文](README_CN.md)

# wechat-export-bot

将微信 / Weixin 聊天记录导出为 Markdown（含语音转文字和 AI 纠错），还可以跑一个 AI 自动回复机器人——全部本地完成，不需要云端。

> **支持 macOS 和 Windows**，需要微信 / Weixin 桌面客户端。

---

## 功能

### 聊天记录导出
- 导出任意私聊或群聊为 Markdown
- 支持 **16 种消息类型**：文字、图片、语音、视频、表情包、公众号、小程序、文件、转账、红包、拍一拍、引用回复（嵌套）、通话、系统消息
- 语音消息自动转文字（Whisper，直接从本地数据库提取，不需要网络）
- AI 同音字纠错，HTML 注释标注（渲染不可见）
- 发送者统一显示微信原始昵称
- 跨数据库 `server_id` 去重（全局唯一）

### AI 机器人
- 监听指定对话的触发词
- 通过 Claude API 自动回复
- Mac：AppleScript 发送，不使用任何非官方协议
- Windows：Win32 键盘模拟发送
- 每个对话独立记忆，可配置历史窗口
- RAG 向量搜索支持长期上下文

---

## 原理

**聊天导出：**
```
加密数据库 → SQLCipher 解密 → 解析消息 → Markdown
语音（Mac）：     media_0.db BLOB → silk-v3-decoder → ffmpeg → Whisper
语音（Windows）： media_0.db BLOB → pilk → numpy 重采样 → Whisper
```

**AI 机器人：**
```
检测到 DB 变化 → SQLCipher 查询 → 触发词匹配 → Claude API → 发送回复
```

---

## 快速开始

### 前置安装

**Mac：**
```bash
brew install sqlcipher ffmpeg
pip install -r requirements.txt

# SILK 解码器（语音转录用）
git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder
cd /tmp/silk-v3-decoder/silk && make
```

**Windows：**
```bash
pip install -r requirements.txt
# 后续命令以管理员身份运行
```

### 1. 提取加密密钥

**Mac**（微信必须在运行中）：
```bash
sudo codesign --force --deep --sign - /Applications/WeChat.app
lldb -p $(pgrep -x WeChat) \
     -o "script exec(open('scripts/keys/extract_key2.py').read())" \
     -o "quit"
```

**Windows**（以管理员身份运行，Weixin 必须在运行中）：
```bash
python scripts\keys\extract_key_windows.py
```

### 2. 导出聊天记录

```bash
python3 scripts/export_chat.py --name "昵称"
python3 scripts/export_chat.py --name "群名" --group
```

### 3. 语音转文字（可选）

```bash
python3 scripts/transcribe_voices.py --name "昵称"
# Claude 自动纠正同音字错误（无需额外命令）
python3 scripts/export_chat.py --name "昵称" --voice-json 昵称_voice_map.json
```

### 4. 启动 AI 机器人（可选）

```bash
echo "wxid_xxx" > .watch
echo "sk-ant-xxx" > .apikey
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
wechat-export-bot/
├── scripts/
│   ├── export_chat.py            # 聊天记录 → Markdown
│   ├── transcribe_voices.py      # 语音 → Whisper 转录（Mac + Windows）
│   ├── start.py                  # 机器人启动器
│   ├── bot.py                    # 机器人主进程
│   ├── config.py                 # 配置——自动识别平台
│   ├── dashboard.py              # Web 监控面板（localhost:7788）
│   ├── keys/
│   │   ├── extract_key2.py       # Mac：LLDB 密钥提取
│   │   └── extract_key_windows.py   # Windows：Win32 内存扫描
│   └── core/                     # AI、联系人、RAG、向量存储、发送等
├── SKILL.md                      # Claude Code skill 定义
├── requirements.txt
└── .gitignore
```

---

## 平台对比

| | Mac | Windows |
|---|---|---|
| 密钥提取 | LLDB + Mach 内核 API | ReadProcessMemory Win32 |
| SILK 解码 | silk-v3-decoder（编译） | pilk（pip） |
| 音频处理 | ffmpeg → WAV | numpy 重采样 → array |
| 机器人发送 | AppleScript | Win32 keybd_event |
| 数据路径 | `~/Library/Containers/.../xwechat_files` | 注册表 / 全盘搜索自动检测 |

数据库结构、消息解析、AI 逻辑、RAG 两端完全相同。

---

## 安全

- `keys/wechat_keys.json` — 已 gitignore，绝不提交
- 导出的聊天文件 — 已 gitignore
- API 密钥 — 仅通过环境变量读取
- Mac：密钥提取完成后恢复微信原始签名

---

## 限制

- 微信 / Weixin 桌面客户端必须在运行
- 大版本更新后需重新提取密钥
- 语音转录约 2.8 秒/条（Whisper `small` 模型）
- Windows 机器人发送需要微信窗口保持打开
