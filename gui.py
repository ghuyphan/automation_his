import asyncio
import io
import os
import sys
import time
import re
import threading
from PIL import Image
import cv2
import numpy as np
import pyautogui
import ctypes
import qrcode

# Import CustomTkinter for modern GUI
import customtkinter as ctk
from tkinter import messagebox

# Import AppConfig and core automation components
from automation.config import AppConfig
from automation.screen import focus_window, minimize_console, capture_screenshot
from automation.templates import locate_all_templates, locate_single_template
from automation.ocr import run_ocr_on_pil_image, is_thyl_id_populated, extract_document_code, remove_vietnamese_diacritics
from automation.grid import calibrate_columns, calibrate_main_grid, has_visible_data_rows, scroll_main_grid_down, focus_tab_by_name, focus_sub_tab, get_visible_row_arrows
from automation.patient import find_ma_nguoi_benh_label, select_so_benh_an, is_so_benh_an, read_current_patient_code, normalize_patient_code
from automation.waiters import wait_until_dropdown_visible
from automation.engine import AutomationEngine
from automate import get_windows_clipboard_text, set_template_paths


# Set console encoding to UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Disable PyAutoGUI fail-safe to allow screen edges, but set short pause
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.3

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# File paths
ICON_TEMPLATE_PATH = resource_path("image3_icon.png")
CO_BUTTON_TEMPLATE_PATH = resource_path("co_button.png")
set_template_paths(ICON_TEMPLATE_PATH, CO_BUTTON_TEMPLATE_PATH)

FONT_FAMILY = "Segoe UI"
COLORS = {
    "app_bg": ("#f4f7fb", "#08111f"),
    "surface": ("#ffffff", "#101827"),
    "surface_alt": ("#eef6f6", "#132235"),
    "border": ("#d7e1e8", "#29394d"),
    "text": ("#142033", "#f8fafc"),
    "muted": ("#607085", "#a6b3c2"),
    "primary": ("#0f766e", "#14b8a6"),
    "primary_hover": ("#115e59", "#0d9488"),
    "accent": ("#2563eb", "#60a5fa"),
    "accent_hover": ("#1d4ed8", "#3b82f6"),
    "success": ("#059669", "#34d399"),
    "warning": ("#b45309", "#fbbf24"),
    "danger": ("#dc2626", "#f87171"),
    "danger_hover": ("#b91c1c", "#ef4444"),
    "control": ("#f8fafc", "#162235"),
}

# Windows API constants for clipboard image copy
CF_DIB = 8
CF_UNICODETEXT = 13
GHND = 0x0042  # GMEM_MOVEABLE | GMEM_ZEROINIT

def app_font(size, weight=None, family=FONT_FAMILY):
    if weight is None:
        return ctk.CTkFont(family=family, size=size)
    return ctk.CTkFont(family=family, size=size, weight=weight)

def make_card(parent, **kwargs):
    defaults = {
        "fg_color": COLORS["surface"],
        "border_width": 1,
        "border_color": COLORS["border"],
        "corner_radius": 8,
    }
    defaults.update(kwargs)
    return ctk.CTkFrame(parent, **defaults)

def make_primary_button(parent, **kwargs):
    defaults = {
        "height": 40,
        "corner_radius": 7,
        "fg_color": COLORS["primary"],
        "hover_color": COLORS["primary_hover"],
        "text_color": "#ffffff",
        "font": app_font(13, "bold"),
    }
    defaults.update(kwargs)
    return ctk.CTkButton(parent, **defaults)

def make_secondary_button(parent, **kwargs):
    defaults = {
        "height": 34,
        "corner_radius": 7,
        "fg_color": "transparent",
        "hover_color": ("#e7f1f0", "#1b3141"),
        "text_color": COLORS["primary"],
        "border_width": 1,
        "border_color": COLORS["border"],
        "font": app_font(12, "bold"),
    }
    defaults.update(kwargs)
    return ctk.CTkButton(parent, **defaults)

def copy_text_to_clipboard(text):
    """Copy Unicode text directly to the Windows clipboard."""
    data = (str(text) + "\0").encode("utf-16le")
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p

    hData = kernel32.GlobalAlloc(GHND, len(data))
    if not hData:
        raise RuntimeError("GlobalAlloc failed")
    pData = kernel32.GlobalLock(hData)
    if not pData:
        raise RuntimeError("GlobalLock failed")
    ctypes.memmove(pData, data, len(data))
    kernel32.GlobalUnlock(hData)

    if not user32.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, hData):
            raise RuntimeError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()

def copy_image_to_clipboard(image: Image.Image):
    """Copy a PIL Image directly to the Windows Clipboard as CF_DIB (Device Independent Bitmap)"""
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    # Convert image to BMP format in memory
    output = io.BytesIO()
    image.convert("RGB").save(output, "BMP")
    # BMP file header is 14 bytes; stripping it leaves raw DIB data
    data = output.getvalue()[14:]
    output.close()

    # Allocate global memory
    hData = kernel32.GlobalAlloc(GHND, len(data))
    pData = kernel32.GlobalLock(hData)
    
    # Copy data into the allocated memory
    ctypes.memmove(pData, data, len(data))
    kernel32.GlobalUnlock(hData)

    # Place data on the clipboard
    user32.OpenClipboard(None)
    user32.EmptyClipboard()
    user32.SetClipboardData(CF_DIB, hData)
    user32.CloseClipboard()

def validate_filter_datetime(date_val, time_val):
    """Validate optional HIS Thoi gian YL filter inputs before automation starts."""
    if not date_val and not time_val:
        return True, ""
    if time_val and not date_val:
        return False, "Vui long nhap ngay khi loc theo gio."
    if date_val:
        if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", date_val):
            return False, "Ngay YL phai co dang dd/mm/yyyy, vi du: 25/06/2026."
        try:
            time.strptime(date_val, "%d/%m/%Y")
        except ValueError:
            return False, "Ngay YL khong ton tai. Vui long kiem tra lai ngay/thang/nam."
    if time_val:
        match = re.fullmatch(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", time_val)
        if not match:
            return False, "Gio YL phai co dang H:MM hoac H:MM:SS, vi du: 7:10:00."
        hour, minute, second = [int(part or 0) for part in match.groups()]
        if hour > 23 or minute > 59 or second > 59:
            return False, "Gio YL khong hop le. Gio <= 23, phut/giay <= 59."
    return True, ""

def clamp(value, lower, upper):
    return max(lower, min(value, upper))

def get_screen_size(window):
    window.update_idletasks()
    return window.winfo_screenwidth(), window.winfo_screenheight()

def fit_window_size(window, width, height, max_width_pct=0.9, max_height_pct=0.9):
    screen_width, screen_height = get_screen_size(window)
    safe_width = int(screen_width * max_width_pct)
    safe_height = int(screen_height * max_height_pct)
    return min(width, safe_width), min(height, safe_height)

def place_window(window, width, height, anchor="center", margin=16):
    width, height = fit_window_size(window, width, height)
    screen_width, screen_height = get_screen_size(window)

    if anchor == "bottom-right":
        x = screen_width - width - margin
        y = screen_height - height - margin - 36
    else:
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2

    x = clamp(x, margin, max(margin, screen_width - width - margin))
    y = clamp(y, margin, max(margin, screen_height - height - margin))
    window.geometry(f"{width}x{height}+{x}+{y}")

def get_window_profile(window, mode="full"):
    screen_width, screen_height = get_screen_size(window)
    scale = clamp(min(screen_width / 1920, screen_height / 1080), 0.82, 1.15)

    profiles = {
        "full": {
            "width": clamp(int(screen_width * 0.224), 390, 460),
            "height": clamp(int(screen_height * 0.52), 500, 620),
            "min_width": clamp(int(360 * scale), 340, 400),
            "min_height": clamp(int(470 * scale), 430, 540),
        },
        "countdown": {
            "width": clamp(int(screen_width * 0.205), 350, 410),
            "height": clamp(int(screen_height * 0.305), 300, 350),
            "min_width": clamp(int(340 * scale), 320, 380),
            "min_height": clamp(int(285 * scale), 270, 330),
        },
        "mini": {
            "width": clamp(int(screen_width * 0.178), 310, 365),
            "height": clamp(int(screen_height * 0.205), 190, 240),
            "min_width": clamp(int(300 * scale), 290, 340),
            "min_height": clamp(int(180 * scale), 175, 220),
        },
    }
    return profiles[mode]

class CountdownWindow(ctk.CTkToplevel):
    def __init__(self, parent, seconds=3, title=None, warning=None):
        super().__init__(parent)
        self.seconds = seconds
        title = title or "DANG KHOI CHAY TU DONG HOA HIS"
        warning = warning or "Vui long khong di chuyen chuot..."
        
        self.overrideredirect(True)  # borderless
        self.attributes("-topmost", True)
        self.configure(fg_color=COLORS["surface"])
        
        place_window(self, 300, 220, anchor="bottom-right")
        
        # Styling
        self.lbl_title = ctk.CTkLabel(
            self, 
            text="ĐANG KHỞI CHẠY TỰ ĐỘNG HÓA HIS", 
            font=app_font(13, "bold"), 
            text_color=COLORS["muted"]
        )
        self.lbl_title.pack(pady=(35, 5))
        self.lbl_title.configure(text=title)
        
        self.lbl_num = ctk.CTkLabel(
            self, 
            text=str(self.seconds), 
            font=app_font(84, "bold"), 
            text_color=COLORS["primary"]
        )
        self.lbl_num.pack(pady=5)
        
        self.lbl_warning = ctk.CTkLabel(
            self, 
            text="Vui lòng không di chuyển chuột...", 
            font=app_font(12), 
            text_color=COLORS["text"]
        )
        self.lbl_warning.pack(pady=(0, 20))
        self.lbl_warning.configure(text=warning)
        
        self.update_countdown()
        
    def update_countdown(self):
        if self.seconds > 0:
            self.lbl_num.configure(text=str(self.seconds))
            self.seconds -= 1
            self.after(1000, self.update_countdown)
        else:
            self.destroy()

class ManualFilterCountdownWindow(ctk.CTkToplevel):
    def __init__(self, parent, seconds=12):
        super().__init__(parent)
        self.seconds = seconds
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=("#fff7ed", "#341609"))
        self.resizable(False, False)

        place_window(self, 300, 118, anchor="bottom-right")

        self.lbl_title = ctk.CTkLabel(
            self,
            text="CHỌN LỌC THỜI GIAN YL",
            font=app_font(13, "bold"),
            text_color=("#9a3412", "#fed7aa"),
        )
        self.lbl_title.pack(pady=(12, 2))

        self.lbl_num = ctk.CTkLabel(
            self,
            text=str(self.seconds),
            font=app_font(38, "bold"),
            text_color=("#ea580c", "#fb923c"),
        )
        self.lbl_num.pack()

        self.lbl_hint = ctk.CTkLabel(
            self,
            text="Tự chọn ngày/giờ trên HIS rồi bấm Đóng",
            font=app_font(11),
            text_color=("#7c2d12", "#ffedd5"),
        )
        self.lbl_hint.pack(pady=(0, 8))
        self.update_countdown()

    def update_countdown(self):
        if self.seconds > 0:
            self.lbl_num.configure(text=str(self.seconds))
            self.seconds -= 1
            self.after(1000, self.update_countdown)
        else:
            self.destroy()

