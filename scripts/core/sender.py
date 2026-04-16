"""
Send WeChat messages — cross-platform (Mac + Windows)

Mac:     pbcopy to clipboard  +  AppleScript (osascript) to paste & send
Windows: ctypes clipboard     +  Win32 keybd_event to paste & send
"""
import sys
import time
import re
import threading

_send_lock = threading.Lock()


# ── Text cleanup (shared) ─────────────────────────────────────────

def strip_blank_lines(text: str) -> str:
    return re.sub(r'\n{2,}', '\n', text).strip()


def strip_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*',     r'\1', text)
    text = re.sub(r'__(.+?)__',     r'\1', text)
    text = re.sub(r'_(.+?)_',       r'\1', text)
    text = re.sub(r'^#{1,6}\s+',    '',    text, flags=re.MULTILINE)
    text = re.sub(r'`(.+?)`',       r'\1', text)
    text = re.sub(r'```[\s\S]*?```', '',   text)
    text = text.replace('/xin', '')
    return text.strip()


# ── Mac implementation ────────────────────────────────────────────

def _send_mac(message: str) -> bool:
    import subprocess
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


def _navigate_to_mac(chat_name: str):
    import subprocess
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


# ── Windows implementation ────────────────────────────────────────

def _copy_to_clipboard_win(text: str):
    import ctypes
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE  = 0x0002
    encoded = (text + '\0').encode('utf-16-le')
    h = ctypes.windll.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    p = ctypes.windll.kernel32.GlobalLock(h)
    ctypes.memmove(p, encoded, len(encoded))
    ctypes.windll.kernel32.GlobalUnlock(h)
    ctypes.windll.user32.OpenClipboard(0)
    ctypes.windll.user32.EmptyClipboard()
    ctypes.windll.user32.SetClipboardData(CF_UNICODETEXT, h)
    ctypes.windll.user32.CloseClipboard()


def _find_wechat_hwnd_win():
    import ctypes
    hwnd = ctypes.windll.user32.FindWindowW("WeChatMainWndForPC", None)
    if not hwnd:
        hwnd = ctypes.windll.user32.FindWindowW(None, "微信")
    return hwnd


def _keybd_win(vk: int, up: bool = False):
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, 0x0002 if up else 0, 0)


def _send_win(message: str) -> bool:
    import ctypes
    _copy_to_clipboard_win(message)
    time.sleep(0.1)
    hwnd = _find_wechat_hwnd_win()
    if not hwnd:
        print("[!] WeChat window not found — is WeChat running?", flush=True)
        return False
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    VK_CTRL, VK_A, VK_V, VK_DEL, VK_ENTER = 0x11, 0x41, 0x56, 0x2E, 0x0D
    _keybd_win(VK_CTRL);  _keybd_win(VK_A);   _keybd_win(VK_A,    up=True); _keybd_win(VK_CTRL, up=True)
    time.sleep(0.05)
    _keybd_win(VK_DEL);   _keybd_win(VK_DEL,  up=True)
    time.sleep(0.05)
    _keybd_win(VK_CTRL);  _keybd_win(VK_V);   _keybd_win(VK_V,    up=True); _keybd_win(VK_CTRL, up=True)
    time.sleep(0.05)
    _keybd_win(VK_ENTER); _keybd_win(VK_ENTER, up=True)
    return True


def _navigate_to_win(chat_name: str):
    import ctypes
    hwnd = _find_wechat_hwnd_win()
    if not hwnd:
        return
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    _copy_to_clipboard_win(chat_name)
    VK_CTRL, VK_F, VK_V, VK_ENTER, VK_ESC = 0x11, 0x46, 0x56, 0x0D, 0x1B
    _keybd_win(VK_CTRL); _keybd_win(VK_F);   _keybd_win(VK_F,     up=True); _keybd_win(VK_CTRL, up=True)
    time.sleep(0.5)
    _keybd_win(VK_CTRL); _keybd_win(VK_V);   _keybd_win(VK_V,     up=True); _keybd_win(VK_CTRL, up=True)
    time.sleep(0.8)
    _keybd_win(VK_ENTER); _keybd_win(VK_ENTER, up=True)
    time.sleep(0.5)
    _keybd_win(VK_ESC);  _keybd_win(VK_ESC,   up=True)


# ── Public API ────────────────────────────────────────────────────

def send(message: str) -> bool:
    """Paste message via clipboard into the active WeChat chat window and send."""
    with _send_lock:
        message = strip_markdown(message)
        message = strip_blank_lines(message)
        if sys.platform == 'win32':
            return _send_win(message)
        return _send_mac(message)


def navigate_to(chat_name: str):
    """Search for and switch to the specified WeChat conversation."""
    if sys.platform == 'win32':
        _navigate_to_win(chat_name)
    else:
        _navigate_to_mac(chat_name)
