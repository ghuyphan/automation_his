import os
import sys
import time
import asyncio
import cv2
import numpy as np
from PIL import Image

# Add root folder to sys.path in case we are running inside subfolder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from automation.config import AppConfig
from automation.screen import focus_window, minimize_console, capture_screenshot as pyautogui_screenshot
from automation.templates import locate_all_templates, locate_single_template
from automation.ocr import (
    run_ocr_on_pil_image, 
    is_thyl_id_populated, 
    extract_document_code, 
    remove_vietnamese_diacritics
)
from automation.grid import (
    calibrate_columns, 
    calibrate_main_grid, 
    has_visible_data_rows, 
    scroll_main_grid_down,
    get_visible_row_arrows,
    focus_tab_by_name,
    focus_sub_tab,
    calibrate_layered
)
from automation.patient import (
    find_ma_nguoi_benh_label, 
    select_so_benh_an, 
    is_so_benh_an,
    read_current_patient_code, 
    normalize_patient_code
)
from automation.waiters import wait_until_dropdown_visible
from automation.engine import AutomationEngine

def set_template_paths(icon_path=None, co_button_path=None):
    """Fallback export to set template paths."""
    AppConfig.update_templates(icon_path, co_button_path)

# Windows API constants for clipboard reads
CF_UNICODETEXT = 13

def get_windows_clipboard_text():
    """Read Unicode text from the Windows clipboard without extra dependencies."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL

        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr) or ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception as e:
        print(f"Clipboard read failed safely: {e}")
        return ""

async def process_automation():
    """CLI automation executor mimicking old automate.py behavior but utilizing modular flow."""
    print("=========================================")
    print("HIS Desktop Automation Tool (Modular CLI)")
    print("=========================================")
    
    print("Please make sure the HIS application window is fully visible on the screen.")
    print("Waiting 3 seconds... Console window will minimize automatically.")
    for i in range(3, 0, -1):
        print(f"{i}...")
        time.sleep(1)
        
    minimize_console()
    time.sleep(0.5)
    
    if not focus_window():
        print("Could not focus the HIS window. Stopping before any automated clicks.")
        return
    time.sleep(0.5)
            
    cleared_codes = []
    iteration = 1
    
    while True:
        print(f"\n--- Scanning Screen (Iteration {iteration}) ---")
        
        screenshot_pil = pyautogui_screenshot()
        screenshot_bgr = cv2.cvtColor(np.array(screenshot_pil), cv2.COLOR_RGB2BGR)
            
        arrows = get_visible_row_arrows(screenshot_pil)
        if not arrows:
            print("No down-arrow icons found on screen. Stopping.")
            debug_path = "debug_screenshot.png"
            screenshot_pil.save(debug_path)
            print(f"Saved current screen to '{os.path.abspath(debug_path)}' to help diagnose matching errors.")
            break
            
        print(f"Found {len(arrows)} rows in the table.")
        
        # Layered calibration
        thyl_range, ma_range, conf = await calibrate_layered(screenshot_pil, arrows)
        if not thyl_range or not ma_range:
            print("Could not calibrate column positions. Stopping.")
            break
            
        rows_processed_this_scan = 0
        scale_factor = arrows[0][2] / 26.0
        
        for idx, arrow in enumerate(arrows):
            ax, ay, aw, ah = arrow
            row_y = ay + ah // 2
            
            # High-speed blank detection
            thyl_w = thyl_range[1] - thyl_range[0]
            thyl_h = int(20 * scale_factor)
            thyl_crop = screenshot_pil.crop((thyl_range[0], row_y - int(10 * scale_factor), thyl_range[1], row_y + int(10 * scale_factor)))
            
            if is_cell_visually_blank(thyl_crop):
                print(f"Row {idx+1} (Y={row_y}): THYL_ID is blank (visual). Skipping deletion.")
                continue
                
            # OCR THYL_ID with vertical Y-shifting retry
            thyl_text = ""
            for y_shift in [0, -1, 1, -2, 2]:
                shift_y = row_y + int(y_shift * scale_factor)
                t_crop = screenshot_pil.crop((thyl_range[0], shift_y - int(10 * scale_factor), thyl_range[1], shift_y + int(10 * scale_factor)))
                t_upscaled = t_crop.resize((thyl_w * 3, thyl_h * 3), Image.Resampling.LANCZOS)
                
                try:
                    thyl_ocr = await run_ocr_on_pil_image(t_upscaled)
                    temp_text = thyl_ocr.text.strip()
                    if is_thyl_id_populated(temp_text):
                        thyl_text = temp_text
                        break
                except Exception:
                    pass
                    
            if not thyl_text:
                print(f"Row {idx+1} (Y={row_y}): THYL_ID is blank. Skipping deletion.")
                continue
                
            # OCR Mã chứng từ with robust retry matrix
            ma_text, cleaned_ma = await extract_document_code(screenshot_pil, ma_range, row_y, scale_factor)
            sanitized_ma = ""
            if cleaned_ma:
                sanitized_ma = "".join(c for c in cleaned_ma if c.isalnum())
                print(f"Row {idx+1} (Y={row_y}): Extracted Mã chứng từ = '{ma_text}' (Cleaned: '{cleaned_ma}')")
            else:
                print(f"Warning: Row {idx+1} (Y={row_y}): Could not read Mã chứng từ code!")
                
            print(f"Row {idx+1} (Y={row_y}): THYL_ID is '{thyl_text}' (Not blank). Deleting/clearing...")
            
            # Click down-arrow and confirmation dialog
            from automation.deletion import perform_row_deletion
            success, _ = await perform_row_deletion(arrow, thyl_range, ma_range, sanitized_ma, scale_factor, log_cb=print)
            
            if success:
                if sanitized_ma:
                    cleared_codes.append(sanitized_ma)
                rows_processed_this_scan += 1
                print("Row processed. Recapturing screen for next iteration.")
                break
                
        if rows_processed_this_scan == 0:
            if scroll_main_grid_down(screenshot_pil):
                print("\nVisible rows have blank THYL_ID; scrolled down to check more rows.")
                iteration += 1
                continue
            print("\nAll visible rows have blank THYL_ID. Automation complete!")
            break
            
        iteration += 1
        if iteration > 50:
            print("Warning: Loop safety limit reached.")
            break
            
    unique_codes = sorted(list(set(cleared_codes)))
    
    print("\n=========================================")
    print("Automation Results")
    print("=========================================")
    print(f"Total rows cleared: {len(cleared_codes)}")
    print(f"Unique, sorted Mã chứng từ codes ({len(unique_codes)}):")
    for code in unique_codes:
        print(f" - {code}")
        
    output_file = "output_ma_chung_tu.txt"
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            for code in unique_codes:
                f.write(code + "\n")
        print(f"\nSaved list of codes to {os.path.abspath(output_file)}")
    except Exception as e:
        print(f"Error saving output file: {e}")

if __name__ == "__main__":
    import ctypes
    try:
        # Initialize COM
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except:
        pass
    try:
        asyncio.run(process_automation())
    finally:
        try:
            ctypes.windll.ole32.CoUninitialize()
        except:
            pass
