"""
Send WeChat messages on Windows.

Strategy:
  1. Copy the reply text to the clipboard (using ctypes CF_UNICODETEXT for full Unicode support).
  2. Bring the WeChat window to the foreground.
  3. Simulate Ctrl+A → Delete → Ctrl+V → Enter to clear the input box, paste, and send.

Requirements: WeChat PC must be running and the target conversation must be open.
No extra Python packages needed — uses only the built-in ctypes module.
"""
import ctypes
import ctypes.wintypes
import time
import re
import threading

_send_lock = threading.Lock()   # One send at a time


# ── Text cleanup helpers ──────────────────────────────────────────

def strip_blank_lines(text: str) -> str:
    """Collapse consecutive blank lines into a single newline."""
    return re.sub(r'\n{2,}', '\n', text).strip()


def strip_markdown(text: str) -> str:
    """Strip markdown formatting not supported by WeChat."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*',     r'\1', text)
    text = re.sub(r'__(.+?)__',     r'\1', text)
    text = re.sub(r'_(.+?)_',       r'\1', text)
    text = re.sub(r'^#{1,6}\s+',    '',    text, flags=re.MULTILINE)
    text = re.sub(r'`(.+?)`',       r'\1', text)
    text = re.sub(r'```[\s\S]*?```', '',   text)
    text = text.replace('/xin', '')
    return text.strip()


# ── Windows clipboard ─────────────────────────────────────────────

_user32   = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT   = 13
GMEM_MOVEABLE    = 0x0002
KEYEVENTF_KEYUP  = 0x0002


def _copy_to_clipboard(text: str):
    """Write text to the Windows clipboard as Unicode."""
    encoded = (text + '\0').encode('utf-16-le')
    h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not h:
        return
    p = _kernel32.GlobalLock(h)
    ctypes.memmove(p, encoded, len(encoded))
    _kernel32.GlobalUnlock(h)
    _user32.OpenClipboard(0)
    _user32.EmptyClipboard()
    _user32.SetClipboardData(CF_UNICODETEXT, h)
    _user32.CloseClipboard()


# ── Window helpers ────────────────────────────────────────────────

def _find_wechat_hwnd() -> int:
    """Return the handle of the WeChat main window, or 0 if not found."""
    hwnd = _user32.FindWindowW("WeChatMainWndForPC", None)
    if not hwnd:
        hwnd = _user32.FindWindowW(None, "微信")
    return hwnd


def _keybd(vk: int, up: bool = False):
    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, 0)


# ── Public API ────────────────────────────────────────────────────

def send(message: str) -> bool:
    """Paste message via clipboard into the active WeChat chat window and send."""
    with _send_lock:
        message = strip_markdown(message)
        message = strip_blank_lines(message)
        _copy_to_clipboard(message)

    time.sleep(0.1)

    try:
        hwnd = _find_wechat_hwnd()
        if not hwnd:
            print("[!] WeChat window not found — is WeChat running?", flush=True)
            return False

        # Bring WeChat to foreground
        _user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)

        VK_CTRL   = 0x11
        VK_A      = 0x41
        VK_V      = 0x56
        VK_DEL    = 0x2E
        VK_RETURN = 0x0D

        # Ctrl+A  — select all text in input box
        _keybd(VK_CTRL); _keybd(VK_A); _keybd(VK_A, up=True); _keybd(VK_CTRL, up=True)
        time.sleep(0.05)
        # Delete  — clear selection
        _keybd(VK_DEL); _keybd(VK_DEL, up=True)
        time.sleep(0.05)
        # Ctrl+V  — paste
        _keybd(VK_CTRL); _keybd(VK_V); _keybd(VK_V, up=True); _keybd(VK_CTRL, up=True)
        time.sleep(0.05)
        # Enter   — send
        _keybd(VK_RETURN); _keybd(VK_RETURN, up=True)
        return True

    except Exception as e:
        print(f"[!] send() failed: {e}", flush=True)
        return False


def navigate_to(chat_name: str):
    """Use Ctrl+F to search for and switch to the specified conversation."""
    try:
        hwnd = _find_wechat_hwnd()
        if not hwnd:
            return

        _user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)

        VK_CTRL   = 0x11
        VK_F      = 0x46
        VK_V      = 0x56
        VK_RETURN = 0x0D
        VK_ESC    = 0x1B

        # Copy chat name to clipboard and open search
        _copy_to_clipboard(chat_name)

        _keybd(VK_CTRL); _keybd(VK_F); _keybd(VK_F, up=True); _keybd(VK_CTRL, up=True)
        time.sleep(0.5)

        # Paste the chat name into the search box
        _keybd(VK_CTRL); _keybd(VK_V); _keybd(VK_V, up=True); _keybd(VK_CTRL, up=True)
        time.sleep(0.8)

        # Confirm selection
        _keybd(VK_RETURN); _keybd(VK_RETURN, up=True)
        time.sleep(0.5)

        # Close search overlay
        _keybd(VK_ESC); _keybd(VK_ESC, up=True)
        time.sleep(0.3)

    except Exception as e:
        print(f"[!] navigate_to() failed: {e}", flush=True)
