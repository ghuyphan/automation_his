import sys
import ctypes
import pyautogui
from PIL import Image
from automation.config import AppConfig

# Set PyAutoGUI PAUSE globally to a low value
pyautogui.PAUSE = AppConfig.PYAUTOGUI_PAUSE
pyautogui.FAILSAFE = AppConfig.FAILSAFE

def minimize_console():
    """Automatically minimize the current command prompt window to reveal the HIS application behind it."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            # 6 is SW_MINIMIZE
            ctypes.windll.user32.ShowWindow(hwnd, 6)
            print("[Screen] Console minimized to reveal screen.")
    except Exception as e:
        print(f"[Screen] Could not minimize console window: {e}")

def focus_window(keywords=None, auto_maximize=None):
    """
    Finds a visible window whose title contains any of the keywords (case-insensitive)
    and brings it to the foreground/focuses it.
    """
    if keywords is None:
        keywords = AppConfig.keywords
    if auto_maximize is None:
        auto_maximize = AppConfig.auto_maximize

    try:
        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        GetWindowText = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible
        SetWindowPos = ctypes.windll.user32.SetWindowPos
        GetSystemMetrics = ctypes.windll.user32.GetSystemMetrics
        
        target_hwnd = []
        
        def foreach_window(hwnd, lParam):
            if IsWindowVisible(hwnd):
                length = GetWindowTextLength(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buff, length + 1)
                    title = buff.value
                    for keyword in keywords:
                        if keyword.lower() in title.lower():
                            target_hwnd.append((hwnd, title))
                            return False # stop enumeration
            return True
            
        EnumWindows(EnumWindowsProc(foreach_window), 0)
        
        if target_hwnd:
            hwnd, title = target_hwnd[0]
            # Restore first, then pin the HIS window to the primary monitor.
            # This avoids negative multi-monitor coordinates during OCR/clicking.
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            primary_w = GetSystemMetrics(0)
            primary_h = GetSystemMetrics(1)
            SetWindowPos(hwnd, None, 0, 0, primary_w, primary_h, 0x0040)
            if auto_maximize:
                # 3 is SW_SHOWMAXIMIZED (maximizes the window to ensure all columns are visible)
                ctypes.windll.user32.ShowWindow(hwnd, 3)
                print(f"[Screen] Successfully focused and maximized window: '{title}' (HWND: {hwnd})")
            else:
                # 9 is SW_RESTORE
                ctypes.windll.user32.ShowWindow(hwnd, 9)
                print(f"[Screen] Successfully focused and restored window: '{title}' (HWND: {hwnd})")
            # Set to foreground
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            return True
        else:
            print(f"[Screen] Could not find any window matching keywords: {keywords}")
            return False
    except Exception as e:
        print(f"[Screen] Error focusing window: {e}")
        return False

def capture_screenshot(region=None):
    """
    Capture a screenshot.
    Uses PyAutoGUI screenshot. If region is defined, captures target area (faster).
    """
    AppConfig.enable_dpi_awareness()
    if region:
        # region parameter is (left, top, width, height)
        # Clamping region coordinates to avoid negative/out-of-bounds grab errors
        x, y, w, h = region
        x = max(0, int(x))
        y = max(0, int(y))
        w = max(1, int(w))
        h = max(1, int(h))
        return pyautogui.screenshot(region=(x, y, w, h))
    return pyautogui.screenshot()