class StopOverlay(ctk.CTkToplevel):
    def __init__(self, parent, stop_command):
        super().__init__(parent)
        self.stop_command = stop_command
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=("#fef2f2", "#3b1012"))
        self.resizable(False, False)

        place_window(self, 210, 136, anchor="bottom-right")

        self.btn_stop = ctk.CTkButton(
            self,
            text="DỪNG NGAY",
            command=self.stop_command,
            height=42,
            corner_radius=7,
            fg_color=COLORS["danger"],
            hover_color=COLORS["danger_hover"],
            text_color="#ffffff",
            font=app_font(15, "bold"),
        )
        self.btn_stop.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        self.lbl_status = ctk.CTkLabel(
            self,
            text="Đang chạy",
            font=app_font(11, "bold"),
            text_color=COLORS["danger"],
        )
        self.lbl_status.pack(pady=(0, 2))

        self.lbl_activity = ctk.CTkLabel(
            self,
            text="Đang chờ nhật ký...",
            font=app_font(10),
            text_color=("#7f1d1d", "#fee2e2"),
            wraplength=185,
            justify="center",
        )
        self.lbl_activity.pack(padx=10, pady=(0, 4))

        self.lbl_hint = ctk.CTkLabel(
            self,
            text="Có thể bấm khi bảng điều khiển đang ẩn",
            font=app_font(10),
            text_color=("#7f1d1d", "#fecaca"),
        )
        self.lbl_hint.pack(pady=(0, 7))

    def mark_stop_requested(self):
        self.configure(fg_color=("#fecaca", "#7f1d1d"))
        self.btn_stop.configure(
            text="ĐANG DỪNG...",
            state="disabled",
            fg_color=COLORS["danger_hover"],
        )
        self.lbl_status.configure(text="Đã nhận lệnh dừng")
        self.lbl_hint.configure(text="Chờ bước OCR/click hiện tại kết thúc")

    def update_activity(self, message):
        clean_message = " ".join(str(message).split())
        if len(clean_message) > 86:
            clean_message = clean_message[:83] + "..."
        self.lbl_activity.configure(text=clean_message)

class CustomAlertWindow(ctk.CTkToplevel):
    def __init__(self, parent, title, rows_cleared, unique_codes_count):
        super().__init__(parent)
        
        self.overrideredirect(True)  # borderless
        self.attributes("-topmost", True)
        self.configure(fg_color=COLORS["surface"])
        
        place_window(self, 360, 230, anchor="bottom-right")
        
        # Styling
        self.lbl_icon = ctk.CTkLabel(self, text="OK", font=app_font(32, "bold"), text_color=COLORS["success"])
        self.lbl_icon.pack(pady=(25, 5))
        
        self.lbl_title = ctk.CTkLabel(
            self, 
            text=title.upper(), 
            font=app_font(16, "bold"), 
            text_color=COLORS["success"]
        )
        self.lbl_title.pack(pady=5)
        
        summary_text = f"Tổng số dòng đã xử lý: {rows_cleared}\nSố mã chứng từ duy nhất: {unique_codes_count}"
        self.lbl_summary = ctk.CTkLabel(
            self, 
            text=summary_text, 
            font=app_font(12), 
            text_color=COLORS["text"], 
            justify="center"
        )
        self.lbl_summary.pack(pady=10)
        
        self.btn_ok = make_primary_button(
            self, 
            text="ĐỒNG Ý", 
            command=self.destroy, 
            width=120, 
            height=34
        )
        self.btn_ok.pack(pady=(5, 20))

class QRCodeWindow(ctk.CTkToplevel):
    def __init__(self, parent, text):
        super().__init__(parent)
        
        self.title("Mã QR chứng từ")
        self.attributes("-topmost", True)
        self.configure(fg_color=COLORS["app_bg"])
        self.resizable(False, False)
        
        place_window(self, 360, 540, anchor="center")
        
        # Title Label
        self.lbl_title = ctk.CTkLabel(
            self, 
            text="Mã QR danh sách chứng từ", 
            font=app_font(17, "bold"), 
            text_color=COLORS["text"]
        )
        self.lbl_title.pack(pady=(20, 10))
        
        # Generate QR PIL Image
        display_text = text.strip() if text.strip() else "Không có mã"
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=2,
        )
        qr.add_data(display_text)
        qr.make(fit=True)
        
        self.pil_qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        display_img = self.pil_qr_img.resize((260, 260), Image.Resampling.LANCZOS)
        self.ctk_qr_img = ctk.CTkImage(light_image=display_img, dark_image=display_img, size=(260, 260))
        
        # Image Label
        self.qr_card = make_card(self, fg_color="#ffffff", border_color=("#d7e1e8", "#d7e1e8"))
        self.qr_card.pack(pady=5)
        self.lbl_qr = ctk.CTkLabel(self.qr_card, image=self.ctk_qr_img, text="")
        self.lbl_qr.pack(padx=12, pady=12)
        
        # Subtext / Instruction
        self.lbl_info = ctk.CTkLabel(
            self, 
            text="Quét mã QR để xem danh sách trên điện thoại\nhoặc dùng các nút sao chép bên dưới.", 
            font=app_font(11), 
            text_color=COLORS["muted"],
            justify="center"
        )
        self.lbl_info.pack(pady=(5, 15))
        
        # Actions Row
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(fill="x", padx=25, pady=5)
        self.btn_frame.grid_columnconfigure(0, weight=1)
        self.btn_frame.grid_columnconfigure(1, weight=1)
        
        self.btn_copy_img = make_secondary_button(
            self.btn_frame, 
            text="Sao chép ảnh QR", 
            command=self.copy_qr_image, 
            height=34
        )
        self.btn_copy_img.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        
        self.btn_copy_text = make_secondary_button(
            self.btn_frame, 
            text="Sao chép dạng chữ", 
            command=self.copy_code_text, 
            height=34,
            text_color=COLORS["success"],
            hover_color=("#e8f8f2", "#123429")
        )
        self.btn_copy_text.grid(row=0, column=1, padx=(5, 0), sticky="ew")
        
        # Dismiss Button
        self.btn_close = make_primary_button(
            self, 
            text="ĐÓNG", 
            command=self.destroy, 
            height=36
        )
        self.btn_close.pack(pady=(10, 16), fill="x", padx=25)
        
        self.text_to_copy = display_text
        
    def copy_code_text(self):
        if self.text_to_copy.strip():
            self.clipboard_clear()
            self.clipboard_append(self.text_to_copy)
            messagebox.showinfo("Bộ nhớ tạm", "Đã sao chép danh sách mã chứng từ.")
        else:
            messagebox.showwarning("Bộ nhớ tạm", "Không có mã để sao chép.")
            
    def copy_qr_image(self):
        try:
            copy_image_to_clipboard(self.pil_qr_img)
            messagebox.showinfo("Bộ nhớ tạm", "Đã sao chép hình ảnh mã QR.")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể sao chép hình ảnh: {e}")

class HISAutomatorGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Configure window
        self.title("Tự động hóa HIS")
        full_profile = get_window_profile(self, "full")
        place_window(self, full_profile["width"], full_profile["height"], anchor="bottom-right")
        self.minsize(full_profile["min_width"], full_profile["min_height"])
        self.resizable(True, True)
        self.attributes("-topmost", True)
        
        # Set default appearance mode to Light
        ctk.set_appearance_mode("Light")
        
        # Dual-mode background colors for main window
        self.configure(fg_color=COLORS["app_bg"])

        # State variables
        self.is_running = False
        self.cleared_codes = []
        self.processed_rows_count = 0
        self.loop_thread = None
        self.async_loop = None
        self.worker_active = False
        self.stop_requested = False
        self.run_failed = False
        self.stop_overlay = None
        self.qr_window = None
        self.debug_dir = None
        self.scanned_rows_count = 0
        self.blank_thyl_count = 0
        self.ocr_failed_count = 0
        self.log_history = []  # Stores raw logs for search filtering

        # Layout grids
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Create GUI Components
        self.create_compact_layout()
        self.bind("<Configure>", self.on_window_configure)
        
        self.log("Hệ thống đã khởi tạo. Sẵn sàng tự động hóa.")
        self.log("Khớp tỷ lệ màn hình: đã bật (80% - 150%).")

    def content_wrap_width(self, fallback=360):
        width = self.winfo_width()
        if width <= 1:
            width = fallback
        return max(240, width - 50)

    def on_window_configure(self, event):
        if event.widget is not self:
            return
        if hasattr(self, "latest_activity"):
            self.latest_activity.configure(wraplength=self.content_wrap_width())

    def create_compact_layout(self):
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        self.content_frame.grid_columnconfigure(0, weight=1)
        self.content_frame.grid_rowconfigure(7, weight=1)

        self.metrics_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self.metrics_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.metrics_frame.grid_columnconfigure((0, 1), weight=1)

        self.cleared_chip = make_card(self.metrics_frame, fg_color=("#f7fbfa", "#0f1c28"))
        self.cleared_chip.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        self.cleared_chip.grid_columnconfigure(1, weight=1)
        self.lbl_cleared_txt = ctk.CTkLabel(self.cleared_chip, text="Dòng xử lý", font=app_font(11), text_color=COLORS["muted"])
        self.lbl_cleared_txt.grid(row=0, column=0, padx=(12, 4), pady=(9, 7), sticky="w")
        self.lbl_cleared_num = ctk.CTkLabel(self.cleared_chip, text="0", font=app_font(20, "bold"), text_color=COLORS["success"])
        self.lbl_cleared_num.grid(row=0, column=1, padx=(4, 12), pady=(7, 7), sticky="e")

        self.unique_chip = make_card(self.metrics_frame, fg_color=("#f7f9ff", "#121d31"))
        self.unique_chip.grid(row=0, column=1, padx=(5, 0), sticky="ew")
        self.unique_chip.grid_columnconfigure(1, weight=1)
        self.lbl_unique_txt = ctk.CTkLabel(self.unique_chip, text="Mã chứng từ", font=app_font(11), text_color=COLORS["muted"])
        self.lbl_unique_txt.grid(row=0, column=0, padx=(12, 4), pady=(9, 7), sticky="w")
        self.lbl_unique_num = ctk.CTkLabel(self.unique_chip, text="0", font=app_font(20, "bold"), text_color=COLORS["accent"])
        self.lbl_unique_num.grid(row=0, column=1, padx=(4, 12), pady=(7, 7), sticky="e")

        self.patient_frame = make_card(self.content_frame)
        self.patient_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.patient_frame.grid_columnconfigure(0, weight=1)

        self.patient_top_frame = ctk.CTkFrame(self.patient_frame, fg_color="transparent")
        self.patient_top_frame.grid(row=0, column=0, padx=12, pady=(10, 5), sticky="ew")
        self.patient_top_frame.grid_columnconfigure(0, weight=1)
        self.lbl_patient = ctk.CTkLabel(self.patient_top_frame, text="Mã người bệnh", font=app_font(12, "bold"), text_color=COLORS["text"])
        self.lbl_patient.grid(row=0, column=0, sticky="w")
        self.cb_cap_cuu = ctk.CTkCheckBox(
            self.patient_top_frame,
            text="Cấp cứu (CC)",
            font=app_font(12),
            checkbox_width=18,
            checkbox_height=18,
            fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"],
        )
        self.cb_cap_cuu.grid(row=0, column=1, sticky="e")
        self.entry_patient = ctk.CTkEntry(
            self.patient_frame,
            placeholder_text="Nhập hoặc dán mã người bệnh",
            height=38,
            corner_radius=7,
            border_color=COLORS["border"],
            fg_color=COLORS["control"],
            font=app_font(13),
        )
        self.entry_patient.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="ew")

        self.options_frame = make_card(self.content_frame)
        self.options_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.options_frame.grid_columnconfigure(0, weight=1)

        self.slider_frame = ctk.CTkFrame(self.options_frame, fg_color="transparent")
        self.slider_frame.grid(row=0, column=0, padx=12, pady=(11, 8), sticky="ew")
        self.slider_frame.grid_columnconfigure(0, weight=1)
        self.delay_label = ctk.CTkLabel(self.slider_frame, text="Độ trễ thao tác", font=app_font(12, "bold"), text_color=COLORS["text"])
        self.delay_label.grid(row=0, column=0, sticky="w")
        self.delay_value_label = ctk.CTkLabel(self.slider_frame, text="0.2s", width=44, height=24, corner_radius=12, fg_color=COLORS["surface_alt"], font=app_font(12, "bold"), text_color=COLORS["primary"])
        self.delay_value_label.grid(row=0, column=1, sticky="e")
        self.delay_slider = ctk.CTkSlider(
            self.slider_frame,
            from_=0.2,
            to=2.0,
            number_of_steps=9,
            height=18,
            button_color=COLORS["primary"],
            button_hover_color=COLORS["primary_hover"],
            progress_color=COLORS["primary"],
        )
        self.delay_slider.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="ew")
        self.delay_slider.set(0.2)
        self.delay_slider.configure(command=self.update_delay_label)

        self.switch_frame = ctk.CTkFrame(self.options_frame, fg_color="transparent")
        self.switch_frame.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="ew")
        self.switch_frame.grid_columnconfigure((0, 1), weight=1)
        self.dry_run_switch = ctk.CTkSwitch(
            self.switch_frame,
            text="Chạy thử",
            font=app_font(12),
            progress_color=COLORS["primary"],
            button_color="#ffffff",
            button_hover_color="#f8fafc",
        )
        self.dry_run_switch.grid(row=0, column=0, sticky="w")
        self.dry_run_switch.deselect()
        self.max_switch = ctk.CTkSwitch(
            self.switch_frame,
            text="Phóng to HIS",
            font=app_font(12),
            progress_color=COLORS["primary"],
            button_color="#ffffff",
            button_hover_color="#f8fafc",
        )
        self.max_switch.grid(row=0, column=1, sticky="e")
        self.max_switch.select()

        self.btn_action = make_primary_button(
            self.content_frame,
            text="Bắt đầu",
            height=44,
            font=app_font(15, "bold"),
            command=self.on_start_clicked,
        )
        self.btn_action.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        self.progress_frame = make_card(self.content_frame, fg_color=COLORS["surface_alt"])
        self.progress_frame.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        self.progress_header = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        self.progress_header.pack(fill="x", padx=12, pady=(10, 2))
        self.progress_header.grid_columnconfigure(0, weight=1)
        self.lbl_progress = ctk.CTkLabel(self.progress_header, text="Tiến độ: đang chờ", font=app_font(12, "bold"), text_color=COLORS["text"], anchor="w")
        self.lbl_progress.grid(row=0, column=0, sticky="ew")
        self.lbl_status_val = ctk.CTkLabel(
            self.progress_header,
            text="Sẵn sàng",
            width=82,
            height=24,
            corner_radius=12,
            fg_color=("#fff7ed", "#34210d"),
            font=app_font(11, "bold"),
            text_color=COLORS["warning"],
        )
        self.lbl_status_val.grid(row=0, column=1, sticky="e")
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=8, progress_color=COLORS["primary"])
        self.progress_bar.pack(fill="x", padx=12, pady=(4, 10))
        self.progress_bar.set(0)

        self.latest_activity = ctk.CTkLabel(
            self.content_frame,
            text="Sẵn sàng.",
            font=app_font(11),
            text_color=COLORS["muted"],
            wraplength=self.content_wrap_width(),
            justify="left",
            anchor="w",
        )
        self.latest_activity.grid(row=5, column=0, sticky="ew", pady=(0, 10))

        self.countdown_frame = make_card(self.content_frame, fg_color=("#ecfdf5", "#082f2a"), border_color=("#99f6e4", "#115e59"))
        self.countdown_frame.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        self.countdown_frame.grid_columnconfigure(0, weight=1)
        self.lbl_countdown_num = ctk.CTkLabel(self.countdown_frame, text="", font=app_font(48, "bold"), text_color=COLORS["primary"])
        self.lbl_countdown_num.grid(row=0, column=0, padx=10, pady=(6, 0))
        self.btn_continue_now = make_secondary_button(
            self.countdown_frame,
            text="Tiếp tục ngay",
            height=30,
            command=self.skip_current_countdown,
        )
        self.btn_continue_now.grid(row=1, column=0, padx=12, pady=(0, 10), sticky="ew")
        self.countdown_frame.grid_remove()

        self.tab_view = ctk.CTkTabview(
            self.content_frame,
            height=142,
            corner_radius=8,
            border_width=1,
            border_color=COLORS["border"],
            fg_color=COLORS["surface"],
            segmented_button_fg_color=COLORS["surface_alt"],
            segmented_button_selected_color=COLORS["primary"],
            segmented_button_selected_hover_color=COLORS["primary_hover"],
            segmented_button_unselected_hover_color=("#d9ece9", "#20364a"),
        )
        self.tab_view.grid(row=7, column=0, sticky="nsew")
        self.logs_tab = self.tab_view.add("Nhật ký")
        self.results_tab = self.tab_view.add("Kết quả")
        self.advanced_tab = self.tab_view.add("Cài đặt")

        self.logs_tab.grid_columnconfigure(0, weight=1)
        self.logs_tab.grid_rowconfigure(1, weight=1)
        self.results_tab.grid_columnconfigure(0, weight=1)
        self.results_tab.grid_rowconfigure(0, weight=1)
        self.advanced_tab.grid_columnconfigure(0, weight=1)

        self.entry_search_logs = ctk.CTkEntry(self.logs_tab, placeholder_text="Tìm nhật ký...", height=30, corner_radius=7, fg_color=COLORS["control"], border_color=COLORS["border"], font=app_font(11))
        self.entry_search_logs.grid(row=0, column=0, padx=8, pady=(8, 5), sticky="ew")
        self.entry_search_logs.bind("<KeyRelease>", self.filter_logs)
        self.log_box = ctk.CTkTextbox(self.logs_tab, font=ctk.CTkFont(family="Consolas", size=10), corner_radius=7, fg_color=COLORS["control"], border_width=1, border_color=COLORS["border"])
        self.log_box.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="nsew")

        self.result_box = ctk.CTkTextbox(self.results_tab, font=ctk.CTkFont(family="Consolas", size=11), corner_radius=7, fg_color=COLORS["control"], border_width=1, border_color=COLORS["border"])
        self.result_box.grid(row=0, column=0, padx=8, pady=(8, 6), sticky="nsew")
        self.btn_action_frame = ctk.CTkFrame(self.results_tab, fg_color="transparent")
        self.btn_action_frame.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="ew")
        self.btn_action_frame.grid_columnconfigure((0, 1), weight=1)
        self.btn_copy = make_secondary_button(self.btn_action_frame, text="Sao chép", command=self.copy_to_clipboard, height=32)
        self.btn_copy.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.btn_show_qr = make_secondary_button(self.btn_action_frame, text="Mã QR", command=self.show_qr_code, height=32)
        self.btn_show_qr.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.lbl_kw = ctk.CTkLabel(self.advanced_tab, text="Từ khóa tiêu đề HIS", font=app_font(11, "bold"), text_color=COLORS["text"])
        self.lbl_kw.grid(row=0, column=0, padx=8, pady=(10, 3), sticky="w")
        self.entry_keywords = ctk.CTkEntry(self.advanced_tab, height=32, corner_radius=7, fg_color=COLORS["control"], border_color=COLORS["border"], font=app_font(11))
        self.entry_keywords.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="ew")
        self.entry_keywords.insert(0, "HOANMY_SAIGON, eHospital")
        self.theme_switch = ctk.CTkSwitch(self.advanced_tab, text="Giao diện tối", font=app_font(12), progress_color=COLORS["primary"], command=self.toggle_theme)
        self.theme_switch.grid(row=2, column=0, padx=8, pady=(0, 8), sticky="w")

        # Optimization Mode Selector
        self.lbl_opt_mode = ctk.CTkLabel(self.advanced_tab, text="Chế độ tối ưu hóa", font=app_font(11, "bold"), text_color=COLORS["text"])
        self.lbl_opt_mode.grid(row=3, column=0, padx=8, pady=(10, 3), sticky="w")
        self.opt_mode_var = ctk.StringVar(value="Balanced")
        self.opt_mode_menu = ctk.CTkOptionMenu(
            self.advanced_tab,
            values=["Safe", "Balanced", "Fast"],
            variable=self.opt_mode_var,
            fg_color=COLORS["control"],
            button_color=COLORS["primary"],
            button_hover_color=COLORS["primary_hover"],
            text_color=COLORS["text"],
            font=app_font(11)
        )
        self.opt_mode_menu.grid(row=4, column=0, padx=8, pady=(0, 8), sticky="ew")

        # Custom wait timeouts
        self.lbl_timeouts = ctk.CTkLabel(self.advanced_tab, text="Thời gian chờ tối đa (giây)", font=app_font(11, "bold"), text_color=COLORS["text"])
        self.lbl_timeouts.grid(row=5, column=0, padx=8, pady=(10, 3), sticky="w")
        
        self.timeout_frame = ctk.CTkFrame(self.advanced_tab, fg_color="transparent")
        self.timeout_frame.grid(row=6, column=0, padx=8, pady=(0, 8), sticky="ew")
        self.timeout_frame.grid_columnconfigure((0, 1), weight=1)
        
        self.entry_popup_timeout = ctk.CTkEntry(self.timeout_frame, placeholder_text="Chờ popup (2.0s)", height=32, corner_radius=7, fg_color=COLORS["control"], border_color=COLORS["border"], font=app_font(11))
        self.entry_popup_timeout.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.entry_popup_timeout.insert(0, "2.0")
        
        self.entry_grid_timeout = ctk.CTkEntry(self.timeout_frame, placeholder_text="Chờ lưới (3.0s)", height=32, corner_radius=7, fg_color=COLORS["control"], border_color=COLORS["border"], font=app_font(11))
        self.entry_grid_timeout.grid(row=0, column=1, padx=(4, 0), sticky="ew")
        self.entry_grid_timeout.insert(0, "3.0")

        self.time_filter_frame = ctk.CTkFrame(self.advanced_tab, fg_color="transparent")
        self.time_filter_frame.grid(row=7, column=0, padx=8, pady=(0, 8), sticky="ew")
        self.time_filter_frame.grid_columnconfigure((0, 1), weight=1)
        self.lbl_time = ctk.CTkLabel(self.time_filter_frame, text="Thời gian YL", font=app_font(11, "bold"), text_color=COLORS["text"])
        self.lbl_time.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 3))
        self.entry_date = ctk.CTkEntry(self.time_filter_frame, height=32, placeholder_text="dd/mm/yyyy", corner_radius=7, fg_color=COLORS["control"], border_color=COLORS["border"], font=app_font(11))
        self.entry_time = ctk.CTkEntry(self.time_filter_frame, height=32, placeholder_text="H:MM:SS", corner_radius=7, fg_color=COLORS["control"], border_color=COLORS["border"], font=app_font(11))
        self.entry_date.grid(row=1, column=0, padx=(0, 4), sticky="ew")
        self.entry_time.grid(row=1, column=1, padx=(4, 0), sticky="ew")
        self.entry_date.insert(0, time.strftime("%d/%m/%Y"))
        self.running_hidden_widgets = [
            self.metrics_frame,
            self.patient_frame,
            self.options_frame,
            self.tab_view,
        ]
        self.skip_countdown_requested = False

    def show_countdown_mode(self, seconds, allow_continue=False):
        self.lbl_countdown_num.configure(text=str(seconds))
        if allow_continue:
            self.btn_continue_now.grid()
        else:
            self.btn_continue_now.grid_remove()
        self.countdown_frame.grid()
        countdown_profile = get_window_profile(self, "countdown")
        self.latest_activity.configure(wraplength=self.content_wrap_width(countdown_profile["width"]))
        self.minsize(countdown_profile["min_width"], countdown_profile["min_height"])
        place_window(self, countdown_profile["width"], countdown_profile["height"], anchor="bottom-right")

    def hide_countdown_mode(self):
        self.countdown_frame.grid_remove()
        self.skip_countdown_requested = False
        if self.is_running:
            mini_profile = get_window_profile(self, "mini")
            self.latest_activity.configure(wraplength=self.content_wrap_width(mini_profile["width"]))
            self.minsize(mini_profile["min_width"], mini_profile["min_height"])
            place_window(self, mini_profile["width"], mini_profile["height"], anchor="bottom-right")

    def skip_current_countdown(self):
        self.skip_countdown_requested = True
        self.set_activity("Đang tiếp tục ngay...")

    def enter_running_mini_mode(self):
        for widget in self.running_hidden_widgets:
            widget.grid_remove()
        self.content_frame.grid_rowconfigure(7, weight=0)
        mini_profile = get_window_profile(self, "mini")
        self.latest_activity.configure(wraplength=self.content_wrap_width(mini_profile["width"]))
        self.minsize(mini_profile["min_width"], mini_profile["min_height"])
        place_window(self, mini_profile["width"], mini_profile["height"], anchor="bottom-right")
        self.lift()

    def exit_running_mini_mode(self):
        self.countdown_frame.grid_remove()
        self.skip_countdown_requested = False
        self.metrics_frame.grid()
        self.patient_frame.grid()
        self.options_frame.grid()
        self.tab_view.grid()
        self.content_frame.grid_rowconfigure(7, weight=1)
        full_profile = get_window_profile(self, "full")
        self.latest_activity.configure(wraplength=self.content_wrap_width(full_profile["width"]))
        self.minsize(full_profile["min_width"], full_profile["min_height"])
        place_window(self, full_profile["width"], full_profile["height"], anchor="bottom-right")

    def create_sidebar(self):
        # Sidebar Frame with Custom Dual Background
        self.sidebar_frame = ctk.CTkFrame(self, corner_radius=0, fg_color=("#f8fafc", "#0f172a"), border_width=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.sidebar_frame.grid_columnconfigure(0, weight=1)
        self.sidebar_frame.grid_rowconfigure(6, weight=1) 

        # Logo and Title
        self.title_label = ctk.CTkLabel(
            self.sidebar_frame, 
            text="Tá»± Äá»™ng HÃ³a\neHospital", 
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"), 
            text_color=("#4f46e5", "#6366f1")
        )
        self.title_label.grid(row=0, column=0, padx=20, pady=(30, 20))

        # Divider line
        self.line = ctk.CTkFrame(self.sidebar_frame, height=2, fg_color=("#e2e8f0", "#2e3748"))
        self.line.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 15))

        # --- Settings Section ---
        self.settings_label = ctk.CTkLabel(
            self.sidebar_frame, 
            text="Cáº¤U HÃŒNH Há»† THá»NG", 
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), 
            text_color=("#475569", "#94a3b8")
        )
        self.settings_label.grid(row=2, column=0, padx=20, pady=(5, 5), sticky="w")

        # 1. Delay Slider
        self.slider_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.slider_frame.grid(row=3, column=0, padx=20, pady=10, sticky="ew")
        self.slider_frame.grid_columnconfigure(0, weight=1)
        
        self.delay_label = ctk.CTkLabel(
            self.slider_frame, 
            text="Äá»™ trá»… click (giÃ¢y):", 
            font=ctk.CTkFont(size=12),
            text_color=("#0f172a", "#f8fafc")
        )
        self.delay_label.grid(row=0, column=0, sticky="w")
        self.delay_value_label = ctk.CTkLabel(
            self.slider_frame, 
            text="0.2s", 
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#4f46e5", "#6366f1")
        )
        self.delay_value_label.grid(row=0, column=1, sticky="e")
        
        self.delay_slider = ctk.CTkSlider(
            self.slider_frame, 
            from_=0.2, 
            to=2.0, 
            number_of_steps=9, 
            height=16,
            fg_color=("#cbd5e1", "#1e293b"),
            progress_color=("#4f46e5", "#6366f1"),
            button_color=("#4f46e5", "#6366f1"),
            button_hover_color=("#4338ca", "#4f46e5")
        )
        self.delay_slider.grid(row=1, column=0, columnspan=2, pady=5, sticky="ew")
        self.delay_slider.set(0.2)
        self.delay_slider.configure(command=self.update_delay_label)

        # 2. Auto-Maximize Checkbox
        self.max_switch = ctk.CTkSwitch(
            self.sidebar_frame, 
            text="PhÃ³ng to App HIS", 
            font=ctk.CTkFont(size=12),
            text_color=("#0f172a", "#f8fafc"),
            fg_color=("#cbd5e1", "#334155"),
            progress_color=("#4f46e5", "#6366f1")
        )
        self.max_switch.grid(row=4, column=0, padx=20, pady=10, sticky="w")
        self.max_switch.select()

        # 3. Dry Run Switch
        self.dry_run_switch = ctk.CTkSwitch(
            self.sidebar_frame, 
            text="Chá»‰ cháº¡y thá»­ (KhÃ´ng xÃ³a)", 
            font=ctk.CTkFont(size=12),
            text_color=("#0f172a", "#f8fafc"),
            fg_color=("#cbd5e1", "#334155"),
            progress_color=("#4f46e5", "#6366f1")
        )
        self.dry_run_switch.grid(row=5, column=0, padx=20, pady=10, sticky="w")

        # 4. Window Title Keywords Entry
        self.keywords_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.keywords_frame.grid(row=6, column=0, padx=20, pady=10, sticky="ew")
        
        self.lbl_kw = ctk.CTkLabel(
            self.keywords_frame, 
            text="Tá»« khÃ³a tiÃªu Ä‘á» app HIS:", 
            font=ctk.CTkFont(size=11), 
            text_color=("#64748b", "#94a3b8")
        )
        self.lbl_kw.pack(anchor="w")
        
        self.entry_keywords = ctk.CTkEntry(
            self.keywords_frame, 
            font=ctk.CTkFont(size=12), 
            height=28, 
            fg_color=("#ffffff", "#090d16"), 
            border_color=("#cbd5e1", "#1e293b"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.entry_keywords.pack(fill="x", pady=2)
        self.entry_keywords.insert(0, "HOANMY_SAIGON, eHospital")

        # 4. Theme switcher at bottom
        self.bottom_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.bottom_frame.grid(row=7, column=0, padx=20, pady=20, sticky="ew")
        
        self.theme_switch = ctk.CTkSwitch(
            self.bottom_frame, 
            text="Giao diá»‡n Tá»‘i", 
            font=ctk.CTkFont(size=12),
            text_color=("#0f172a", "#f8fafc"),
            fg_color=("#cbd5e1", "#334155"),
            progress_color=("#4f46e5", "#6366f1"),
            command=self.toggle_theme
        )
        self.theme_switch.pack(anchor="w", pady=5)

    def toggle_theme(self):
        if self.theme_switch.get() == 1:
            ctk.set_appearance_mode("Dark")
            self.theme_switch.configure(text="Giao diện tối")
        else:
            ctk.set_appearance_mode("Light")
            self.theme_switch.configure(text="Giao diện sáng")

    def create_main_area(self):
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=1)
        self.main_frame.grid_rowconfigure(4, weight=1)

        # --- Top Metrics Row ---
        self.metrics_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.metrics_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 15))
        self.metrics_frame.grid_columnconfigure(0, weight=1)
        self.metrics_frame.grid_columnconfigure(1, weight=1)
        self.metrics_frame.grid_columnconfigure(2, weight=1)

        # Metrics Card 1: Cleared Rows
        self.card_cleared = ctk.CTkFrame(self.metrics_frame, fg_color=("#ffffff", "#1e293b"), border_width=1, border_color=("#cbd5e1", "#334155"))
        self.card_cleared.grid(row=0, column=0, padx=(0, 10), sticky="ew")
        self.lbl_cleared_num = ctk.CTkLabel(self.card_cleared, text="0", font=ctk.CTkFont(size=32, weight="bold"), text_color=("#059669", "#10b981"))
        self.lbl_cleared_num.pack(pady=(15, 2))
        self.lbl_cleared_txt = ctk.CTkLabel(self.card_cleared, text="Sá»‘ DÃ²ng ÄÃ£ XÃ³a", font=ctk.CTkFont(size=11, weight="bold"), text_color=("#64748b", "#94a3b8"))
        self.lbl_cleared_txt.pack(pady=(0, 15))

        # Metrics Card 2: Unique Codes
        self.card_unique = ctk.CTkFrame(self.metrics_frame, fg_color=("#ffffff", "#1e293b"), border_width=1, border_color=("#cbd5e1", "#334155"))
        self.card_unique.grid(row=0, column=1, padx=5, sticky="ew")
        self.lbl_unique_num = ctk.CTkLabel(self.card_unique, text="0", font=ctk.CTkFont(size=32, weight="bold"), text_color=("#4f46e5", "#6366f1"))
        self.lbl_unique_num.pack(pady=(15, 2))
        self.lbl_unique_txt = ctk.CTkLabel(self.card_unique, text="MÃ£ Chá»©ng Tá»« Duy Nháº¥t", font=ctk.CTkFont(size=11, weight="bold"), text_color=("#64748b", "#94a3b8"))
        self.lbl_unique_txt.pack(pady=(0, 15))

        # Metrics Card 3: Status
        self.card_status = ctk.CTkFrame(self.metrics_frame, fg_color=("#ffffff", "#1e293b"), border_width=1, border_color=("#cbd5e1", "#334155"))
        self.card_status.grid(row=0, column=2, padx=(10, 0), sticky="ew")
        self.lbl_status_val = ctk.CTkLabel(self.card_status, text="ÄANG CHá»œ", font=ctk.CTkFont(size=22, weight="bold"), text_color=("#d97706", "#f59e0b"))
        self.lbl_status_val.pack(pady=(18, 4))
        self.lbl_status_txt = ctk.CTkLabel(self.card_status, text="Tráº¡ng ThÃ¡i", font=ctk.CTkFont(size=11, weight="bold"), text_color=("#64748b", "#94a3b8"))
        self.lbl_status_txt.pack(pady=(0, 15))

        # --- Progress Bar ---
        self.progress_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.progress_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 15))
        self.lbl_progress = ctk.CTkLabel(
            self.progress_frame, 
            text="Tiáº¿n Ä‘á»™: Äang chá» khá»Ÿi cháº¡y...", 
            font=ctk.CTkFont(size=12),
            text_color=("#0f172a", "#f8fafc")
        )
        self.lbl_progress.pack(anchor="w")
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=10, fg_color=("#cbd5e1", "#1e293b"), progress_color=("#4f46e5", "#6366f1"))
        self.progress_bar.pack(fill="x", pady=5)
        self.progress_bar.set(0)

        # --- Patient Code Input ---
        self.patient_frame = ctk.CTkFrame(self.main_frame, fg_color=("#ffffff", "#1e293b"), border_width=1, border_color=("#cbd5e1", "#334155"))
        self.patient_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 15))
        self.patient_frame.grid_columnconfigure(1, weight=1)
        self.patient_frame.grid_columnconfigure(2, weight=0)
        
        self.lbl_patient = ctk.CTkLabel(
            self.patient_frame, 
            text="MÃƒ NGÆ¯á»œI Bá»†NH:", 
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.lbl_patient.grid(row=0, column=0, padx=(15, 10), pady=15, sticky="w")
        
        self.entry_patient = ctk.CTkEntry(
            self.patient_frame,
            placeholder_text="Nháº­p hoáº·c dÃ¡n mÃ£ ngÆ°á»i bá»‡nh táº¡i Ä‘Ã¢y...",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            height=38,
            fg_color=("#f8fafc", "#090d16"),
            border_color=("#cbd5e1", "#1e293b"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.entry_patient.grid(row=0, column=1, padx=(0, 10), pady=15, sticky="ew")

        self.cb_cap_cuu = ctk.CTkCheckBox(
            self.patient_frame,
            text="Cáº¥p cá»©u (CC)",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=("#0f172a", "#f8fafc"),
            fg_color=("#cbd5e1", "#334155"),
            checkmark_color="#ffffff",
            border_color=("#cbd5e1", "#1e293b"),
            hover_color=("#e0e7ff", "#1e1b4b"),
        )
        self.cb_cap_cuu.grid(row=0, column=2, padx=(0, 15), pady=15, sticky="w")

        self.lbl_time = ctk.CTkLabel(
            self.patient_frame, 
            text="THá»œI GIAN YL:", 
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.lbl_time.grid(row=1, column=0, padx=(15, 10), pady=(0, 15), sticky="w")
        
        # Sub-frame for side-by-side Date and Time inputs
        self.time_input_frame = ctk.CTkFrame(self.patient_frame, fg_color="transparent")
        self.time_input_frame.grid(row=1, column=1, columnspan=2, padx=(0, 15), pady=(0, 15), sticky="ew")
        self.time_input_frame.grid_columnconfigure(0, weight=1)
        self.time_input_frame.grid_columnconfigure(1, weight=1)
        
        self.entry_date = ctk.CTkEntry(
            self.time_input_frame,
            placeholder_text="NgÃ y YL (vÃ­ dá»¥: 25/06/2026)...",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            height=38,
            fg_color=("#f8fafc", "#090d16"),
            border_color=("#cbd5e1", "#1e293b"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.entry_date.grid(row=0, column=0, padx=(0, 10), sticky="ew")
        
        # Default the Date field to today's date in dd/mm/yyyy format
        today_date = time.strftime("%d/%m/%Y")
        self.entry_date.insert(0, today_date)
        
        self.entry_time = ctk.CTkEntry(
            self.time_input_frame,
            placeholder_text="Giá» YL (vÃ­ dá»¥: 7:10:00)...",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            height=38,
            fg_color=("#f8fafc", "#090d16"),
            border_color=("#cbd5e1", "#1e293b"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.entry_time.grid(row=0, column=1, padx=(10, 0), sticky="ew")
        self.lbl_time.grid_remove()
        self.time_input_frame.grid_remove()

        # --- Action CTA Button ---
        self.btn_action = ctk.CTkButton(
            self.main_frame, 
            text="Báº®T Äáº¦U Tá»° Äá»˜NG HÃ“A", 
            font=ctk.CTkFont(size=16, weight="bold"), 
            height=50, 
            fg_color=("#4f46e5", "#6366f1"), 
            hover_color=("#3730a3", "#4f46e5"),
            text_color="#ffffff",
            command=self.on_start_clicked
        )
        self.btn_action.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 15))

        # --- Bottom Split Section ---
        # 1. Logs Panel
        self.log_frame = ctk.CTkFrame(self.main_frame, fg_color=("#ffffff", "#1e293b"), border_width=1, border_color=("#cbd5e1", "#334155"))
        self.log_frame.grid(row=4, column=0, sticky="nsew", padx=(0, 10), pady=0)
        self.log_frame.grid_columnconfigure(0, weight=1)
        self.log_frame.grid_rowconfigure(1, weight=1)
        
        # Header + Search Box
        self.log_header_frame = ctk.CTkFrame(self.log_frame, fg_color="transparent")
        self.log_header_frame.grid(row=0, column=0, padx=15, pady=(10, 5), sticky="ew")
        
        self.lbl_logs_title = ctk.CTkLabel(
            self.log_header_frame, 
            text="Nháº­t KÃ½ Hoáº¡t Äá»™ng", 
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.lbl_logs_title.pack(side="left")
        
        self.entry_search_logs = ctk.CTkEntry(
            self.log_header_frame, 
            placeholder_text="TÃ¬m nháº­t kÃ½...", 
            width=150, 
            height=24, 
            font=ctk.CTkFont(size=11), 
            fg_color=("#f8fafc", "#090d16"), 
            border_color=("#cbd5e1", "#1e293b"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.entry_search_logs.pack(side="right")
        self.entry_search_logs.bind("<KeyRelease>", self.filter_logs)
        
        self.log_box = ctk.CTkTextbox(
            self.log_frame, 
            font=ctk.CTkFont(family="Consolas", size=11), 
            fg_color=("#f8fafc", "#090d16"), 
            border_width=1, 
            border_color=("#cbd5e1", "#1e293b"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.log_box.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="nsew")

        # 2. Results Panel
        self.result_frame = ctk.CTkFrame(self.main_frame, fg_color=("#ffffff", "#1e293b"), border_width=1, border_color=("#cbd5e1", "#334155"))
        self.result_frame.grid(row=4, column=1, sticky="nsew", padx=(10, 0), pady=0)
        self.result_frame.grid_columnconfigure(0, weight=1)
        self.result_frame.grid_rowconfigure(1, weight=1)
        
        self.lbl_results_title = ctk.CTkLabel(
            self.result_frame, 
            text="MÃ£ Chá»©ng Tá»« TrÃ­ch Xuáº¥t", 
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.lbl_results_title.grid(row=0, column=0, padx=15, pady=(10, 5), sticky="w")
        
        self.result_box = ctk.CTkTextbox(
            self.result_frame, 
            font=ctk.CTkFont(family="Consolas", size=12), 
            fg_color=("#f8fafc", "#090d16"), 
            border_width=1, 
            border_color=("#cbd5e1", "#1e293b"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.result_box.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="nsew")

        # Copy + QR Buttons
        self.btn_action_frame = ctk.CTkFrame(self.result_frame, fg_color="transparent")
        self.btn_action_frame.grid(row=2, column=0, padx=15, pady=(0, 15), sticky="ew")
        self.btn_action_frame.grid_columnconfigure(0, weight=1)
        self.btn_action_frame.grid_columnconfigure(1, weight=1)
        
        self.btn_copy = ctk.CTkButton(
            self.btn_action_frame, 
            text="Sao ChÃ©p Chá»¯", 
            command=self.copy_to_clipboard, 
            height=32, 
            fg_color="transparent", 
            text_color=("#4f46e5", "#8b5cf6"),
            border_width=1, 
            border_color=("#cbd5e1", "#334155"), 
            hover_color=("#e0e7ff", "#1e1b4b"),
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.btn_copy.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        
        self.btn_show_qr = ctk.CTkButton(
            self.btn_action_frame, 
            text="Táº¡o MÃ£ QR", 
            command=self.show_qr_code, 
            height=32, 
            fg_color="transparent", 
            text_color=("#059669", "#10b981"),
            border_width=1, 
            border_color=("#cbd5e1", "#334155"), 
            hover_color=("#d1fae5", "#064e3b"),
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.btn_show_qr.grid(row=0, column=1, padx=(5, 0), sticky="ew")

    def update_delay_label(self, val):
        self.delay_value_label.configure(text=f"{val:.1f}s")

    def log(self, message):
        """Append log message thread-safely."""
        message = str(message)
        timestamp = time.strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {message}"
        self.log_history.append(log_line)
        self.set_activity(message)
        if self.stop_overlay is not None and self.stop_overlay.winfo_exists():
            self.after(0, lambda msg=message: self.stop_overlay.update_activity(msg))
        self.after(0, self.refresh_log_box)

    def set_activity(self, message):
        if hasattr(self, "latest_activity"):
            clean_message = " ".join(str(message).split())
            self.after(0, lambda msg=clean_message: self.latest_activity.configure(text=msg))

    def refresh_log_box(self):
        """Refresh log box applying search filter."""
        search_query = self.entry_search_logs.get().strip().lower()
        self.log_box.delete("1.0", "end")
        
        for line in self.log_history:
            if not search_query or search_query in line.lower():
                self.log_box.insert("end", line + "\n")
        self.log_box.see("end")

    def filter_logs(self, event):
        self.refresh_log_box()

    def is_valid_column_range(self, name, column_range, screen_width, min_width=12):
        left, right = column_range
        if left < 0 or right > screen_width or right - left < min_width:
            self.log(f"Invalid {name} column range {column_range} for screen width {screen_width}.")
            return False
        return True

    def start_debug_session(self):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.debug_dir = os.path.abspath(os.path.join("debug", timestamp))
        try:
            os.makedirs(self.debug_dir, exist_ok=True)
            self.log(f"Thư mục debug phiên này: {self.debug_dir}")
        except Exception as e:
            self.debug_dir = None
            self.log(f"Cảnh báo: Không thể tạo thư mục debug: {e}")

    def save_debug_image(self, image, filename):
        if image is None:
            return None
        if self.debug_dir is None:
            self.start_debug_session()
        if self.debug_dir is None:
            return None
        path = os.path.join(self.debug_dir, filename)
        try:
            image.save(path)
            return path
        except Exception as e:
            self.log(f"Cảnh báo: Không thể lưu ảnh debug '{filename}': {e}")
            return None

    async def preflight_grid_check(self):
        self.log("Kiểm tra màn hình trước khi quét bảng...")
        missing_templates = [
            path for path in (ICON_TEMPLATE_PATH, CO_BUTTON_TEMPLATE_PATH)
            if not os.path.exists(path)
        ]
        if missing_templates:
            self.log(f"Thiếu file nhận dạng: {', '.join(missing_templates)}")
            return None

        screenshot_pil = pyautogui.screenshot()
        screenshot_bgr = cv2.cvtColor(np.array(screenshot_pil), cv2.COLOR_RGB2BGR)
        arrows = locate_all_templates(ICON_TEMPLATE_PATH, screenshot_bgr, threshold=0.72)
        if not arrows:
            debug_path = self.save_debug_image(screenshot_pil, "preflight_no_arrows.png")
            if debug_path:
                self.log(f"Không thấy mũi tên xanh. Đã lưu ảnh kiểm tra: {debug_path}")
            return None

        grid = calibrate_main_grid(screenshot_pil)
        if grid:
            columns = grid["columns"]
            thyl_range = columns["thyl_id"]
            ma_range = columns["ma_chung_tu"]
            valid = (
                self.is_valid_column_range("THYL_ID", thyl_range, screenshot_pil.width)
                and self.is_valid_column_range("Ma chung tu", ma_range, screenshot_pil.width)
            )
            if not valid:
                self.save_debug_image(screenshot_pil, "preflight_invalid_grid.png")
                return None
            self.log(f"Kiểm tra OK: thấy {len(grid['arrows'] or arrows)} dòng, hiệu chuẩn được cột bảng.")
            return grid

        arrows = sorted(arrows, key=lambda a: a[1])
        first_arrow = arrows[0]
        thyl_range, ma_range = await calibrate_columns(screenshot_pil, first_arrow[0], first_arrow[1], first_arrow[2])
        valid = (
            self.is_valid_column_range("THYL_ID", thyl_range, screenshot_pil.width)
            and self.is_valid_column_range("Ma chung tu", ma_range, screenshot_pil.width)
        )
        if not valid:
            self.save_debug_image(screenshot_pil, "preflight_invalid_fallback_columns.png")
            return None
        self.log(f"Kiểm tra OK: thấy {len(arrows)} dòng, dùng hiệu chuẩn cột dự phòng.")
        return {"arrows": arrows, "columns": {"thyl_id": thyl_range, "ma_chung_tu": ma_range}}

    def copy_to_clipboard(self):
        codes = self.result_box.get("1.0", "end-1c")
        if codes.strip():
            pyautogui.write("") # dummy keypress to make clipboard focus
            self.clipboard_clear()
            self.clipboard_append(codes)
            messagebox.showinfo("Bộ nhớ tạm", "Đã sao chép danh sách mã chứng từ vào bộ nhớ tạm.")
        else:
            messagebox.showwarning("Bộ nhớ tạm", "Không có mã để sao chép.")

    def show_qr_code(self):
        codes = self.result_box.get("1.0", "end-1c")
        if not codes.strip():
            messagebox.showwarning("Tạo mã QR", "Không có mã chứng từ nào để tạo mã QR. Vui lòng chạy tự động hóa trước.")
            return
        self.open_qr_code(codes)

    def open_qr_code(self, codes):
        if not codes.strip():
            return
        if self.qr_window is not None:
            try:
                if self.qr_window.winfo_exists():
                    self.qr_window.destroy()
            except Exception:
                pass
        self.qr_window = QRCodeWindow(self, codes)
        self.qr_window.protocol("WM_DELETE_WINDOW", self.on_qr_window_closed)

    def on_qr_window_closed(self):
        if self.qr_window is not None:
            try:
                self.qr_window.destroy()
            except Exception:
                pass
        self.qr_window = None

    def set_gui_state(self, running):
        self.is_running = running
        if running:
            self.btn_action.configure(text="Dừng ngay", fg_color=COLORS["danger"], hover_color=COLORS["danger_hover"])
            self.lift()
            self.attributes("-topmost", True)
            self.delay_slider.configure(state="disabled")
            self.max_switch.configure(state="disabled")
            self.entry_keywords.configure(state="disabled")
            self.entry_patient.configure(state="disabled")
            self.cb_cap_cuu.configure(state="disabled")
            self.dry_run_switch.configure(state="disabled")
            self.entry_date.configure(state="disabled")
            self.entry_time.configure(state="disabled")
            self.lbl_status_val.configure(text="Đang chạy", fg_color=("#ecfdf5", "#063c34"), text_color=COLORS["success"])
            self.enter_running_mini_mode()
        else:
            self.btn_action.configure(text="Bắt đầu", fg_color=COLORS["primary"], hover_color=COLORS["primary_hover"])
            self.hide_stop_overlay()
            self.exit_running_mini_mode()
            self.delay_slider.configure(state="normal")
            self.max_switch.configure(state="normal")
            self.entry_keywords.configure(state="normal")
            self.entry_patient.configure(state="normal")
            self.cb_cap_cuu.configure(state="normal")
            self.dry_run_switch.configure(state="normal")
            self.entry_date.configure(state="normal")
            self.entry_time.configure(state="normal")
            self.lbl_status_val.configure(text="Sẵn sàng", fg_color=("#fff7ed", "#34210d"), text_color=COLORS["warning"])
    def show_stop_overlay(self):
        if self.stop_overlay is not None and self.stop_overlay.winfo_exists():
            self.stop_overlay.lift()
            return
        self.stop_overlay = StopOverlay(self, self.request_stop)

    def hide_stop_overlay(self):
        if self.stop_overlay is not None:
            try:
                self.stop_overlay.destroy()
            except Exception:
                pass
            self.stop_overlay = None

    def request_stop(self):
        self.stop_requested = True
        if self.is_running:
            self.is_running = False
            self.hide_countdown_mode()
            self.set_activity("Đã nhận lệnh dừng. Đang dừng sau bước OCR/click hiện tại.")
            if self.stop_overlay is not None and self.stop_overlay.winfo_exists():
                self.stop_overlay.mark_stop_requested()
            self.lbl_status_val.configure(text="Đang dừng", fg_color=("#fef2f2", "#3b1012"), text_color=COLORS["danger"])
            self.log("Người dùng đã bấm DỪNG NGAY. Đang dừng sau bước OCR/click hiện tại.")
        self.after(0, self.deiconify)
        if not self.worker_active:
            self.after(0, lambda: self.set_gui_state(False))

    def on_start_clicked(self):
        if self.worker_active:
            self.request_stop()
            return
        if self.is_running:
            self.request_stop()
            return
            
        ma_nguoi_benh = self.entry_patient.get().strip()
        if not ma_nguoi_benh:
            messagebox.showwarning("Thiếu thông tin", "Vui lòng nhập Mã người bệnh trước khi bắt đầu.")
            return

        cleaned_patient_code = normalize_patient_code(ma_nguoi_benh)
        if cleaned_patient_code:
            ma_nguoi_benh = cleaned_patient_code
        self.entry_patient.delete(0, "end")
        self.entry_patient.insert(0, ma_nguoi_benh)

        is_dry_run = self.dry_run_switch.get() == 1

        if not is_dry_run:
            ans = messagebox.askyesno(
                "Xác nhận chạy thật",
                "CẢNH BÁO: App sẽ tự động click mũi tên, xác nhận hộp thoại xóa, và xóa dữ liệu trên HIS.\n\nBạn chắc chắn muốn tiếp tục?"
            )
            if not ans:
                return

        self.set_gui_state(True)
        self.stop_requested = False
        self.run_failed = False
        self.cleared_codes = []
        self.processed_rows_count = 0
        self.scanned_rows_count = 0
        self.blank_thyl_count = 0
        self.ocr_failed_count = 0
        self.result_box.delete("1.0", "end")
        self.lbl_cleared_num.configure(text="0")
        self.lbl_unique_num.configure(text="0")
        
        self.progress_bar.set(0)
        self.lbl_progress.configure(text="Tiến độ: đang xử lý 0 / 0 dòng")
        
        # Read parameters from inputs
        delay_sec = self.delay_slider.get()
        auto_max = self.max_switch.get()
        is_cap_cuu = self.cb_cap_cuu.get() == 1
        
        raw_kw = self.entry_keywords.get().strip()
        keywords = [k.strip() for k in raw_kw.split(",") if k.strip()]
        if not keywords:
            keywords = ["HOANMY_SAIGON", "eHospital"]
            
        # Update AppConfig parameters from GUI settings
        AppConfig.click_delay = delay_sec
        AppConfig.auto_maximize = auto_max
        AppConfig.keywords = keywords
        AppConfig.opt_mode = self.opt_mode_var.get().lower()
        
        try:
            AppConfig.popup_timeout = float(self.entry_popup_timeout.get().strip())
        except ValueError:
            AppConfig.popup_timeout = 2.0
            
        try:
            AppConfig.grid_change_timeout = float(self.entry_grid_timeout.get().strip())
        except ValueError:
            AppConfig.grid_change_timeout = 3.0
            
        # Silent backup file path
        backup_file = "backup_extracted_codes.txt"

        self.start_startup_countdown(3, delay_sec, auto_max, keywords, backup_file, ma_nguoi_benh, is_cap_cuu, is_dry_run)

    def start_startup_countdown(self, seconds, delay_sec, auto_max, keywords, backup_file, ma_nguoi_benh, is_cap_cuu, is_dry_run):
        if not self.is_running:
            return
        if seconds > 0:
            self.lbl_progress.configure(text=f"Chuẩn bị chạy sau {seconds} giây")
            self.show_countdown_mode(seconds, allow_continue=False)
            self.set_activity("Vui lòng không di chuyển chuột trong lúc tự động hóa chuẩn bị chạy.")
            self.after(1000, self.start_startup_countdown, seconds - 1, delay_sec, auto_max, keywords, backup_file, ma_nguoi_benh, is_cap_cuu, is_dry_run)
            return
        self.lbl_progress.configure(text="Đang bắt đầu tự động hóa...")
        self.hide_countdown_mode()
        self.start_automation_thread_after_countdown(delay_sec, auto_max, keywords, backup_file, ma_nguoi_benh, is_cap_cuu, is_dry_run)

    def start_automation_thread_after_countdown(self, delay_sec, auto_max, keywords, backup_file, ma_nguoi_benh, is_cap_cuu, is_dry_run):
        if not self.is_running:
            return  # user stopped it during countdown
            
        mini_profile = get_window_profile(self, "mini")
        place_window(self, mini_profile["width"], mini_profile["height"], anchor="bottom-right")
        self.lift()
        self.worker_active = True
        
        self.loop_thread = threading.Thread(
            target=self.run_automation_thread, 
            args=(delay_sec, auto_max, keywords, backup_file, ma_nguoi_benh, is_cap_cuu, is_dry_run), 
            daemon=True
        )
        self.loop_thread.start()

    def on_worker_finished(self):
        self.worker_active = False
        self.is_running = False
        self.async_loop = None
        self.after(0, self.deiconify)
        self.set_gui_state(False)

    def run_automation_thread(self, delay_sec, auto_max, keywords, backup_file, ma_nguoi_benh, is_cap_cuu, is_dry_run):
        try:
            # 0 matches COINIT_MULTITHREADED (MTA)
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception as e:
            self.log(f"Cảnh báo COM: Không thể khởi tạo COM: {e}")
            
        self.async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.async_loop)
        
        try:
            self.async_loop.run_until_complete(self.automation_routine(delay_sec, auto_max, keywords, backup_file, ma_nguoi_benh, is_cap_cuu, is_dry_run))
        except Exception as e:
            self.run_failed = True
            self.log(f"Lỗi hệ thống: {e}")
        finally:
            self.async_loop.close()
            try:
                ctypes.windll.ole32.CoUninitialize()
            except:
                pass
            self.after(0, self.on_worker_finished)

    def update_progress_gui(self, scanned, processed, total_pct):
        """Callback to update progress bar and metrics inside the main GUI thread."""
        self.processed_rows_count = processed
        self.scanned_rows_count = scanned
        self.after(0, lambda p=total_pct: self.progress_bar.set(p))
        self.after(0, lambda: self.lbl_cleared_num.configure(text=str(processed)))
        self.after(0, lambda: self.lbl_progress.configure(text=f"Tiến độ: đã xử lý dòng {processed} / {scanned}"))

    def on_engine_complete(self, summary, backup_file):
        """Callback executed on automation engine completion to display reports/QR."""
        unique_codes = summary.get("unique_deleted_codes", [])
        deleted_count = summary.get("deleted_rows_count", 0)
        self.cleared_codes = unique_codes
        
        # Write to results box
        self.after(0, lambda: self.result_box.delete("1.0", "end"))
        for code in unique_codes:
            self.after(0, lambda c=code: self.result_box.insert("end", c + "\n"))
            
        unique_len = len(unique_codes)
        self.after(0, lambda u=unique_len: self.lbl_unique_num.configure(text=str(u)))
        self.after(0, lambda c=deleted_count: self.lbl_cleared_num.configure(text=str(c)))

        # Reset GUI state
        self.after(0, self.deiconify)
        self.after(0, lambda: self.set_gui_state(False))
        
        if self.stop_requested:
            self.log("Tự động hóa đã dừng theo yêu cầu người dùng trước khi hoàn tất.")
            return
        if self.run_failed:
            self.log("Tự động hóa kết thúc với lỗi; không hiển thị hộp thoại hoàn tất.")
            return
            
        qr_text = "\n".join(unique_codes)
        if qr_text.strip():
            self.after(0, lambda text=qr_text: self.open_qr_code(text))
        self.after(0, lambda: CustomAlertWindow(self, "Tự Động Hóa Hoàn Tất", deleted_count, unique_len))

    async def automation_routine(self, delay_sec, auto_max, keywords, backup_file, ma_nguoi_benh, is_cap_cuu, is_dry_run):
        """Refactored automation routine that delegates the main execution flow to the core AutomationEngine."""
        self.log("Khởi tạo tiến trình tự động hóa...")
        self.start_debug_session()
        self.after(0, minimize_console)
        time.sleep(0.5)

        self.log(f"Đang tìm cửa sổ HIS (từ khóa: {keywords})...")
        focused = focus_window(keywords, auto_maximize=auto_max)
        if not focused:
            self.log("Không tìm thấy cửa sổ HIS. Đã dừng trước khi click tự động.")
            self.after(0, self.deiconify)
            self.after(0, lambda: self.set_gui_state(False))
            return
        if focused:
            self.log("Đã định vị và phóng to cửa sổ HIS.")
        time.sleep(0.5)

        if not self.is_running: return
        self.log("Đang định vị và chọn tab cập nhật trạng thái dịch vụ nội trú...")
        tab_focused = await focus_tab_by_name("UPDATE TRẠNG THÁI DỊCH VỤ NỘI TRÚ")
        if tab_focused:
            self.log("Đã chọn tab thành công.")
        else:
            self.log("Không tìm thấy tab. Có thể tab đã được chọn hoặc nằm ngoài màn hình.")
        time.sleep(0.5)

        if not self.is_running: return
        self.log("Đang định vị ô nhập Mã người bệnh...")
        label_rect = await find_ma_nguoi_benh_label()
        if not label_rect:
            self.log("Cảnh báo: Không tìm thấy ô nhập Mã người bệnh trên màn hình.")
            self.after(0, self.deiconify)
            self.after(0, lambda: self.set_gui_state(False))
            return
            
        lx, ly, lw, lh = label_rect
        click_x = lx + int(1.75 * lw)
        click_y = ly + int(lh / 2)

        self.log(f"Click vào ô nhập tại ({click_x}, {click_y}) để kiểm tra mã hiện tại...")
        pyautogui.click(click_x, click_y)
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)
        try:
            self.clipboard_clear()
            self.update()
        except Exception as e:
            self.log(f"Cảnh báo: Không thể xóa clipboard trước khi đọc mã hiện tại: {e}")
        pyautogui.hotkey('ctrl', 'c')
        time.sleep(0.2)
        current_code = normalize_patient_code(get_windows_clipboard_text())
        if not current_code:
            self.log("Không đọc được mã hiện tại từ clipboard, chuyển sang OCR dự phòng.")
            current_code = await read_current_patient_code(label_rect)
        requested_code = normalize_patient_code(ma_nguoi_benh)
        patient_code_matches = bool(current_code) and current_code == requested_code
        self.log(f"Mã hiện tại đọc được: '{current_code or '(trống)'}'.")
        
        if patient_code_matches:
            self.log(f"Mã người bệnh '{ma_nguoi_benh}' đã có sẵn. Bỏ qua nhập lại để tránh làm mất trạng thái hiện tại.")
        else:
            if current_code:
                self.log(f"Ô Mã người bệnh đang có '{current_code}', khác mã cần chạy. Sẽ xóa và nhập mã mới.")
            else:
                self.log("Ô Mã người bệnh đang trống hoặc không đọc được. Sẽ nhập mã mới.")
            if not self.is_running: return
            pyautogui.click(click_x, click_y)
            time.sleep(0.1)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.1)
            pyautogui.press('backspace')
            time.sleep(0.1)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.1)
            pyautogui.press('delete')
            time.sleep(0.1)
            
            copy_text_to_clipboard(requested_code)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.1)
            try:
                self.clipboard_clear()
                self.update()
            except Exception as e:
                self.log(f"Cảnh báo: Không thể xóa clipboard trước khi xác minh mã: {e}")
            pyautogui.hotkey('ctrl', 'c')
            time.sleep(0.2)
            typed_code = normalize_patient_code(get_windows_clipboard_text())
            if not typed_code:
                self.log("Không đọc được mã sau khi nhập từ clipboard, chuyển sang OCR dự phòng.")
                typed_code = normalize_patient_code(await read_current_patient_code(label_rect))
            if typed_code != requested_code:
                self.log(f"Mã đọc lại là '{typed_code or '(trống)'}'; thử dán lại một lần.")
                pyautogui.click(click_x, click_y)
                time.sleep(0.1)
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(0.1)
                pyautogui.press('delete')
                time.sleep(0.1)
                copy_text_to_clipboard(requested_code)
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.3)
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(0.1)
                pyautogui.hotkey('ctrl', 'c')
                time.sleep(0.2)
                typed_code = normalize_patient_code(get_windows_clipboard_text())
                if not typed_code:
                    typed_code = normalize_patient_code(await read_current_patient_code(label_rect))
            if typed_code != requested_code:
                self.log(f"Cảnh báo: Mã sau khi nhập là '{typed_code or '(trống)'}', không khớp '{requested_code}'. Dừng để tránh chạy sai bệnh nhân.")
                self.after(0, self.deiconify)
                self.after(0, lambda: self.set_gui_state(False))
                return
            self.log(f"Đã nhập mã '{ma_nguoi_benh}'. Chờ hiển thị danh sách bệnh án...")
            time.sleep(1.0)
            
            if not self.is_running: return
            mode_str = "chứa /CC" if is_cap_cuu else "không chứa /CC"
            self.log(f"Đang phân tích danh sách bệnh án và chọn Số bệnh án {mode_str}...")
            selected = await select_so_benh_an(click_x, click_y, lw, is_cap_cuu=is_cap_cuu, stop_checker=lambda: not self.is_running, log_cb=self.log)
            if selected:
                self.log("Đã chọn Số bệnh án thành công.")
            else:
                self.log(f"Cảnh báo: Không thể chọn Số bệnh án {mode_str} từ danh sách dropdown.")
                self.after(0, self.deiconify)
                self.after(0, lambda: self.set_gui_state(False))
                return
            
        time.sleep(1.0)

        # Switch to the delete sub-tab
        if not self.is_running: return
        self.log("Đang chuyển sang sub-tab Xóa thực THYL, Chứng từ...")
        sub_tab_focused = await focus_sub_tab("Xóa thực THYL, Chứng từ")
        if sub_tab_focused:
            self.log("Đã chọn sub-tab thành công.")
        else:
            self.log("Cảnh báo: Không thể chọn sub-tab Xóa thực THYL, Chứng từ.")
            self.after(0, self.deiconify)
            self.after(0, lambda: self.set_gui_state(False))
            return
        time.sleep(0.5)

        # Countdown manual filter YL
        manual_filter_seconds = AppConfig.manual_filter_seconds
        self.log(f"Tạm dừng {manual_filter_seconds} giây để người dùng tự chọn bộ lọc Thời gian YL trên HIS.")
        self.skip_countdown_requested = False
        for remaining in range(manual_filter_seconds, 0, -1):
            if not self.is_running:
                return
            if self.skip_countdown_requested:
                self.log("Người dùng bấm Tiếp tục ngay. Bắt đầu quét bảng hiện tại.")
                break
            self.after(0, lambda r=remaining: self.lbl_progress.configure(text=f"Chờ chọn lọc thủ công: {r} giây"))
            self.after(0, lambda r=remaining: self.show_countdown_mode(r, allow_continue=True))
            self.set_activity("Tự chọn ngày/giờ trên HIS rồi bấm Đóng. App sẽ tự chạy tiếp sau khi hết giờ.")
            time.sleep(1)
        self.after(0, self.hide_countdown_mode)
        if not self.is_running: return
        if not self.skip_countdown_requested:
            self.log("Hết thời gian chọn lọc thủ công. Bắt đầu quét bảng hiện tại.")
        self.skip_countdown_requested = False

        # Run core AutomationEngine
        callbacks = {
            'on_log': self.log,
            'on_status': lambda s: self.after(0, lambda: self.lbl_status_val.configure(text=s.upper())),
            'on_progress': self.update_progress_gui,
            'on_complete': self.on_engine_complete,
            'stop_checker': lambda: not self.is_running
        }
        
        engine = AutomationEngine(callbacks)
        
        # Preflight validation check
        preflight_ok = await engine.preflight_grid_check()
        if not preflight_ok:
            self.run_failed = True
            self.log("Kiểm tra màn hình không đạt. Đã dừng trước khi click/xóa.")
            self.after(0, self.deiconify)
            self.after(0, lambda: self.set_gui_state(False))
            return
            
        await engine.run(ma_nguoi_benh, is_cap_cuu, is_dry_run)

def minimize_console():
    """Automatically minimize the current command prompt window to reveal the HIS application behind it."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            # 6 is SW_MINIMIZE
            ctypes.windll.user32.ShowWindow(hwnd, 6)
            print("Console minimized to reveal screen.")
    except Exception as e:
        print(f"Could not minimize console window: {e}")

if __name__ == "__main__":
    app = HISAutomatorGUI()
    app.mainloop()
