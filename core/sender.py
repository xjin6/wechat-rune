"""通过 AppleScript 发送微信消息"""
import subprocess, time, re, threading

_send_lock = threading.Lock()  # 同一时刻只允许一个线程发送


def strip_blank_lines(text: str) -> str:
    """把连续空行压缩成单换行"""
    import re
    return re.sub(r'\n{2,}', '\n', text).strip()


def strip_markdown(text: str) -> str:
    """去掉微信不支持的markdown格式"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)   # **粗体**
    text = re.sub(r'\*(.+?)\*', r'\1', text)         # *斜体*
    text = re.sub(r'__(.+?)__', r'\1', text)         # __粗体__
    text = re.sub(r'_(.+?)_', r'\1', text)           # _斜体_
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # # 标题
    text = re.sub(r'`(.+?)`', r'\1', text)           # `代码`
    text = re.sub(r'```[\s\S]*?```', '', text)        # ```代码块```
    text = text.replace('/xin', '')                   # 防止回复里含触发词
    return text.strip()


def send(message: str) -> bool:
    """将消息通过剪贴板粘贴到当前微信窗口并发送（串行，不并发）"""
    with _send_lock:
        message = strip_markdown(message)
        message = strip_blank_lines(message)
        subprocess.run(["pbcopy"], input=message.encode("utf-8"))
    time.sleep(0.1)
    script = '''
tell application "System Events"
    set frontmost of process "WeChat" to true
    tell process "WeChat"
        keystroke "a" using {command down}
        key code 51
        keystroke "v" using {command down}
        delay 0.05
        keystroke return
    end tell
end tell
'''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.returncode == 0


def navigate_to(chat_name: str):
    """用 Cmd+F 搜索并切换到指定聊天"""
    script = f'''
tell application "WeChat"
    activate
end tell
delay 0.5
tell application "System Events"
    tell process "WeChat"
        keystroke "f" using {{command down}}
        delay 0.5
        keystroke "{chat_name}"
        delay 1
        keystroke return
        delay 0.5
        key code 53
    end tell
end tell
'''
    subprocess.run(["osascript", "-e", script], capture_output=True)
    time.sleep(0.5)
