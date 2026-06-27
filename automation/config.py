import os
import sys

class AppConfig:
    # Version
    VERSION = "2.0.0"

    # Paths
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Check if packaged (PyInstaller or embedded python package)
    try:
        MEIPASS_DIR = sys._MEIPASS
    except AttributeError:
        MEIPASS_DIR = BASE_DIR
        
    ICON_TEMPLATE_PATH = os.path.join(MEIPASS_DIR, "image3_icon.png")
    CO_BUTTON_TEMPLATE_PATH = os.path.join(MEIPASS_DIR, "co_button.png")
    LAYOUT_PROFILES_PATH = os.path.join(BASE_DIR, "layout_profiles.json")
    SESSION_LOG_DIR = os.path.join(BASE_DIR, "debug")

    # Default delays and timeouts
    PYAUTOGUI_PAUSE = 0.05
    FAILSAFE = True
    click_delay = 0.2           # default action delay (seconds)
    popup_timeout = 2.0         # wait timeout for popup (seconds)
    grid_change_timeout = 3.0   # wait timeout for grid refresh (seconds)
    ocr_retry_depth = 14        # max preprocessing methods tried
    save_debug_images = True
    manual_filter_seconds = 12
    auto_maximize = True
    keywords = ["HOANMY_SAIGON", "eHospital"]
    
    # Optimization mode: "safe", "balanced", "fast"
    opt_mode = "balanced"

    # DPI / Display awareness setup
    @staticmethod
    def enable_dpi_awareness():
        if sys.platform != "win32":
            return
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    @classmethod
    def get_template_paths(cls):
        return cls.ICON_TEMPLATE_PATH, cls.CO_BUTTON_TEMPLATE_PATH

    @classmethod
    def update_templates(cls, icon_path=None, co_button_path=None):
        if icon_path:
            cls.ICON_TEMPLATE_PATH = icon_path
        if co_button_path:
            cls.CO_BUTTON_TEMPLATE_PATH = co_button_path
