import time
import re
import numpy as np
import cv2
import pyautogui
from PIL import Image
from automation.config import AppConfig
from automation.screen import capture_screenshot, focus_window
from automation.templates import locate_single_template
from automation.ocr import run_ocr_on_pil_image, remove_vietnamese_diacritics

async def is_filter_popup_open(filter_x=None, filter_y=None):
    """Return True when the column filter popup is visible, using region screenshots for speed."""
    if filter_x is not None and filter_y is not None:
        # Targeted screenshot crop next to filter icon
        crop_x = max(0, int(filter_x - 100))
        crop_y = max(0, int(filter_y))
        crop_w = 320
        crop_h = 450
        screenshot_pil = capture_screenshot(region=(crop_x, crop_y, crop_w, crop_h))
    else:
        screenshot_pil = capture_screenshot()
        
    try:
        ocr_res = await run_ocr_on_pil_image(screenshot_pil)
    except Exception as e:
        print(f"[Waiters] OCR check for filter popup failed: {e}")
        return False
        
    for line in ocr_res.lines:
        line_text = remove_vietnamese_diacritics(line.text).lower()
        if (
            "enter text" in line_text
            or "to search" in line_text
            or "clear filter" in line_text
            or line_text.strip() == "close"
            or "close" in line_text
        ):
            return True
    return False

async def wait_until_co_button_visible(co_button_path, timeout=2.0, check_interval=0.08, stop_checker=None, log_cb=None):
    """Wait adaptively until the confirmation popup 'Có' button is found on screen."""
    if log_cb:
        log_cb("Chờ hộp thoại xác nhận xóa xuất hiện...")
        
    start_time = time.time()
    while time.time() - start_time < timeout:
        if stop_checker and stop_checker():
            return None
            
        screenshot_pil = capture_screenshot()
        screenshot_bgr = cv2.cvtColor(np.array(screenshot_pil), cv2.COLOR_RGB2BGR)
        pos = locate_single_template(co_button_path, screenshot_bgr, threshold=0.8)
        if pos:
            return pos
            
        time.sleep(check_interval)
    return None

async def wait_until_dropdown_visible(click_x, click_y, label_w, timeout=2.0, check_interval=0.1, stop_checker=None, log_cb=None):
    """Wait adaptively until the patient dropdown list populated under input field."""
    if log_cb:
        log_cb("Chờ danh sách Số bệnh án dropdown xuất hiện...")
        
    start_time = time.time()
    while time.time() - start_time < timeout:
        if stop_checker and stop_checker():
            return False
            
        crop_x1 = max(0, int(click_x - label_w))
        crop_y1 = int(click_y + 10)
        crop_w = int(3.5 * label_w)
        crop_h = 350
        
        dropdown_crop = capture_screenshot(region=(crop_x1, crop_y1, crop_w, crop_h))
        try:
            ocr_result = await run_ocr_on_pil_image(dropdown_crop)
            for line in ocr_result.lines:
                # If there are lines containing dot patterns (XX.XXXXXX) or similar numbers, dropdown has loaded
                if re.search(r'\d{2,3}\.\d{5,6}', line.text):
                    return True
        except Exception:
            pass
            
        time.sleep(check_interval)
    return False

async def wait_until_filter_popup_state(open_target, filter_x=None, filter_y=None, timeout=2.0, check_interval=0.1, stop_checker=None, log_cb=None):
    """Wait adaptively until the filter popup becomes open (open_target=True) or closed (open_target=False)."""
    if log_cb:
        state_str = "mở" if open_target else "đóng"
        log_cb(f"Chờ hộp thoại bộ lọc Thời gian YL {state_str}...")
        
    start_time = time.time()
    while time.time() - start_time < timeout:
        if stop_checker and stop_checker():
            return False
            
        is_open = await is_filter_popup_open(filter_x, filter_y)
        if is_open == open_target:
            return True
            
        time.sleep(check_interval)
    return False

async def wait_until_grid_refreshed(old_arrows_count, old_scrollbar_y=None, timeout=3.0, check_interval=0.1, stop_checker=None, log_cb=None):
    """Wait adaptively until the grid refreshes (detected rows change or scroll completes)."""
    if log_cb:
        log_cb("Chờ lưới danh sách HIS cập nhật lại...")
        
    from automation.grid import get_visible_row_arrows, locate_main_grid_scrollbar_thumb
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        if stop_checker and stop_checker():
            return False
            
        screenshot_pil = capture_screenshot()
        current_arrows = get_visible_row_arrows(screenshot_pil)
        
        # Grid refreshed if rows count changes
        if len(current_arrows) != old_arrows_count:
            return True
            
        # Grid refreshed if scrollbar moves
        if old_scrollbar_y is not None:
            thumb = locate_main_grid_scrollbar_thumb(screenshot_pil)
            if thumb and abs(thumb[1] - old_scrollbar_y) > 3:
                return True
                
        time.sleep(check_interval)
    return True
