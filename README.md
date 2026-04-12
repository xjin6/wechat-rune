# wechat-ai-bot

在微信 Mac 客户端上运行的 AI 助手，监听指定对话，检测触发词后调用 Claude API 生成回复，通过 AppleScript 发送。

## 工作原理

```
微信写入消息 → FSEvents 检测 DB 变化 → sqlcipher 查新消息
→ 触发词匹配 → Claude API 生成回复 → AppleScript 发送
```

- **消息读取**：直接查询微信加密 SQLite 数据库（SQLCipher），无需解密整个 DB
- **历史上下文**：每个对话独立维护一个内存 deque（50条），互不串台
- **联系人识别**：按需查询 contact.db，备注和昵称双标识（如 `叶倩颖(chain)`）
- **发送**：AppleScript 控制微信 Mac 客户端输入框

## 目录结构

```
wechat-ai-bot/
├── bot.py          # 主入口
├── config.py       # 所有配置（改这里）
├── core/
│   ├── ai.py       # Claude API 调用
│   ├── contacts.py # 联系人查询
│   ├── decrypt.py  # 加密 DB 查询
│   ├── reader.py   # 消息读取/解析
│   └── sender.py   # AppleScript 发送
└── keys/
    ├── wechat_keys.json   # 数据库解密 key（已提取）
    ├── extract_key2.py    # 重新提取 key 用（WeChat 更新后）
    └── extract_key.lldb   # LLDB 脚本
```

## 配置（config.py）

```python
# 监听的对话（群ID 或个人 wxid）
WATCH_IDS = [
    "34422179829@chatroom",   # SSCI Team 群
    "wxid_iq08s7oagntq12",    # 杨晨
    "wxid_iv139ys0vn3412",    # HK
]

# 触发词
BOT_TRIGGERS = ["小昕", "/xin"]

# AI 设置
AI_MODEL = "claude-haiku-4-5-20251001"
AI_MAX_TOKENS = 1500
MAX_HISTORY = 50   # 每个对话的历史窗口
REPLY_PREFIX = "👾 "
```

## 触发规则

| 发送者 | 触发条件 |
|--------|---------|
| 自己 | 消息含 `/xin` |
| 其他人 | 消息含 `小昕`、`/xin` 或 @你 |
| AI 回复 | 不含 `/xin`（防自触发循环）|

## 启动

```bash
cd wechat-ai-bot
ANTHROPIC_API_KEY=sk-ant-... python3.9 bot.py
```

微信 Mac 客户端需保持运行。

## 依赖

```bash
brew install sqlcipher
python3.9 -m pip install anthropic watchdog zstandard
```

## 解密 key 失效时（WeChat 大版本更新后）

1. 重签名微信：
   ```bash
   sudo codesign --force --deep --sign - /Applications/WeChat.app
   ```
2. 确保微信已登录，运行提取脚本：
   ```bash
   cd keys
   PYTHONPATH=$(lldb -P) python3.9 extract_key2.py
   ```
   key 保存到 `keys/wechat_keys.json`

3. 恢复微信签名（重装即可）：从 App Store 重装微信

## 注意

- 需要 macOS 辅助功能权限（System Settings → Privacy → Accessibility → Terminal）
- 微信 Mac 客户端需保持在前台或可访问状态（用于 AppleScript 发送）
- key 通常在微信大版本更新或换账号时失效，小版本更新不影响
