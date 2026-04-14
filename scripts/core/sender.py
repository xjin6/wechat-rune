"""Send WeChat messages via AppleScript"""
import subprocess, time, re, threading

_send_lock = threading.Lock()  # Only one thread may send at a time


def strip_blank_lines(text: str) -> str:
    """Collapse consecutive blank lines into a single newline"""
    import re
    return re.sub(r'\n{2,}', '\n', text).strip()


def strip_markdown(text: str) -> str:
    """Strip markdown formatting not supported by WeChat"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)   # **bold**
    text = re.sub(r'\*(.+?)\*', r'\1', text)         # *italic*
    text = re.sub(r'__(.+?)__', r'\1', text)         # __bold__
    text = re.sub(r'_(.+?)_', r'\1', text)           # _italic_
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # # headings
    text = re.sub(r'`(.+?)`', r'\1', text)           # `inline code`
    text = re.sub(r'```[\s\S]*?```', '', text)        # ```code blocks```
    text = text.replace('/xin', '')                   # Prevent trigger word in replies
    return text.strip()


def send(message: str) -> bool:
    """Paste message via clipboard into the current WeChat window and send (serial, not concurrent)"""
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
    """Use Cmd+F to search and switch to the specified chat"""
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
