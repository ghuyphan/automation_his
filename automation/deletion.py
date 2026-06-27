import time
import pyautogui
import cv2
import numpy as np
from PIL import Image
from automation.config import AppConfig
from automation.screen import capture_screenshot
from automation.ocr import is_cell_visually_blank
from automation.waiters import wait_until_co_button_visible

async def verify_row_integrity(arrow, thyl_range, ma_range, expected_ma, scale_factor, log_cb=None):
    """
    Take a quick screenshot of the row to verify it is still valid before deleting:
    1. THYL_ID is not blank
    2. Document code is not blank
    """
    ax, ay, aw, ah = arrow
    row_y = ay + ah // 2
    
    # 1. Grab thyl_crop
    crop_x1 = thyl_range[0]
    crop_y1 = row_y - int(10 * scale_factor)
    crop_w = thyl_range[1] - thyl_range[0]
    crop_h = int(20 * scale_factor)
    
    thyl_crop = capture_screenshot(region=(crop_x1, crop_y1, crop_w, crop_h))
    
    if is_cell_visually_blank(thyl_crop):
        if log_cb:
            log_cb("[Safety] Cảnh báo: THYL_ID bỗng nhiên trống trước khi click! Bỏ qua dòng này.")
        return False, None
        
    # 2. Grab ma_crop and verify code is not blank
    ma_crop_x1 = ma_range[0]
    ma_crop_y1 = row_y - int(10 * scale_factor)
    ma_crop_w = ma_range[1] - ma_range[0]
    ma_crop_h = int(20 * scale_factor)
    
    ma_crop = capture_screenshot(region=(ma_crop_x1, ma_crop_y1, ma_crop_w, ma_crop_h))
    
    if is_cell_visually_blank(ma_crop):
        if log_cb:
            log_cb("[Safety] Cảnh báo: Mã chứng từ bỗng nhiên trống trước khi click! Bỏ qua dòng này.")
        return False, None
            
    return True, thyl_crop

async def perform_row_deletion(arrow, thyl_range, ma_range, expected_ma, scale_factor, stop_checker=None, log_cb=None):
    """
    Perform the row click and click confirm on popup.
    Returns: (success, crop_evidence)
    """
    if stop_checker and stop_checker():
        return False, None
        
    # Safety Check: Verify row is still there and valid
    is_valid, thyl_crop = await verify_row_integrity(arrow, thyl_range, ma_range, expected_ma, scale_factor, log_cb)
    if not is_valid:
        return False, None
        
    # Click down-arrow
    ax, ay, aw, ah = arrow
    click_x = ax + aw // 2
    click_y = ay + ah // 2
    
    if log_cb:
        log_cb(f"Click vào mũi tên tại ({click_x}, {click_y})...")
        
    pyautogui.click(click_x, click_y)
    
    # Wait for popup
    _, co_button_path = AppConfig.get_template_paths()
    co_button_pos = await wait_until_co_button_visible(co_button_path, timeout=AppConfig.popup_timeout, stop_checker=stop_checker, log_cb=log_cb)
    
    if not co_button_pos:
        if log_cb:
            log_cb("[Deletion] Cảnh báo: Hộp thoại xác nhận (nút 'Có') không xuất hiện. Huỷ bỏ thao tác.")
        return False, None
        
    if stop_checker and stop_checker():
        return False, None
        
    cx, cy = co_button_pos
    if log_cb:
        log_cb(f"Xác nhận xóa. Click nút 'Có' tại ({cx}, {cy})...")
        
    pyautogui.click(cx, cy)
    
    # Wait click delay
    time.sleep(AppConfig.click_delay)
    
    return True, thyl_crop
