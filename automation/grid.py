import os
import json
import time
import ctypes
from ctypes import wintypes
import numpy as np
import cv2
import pyautogui
from PIL import Image
from automation.config import AppConfig
from automation.templates import locate_all_templates, locate_single_template
from automation.screen import capture_screenshot
from automation.ocr import run_ocr_on_pil_image, remove_vietnamese_diacritics

def get_his_window_geometry():
    """Retrieve width and height of the currently focused HIS window using Win32 API."""
    if os.name != 'nt':
        return None
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if hwnd:
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                if any(keyword.lower() in title.lower() for keyword in AppConfig.keywords):
                    rect = wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    return rect.right - rect.left, rect.bottom - rect.top
    except Exception as e:
        print(f"[Grid] Warning: Could not retrieve window geometry: {e}")
    return None

def load_layout_profiles():
    """Load layout calibration profiles from local JSON storage."""
    if os.path.exists(AppConfig.LAYOUT_PROFILES_PATH):
        try:
            with open(AppConfig.LAYOUT_PROFILES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Grid] Warning: Could not load layout profiles: {e}")
    return {}

def save_layout_profile(profile_key, profile_data):
    """Save a layout calibration profile to local JSON storage."""
    try:
        profiles = load_layout_profiles()
        profiles[profile_key] = profile_data
        os.makedirs(os.path.dirname(AppConfig.LAYOUT_PROFILES_PATH), exist_ok=True)
        with open(AppConfig.LAYOUT_PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Grid] Warning: Could not save layout profile: {e}")

def get_current_profile_key(screenshot_pil):
    """Construct a unique key representing current display and window geometry."""
    geom = get_his_window_geometry()
    his_w, his_h = geom if geom else (screenshot_pil.width, screenshot_pil.height)
    return f"{screenshot_pil.width}x{screenshot_pil.height}_{his_w}x{his_h}"

def get_visible_row_arrows(screenshot_pil=None):
    """Return sorted row action arrows from the main visible table."""
    if screenshot_pil is None:
        screenshot_pil = capture_screenshot()
    screenshot_bgr = cv2.cvtColor(np.array(screenshot_pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    icon_path, _ = AppConfig.get_template_paths()
    arrows = locate_all_templates(icon_path, screenshot_bgr, threshold=0.72)
    arrows = [a for a in arrows if a[1] > 120]
    return sorted(arrows, key=lambda a: a[1])

def has_visible_data_rows(screenshot_pil=None):
    """Return True when the main table has visible row action icons."""
    if screenshot_pil is None:
        screenshot_pil = capture_screenshot()
    screenshot_bgr = cv2.cvtColor(np.array(screenshot_pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    icon_path, _ = AppConfig.get_template_paths()
    arrows = locate_all_templates(icon_path, screenshot_bgr, threshold=0.72)
    return any(y > 120 for _, y, _, _ in arrows)

def locate_main_grid_header(screenshot_pil, arrows=None):
    """Find the blue main-grid header above visible row arrows and its separators."""
    np_img = np.array(screenshot_pil.convert("RGB"))
    screen_h, screen_w = np_img.shape[:2]
    hsv = cv2.cvtColor(np_img, cv2.COLOR_RGB2HSV)
    if arrows is None:
        arrows = get_visible_row_arrows(screenshot_pil)
    first_arrow_y = arrows[0][1] if arrows else None

    # Detect header blue background
    blue_mask = cv2.inRange(hsv, np.array([90, 55, 70]), np.array([112, 255, 255]))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(blue_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w >= int(screen_w * 0.55) and 15 <= h <= 42 and y >= 45:
            candidates.append((x, y, w, h))

    if not candidates:
        return None

    if first_arrow_y is not None:
        above_rows = [box for box in candidates if box[1] < first_arrow_y]
        header_box = max(above_rows, key=lambda box: (box[1], box[2])) if above_rows else min(candidates, key=lambda box: abs(box[1] - first_arrow_y))
    else:
        upper_candidates = [box for box in candidates if box[1] < int(screen_h * 0.55)]
        header_box = max(upper_candidates or candidates, key=lambda box: (box[2], -box[1]))

    # Locate white column separator lines in the header blue background
    x, y, w, h = header_box
    header_crop = np_img[y:y + h, x:x + w]
    light_pixels = (
        (header_crop[:, :, 0] > 220)
        & (header_crop[:, :, 1] > 220)
        & (header_crop[:, :, 2] > 220)
    )
    column_scores = light_pixels.sum(axis=0)
    separator_indices = np.where(column_scores > h * 0.55)[0]

    groups = []
    for idx in separator_indices:
        if not groups or idx - groups[-1][1] > 2:
            groups.append([idx, idx])
        else:
            groups[-1][1] = idx

    separators = [x + (start + end) // 2 for start, end in groups]
    if len(separators) < 6:
        if AppConfig.save_debug_images:
            try:
                screenshot_pil.save("grid_calibration_debug.png")
            except Exception as e:
                print(f"[Grid] Warning: Error saving grid calibration debug: {e}")
        return None

    return {"box": header_box, "separators": separators, "arrows": arrows}

def calibrate_main_grid(screenshot_pil=None):
    """Calibrate useful main-grid columns using header separators."""
    if screenshot_pil is None:
        screenshot_pil = capture_screenshot()
    arrows = get_visible_row_arrows(screenshot_pil)
    header = locate_main_grid_header(screenshot_pil, arrows)
    if not header:
        return None

    separators = [int(x) for x in header["separators"]]
    header_x = int(header["box"][0])
    columns = {
        "action": (header_x, separators[0]),
        "toa_thuoc_id": (separators[0], separators[1]),
        "thyl_id": (separators[1], separators[2]),
        "thoi_gian_yl": (separators[2], separators[3]),
        "noi_dung": (separators[3], separators[4]),
    }
    if len(separators) >= 6:
        columns["trang_thai_yl"] = (separators[4], separators[5])
    if len(separators) >= 7:
        columns["ma_chung_tu"] = (separators[5], separators[6])
    else:
        # Keep the old, tested offset as a fallback for narrower screenshots.
        first_arrow = arrows[0] if arrows else (separators[0], 0, 26, 18)
        scale_factor = first_arrow[2] / 26.0
        columns["ma_chung_tu"] = (first_arrow[0] + int(620 * scale_factor), first_arrow[0] + int(755 * scale_factor))

    return {"header": header, "columns": columns, "arrows": arrows}

async def calibrate_columns(screenshot_pil, first_arrow_x, first_arrow_y, first_arrow_w):
    """
    Calibrate column horizontal positions using OCR on the header row with spatial constraints.
    Returns (thyl_id_x_range, ma_chung_tu_x_range).
    """
    scale_factor = first_arrow_w / 26.0
    
    header_x1 = max(0, int(first_arrow_x - 20 * scale_factor))
    header_x2 = min(screenshot_pil.width, int(first_arrow_x + 950 * scale_factor))
    header_y1 = max(0, int(first_arrow_y - 45 * scale_factor))
    header_y2 = max(0, int(first_arrow_y - 5 * scale_factor))
    
    header_crop = screenshot_pil.crop((header_x1, header_y1, header_x2, header_y2))
    
    # Upscale header for better OCR
    w, h = header_crop.size
    upscaled = header_crop.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
    
    try:
        ocr_result = await run_ocr_on_pil_image(upscaled)
    except Exception as e:
        print(f"[Grid] OCR calibration failed: {e}")
        # Return fallback range
        thyl_id_offset_x = int(125 * scale_factor)
        thyl_id_width = int(44 * scale_factor)
        ma_chung_tu_offset_x = int(662 * scale_factor)
        thyl_id_x_range = (first_arrow_x + thyl_id_offset_x - thyl_id_width//2, first_arrow_x + thyl_id_offset_x + thyl_id_width//2)
        ma_chung_tu_x_range = (first_arrow_x + ma_chung_tu_offset_x - int(42 * scale_factor), first_arrow_x + ma_chung_tu_offset_x + int(93 * scale_factor))
        return thyl_id_x_range, ma_chung_tu_x_range
        
    thyl_id_offset_x = int(125 * scale_factor)
    thyl_id_width = int(44 * scale_factor)
    ma_chung_tu_offset_x = int(662 * scale_factor)
    
    thyl_x_center = None
    ma_chung_tu_x_center = None
    
    for line in ocr_result.lines:
        for word in line.words:
            word_x = (word.bounding_rect.x / 2.0) + header_x1
            word_text = word.text.upper()
            
            # Spatial filter for THYL_ID to ignore 'ID' from 'Toa thuốc ID'
            if first_arrow_x + 105 * scale_factor <= word_x <= first_arrow_x + 180 * scale_factor:
                if any(x in word_text for x in ["THYL", "THVL"]):
                    thyl_x_center = word_x + (word.bounding_rect.width / 4.0)
                    print(f"[Grid] Calibrated THYL_ID: Found '{word.text}' (main) at absolute X={thyl_x_center:.1f}")
                elif "ID" in word_text and not thyl_x_center:
                    thyl_x_center = word_x + (word.bounding_rect.width / 4.0) - 15 * scale_factor
                    print(f"[Grid] Calibrated THYL_ID: Found '{word.text}' (right-side) at absolute X={thyl_x_center:.1f}")
                    
            # Spatial filter for Mã chứng từ column header
            if first_arrow_x + 550 * scale_factor <= word_x <= first_arrow_x + 750 * scale_factor:
                if any(x in word_text for x in ["MÃ", "MA"]):
                    ma_chung_tu_x_center = word_x + (word.bounding_rect.width / 4.0) + 25 * scale_factor
                    print(f"[Grid] Calibrated Mã chứng từ: Found '{word.text}' (left-side) at absolute X={ma_chung_tu_x_center:.1f}")
                elif any(x in word_text for x in ["CHỨNG", "CHUNG"]):
                    ma_chung_tu_x_center = word_x + (word.bounding_rect.width / 4.0)
                    print(f"[Grid] Calibrated Mã chứng từ: Found '{word.text}' (center-side) at absolute X={ma_chung_tu_x_center:.1f}")
                elif any(x in word_text for x in ["TỪ", "TIF"]):
                    ma_chung_tu_x_center = word_x + (word.bounding_rect.width / 4.0) - 15 * scale_factor
                    print(f"[Grid] Calibrated Mã chứng từ: Found '{word.text}' (right-side) at absolute X={ma_chung_tu_x_center:.1f}")
                    
    if thyl_x_center:
        thyl_id_x_range = (int(thyl_x_center - 22 * scale_factor), int(thyl_x_center + 22 * scale_factor))
    else:
        thyl_id_x_range = (first_arrow_x + thyl_id_offset_x - thyl_id_width//2, first_arrow_x + thyl_id_offset_x + thyl_id_width//2)
        print("[Grid] Calibration fallback: Using default THYL_ID column offsets")
        
    if ma_chung_tu_x_center:
        # Use asymmetric crop (-42 to +93) to catch all characters of the left-aligned text
        ma_chung_tu_x_range = (int(ma_chung_tu_x_center - 42 * scale_factor), int(ma_chung_tu_x_center + 93 * scale_factor))
    else:
        ma_chung_tu_x_range = (first_arrow_x + ma_chung_tu_offset_x - int(42 * scale_factor), first_arrow_x + ma_chung_tu_offset_x + int(93 * scale_factor))
        print("[Grid] Calibration fallback: Using default Mã chứng từ column offsets")
        
    return thyl_id_x_range, ma_chung_tu_x_range

def locate_main_grid_scrollbar_thumb(screenshot_pil):
    """Locate the visible vertical scrollbar thumb for the main grid."""
    np_img = np.array(screenshot_pil.convert("RGB"))
    screen_h, screen_w = np_img.shape[:2]
    header = locate_main_grid_header(screenshot_pil)
    if header:
        hx, hy, hw, hh = header["box"]
        x1 = max(0, min(screen_w - 48, hx + hw - 8))
        y1 = min(screen_h - 1, hy + hh + 4)
        y2 = max(y1 + 1, screen_h - 105)
    else:
        x1 = max(0, screen_w - 48)
        y1 = 180
        y2 = max(y1 + 1, screen_h - 105)
    crop = np_img[y1:y2, x1:min(screen_w, x1 + 48)]
    if crop.size == 0:
        return None

    channel_spread = crop.max(axis=2) - crop.min(axis=2)
    brightness = crop.mean(axis=2)
    mask = ((channel_spread <= 18) & (brightness >= 120) & (brightness <= 235)).astype("uint8") * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if 4 <= w <= 24 and h >= 35 and x >= crop.shape[1] // 3:
            candidates.append((x1 + x, y1 + y, w, h))
    if not candidates:
        return None
    return max(candidates, key=lambda b: b[3])

def can_scroll_main_grid_down(screenshot_pil):
    """Return True if the main grid scrollbar thumb is not at the bottom."""
    thumb = locate_main_grid_scrollbar_thumb(screenshot_pil)
    if not thumb:
        return False
    _, y, _, h = thumb
    track_bottom = screenshot_pil.height - 120
    return y + h < track_bottom - 12

def scroll_main_grid_down(screenshot_pil=None, clicks=-8):
    """Scroll the main grid down if its scrollbar indicates more rows below."""
    if screenshot_pil is None:
        screenshot_pil = capture_screenshot()
    before_thumb = locate_main_grid_scrollbar_thumb(screenshot_pil)
    if not before_thumb or not can_scroll_main_grid_down(screenshot_pil):
        return False
    pyautogui.moveTo(screenshot_pil.width - 24, int(screenshot_pil.height * 0.55), duration=0.1)
    pyautogui.scroll(clicks)
    time.sleep(0.7)
    after_shot = capture_screenshot()
    after_thumb = locate_main_grid_scrollbar_thumb(after_shot)
    if not after_thumb:
        return True
    return after_thumb[1] > before_thumb[1] + 2

async def calibrate_layered(screenshot_pil, arrows):
    """
    Run layered calibration flow:
    1. Check saved layout profiles first.
    2. Try OpenCV grid header separator line detection.
    3. Fall back to OCR header text processing.
    4. Fall back to scaled standard dimensions.
    Returns: (thyl_range, ma_range, confidence)
    """
    if not arrows:
        return None, None, 0
        
    first_arrow = arrows[0]
    scale_factor = first_arrow[2] / 26.0
    
    # Layer 1: Layout Profile Load
    profile_key = get_current_profile_key(screenshot_pil)
    profiles = load_layout_profiles()
    if profile_key in profiles:
        prof = profiles[profile_key]
        thyl_range = tuple(prof["columns"]["thyl_id"])
        ma_range = tuple(prof["columns"]["ma_chung_tu"])
        
        # Verify alignment
        action_left = prof["columns"]["action"][0] if "action" in prof["columns"] else first_arrow[0] - 10
        action_right = prof["columns"]["action"][1] if "action" in prof["columns"] else first_arrow[0] + 30
        
        if action_left - 15 <= first_arrow[0] <= action_right + 15:
            print(f"[Grid] Layer 1: Restored layout profile '{profile_key}' with 100% confidence.")
            return thyl_range, ma_range, 100
        else:
            print(f"[Grid] Warning: Saved profile '{profile_key}' failed alignment verification. Calibrating again.")

    # Layer 2: OpenCV Separator Lines
    grid = calibrate_main_grid(screenshot_pil)
    if grid:
        thyl_range = grid["columns"]["thyl_id"]
        ma_range = grid["columns"]["ma_chung_tu"]
        action_range = grid["columns"]["action"]
        
        profile_data = {
            "columns": {
                "action": action_range,
                "thyl_id": thyl_range,
                "ma_chung_tu": ma_range
            },
            "scale_factor": scale_factor
        }
        save_layout_profile(profile_key, profile_data)
        print(f"[Grid] Layer 2: OpenCV header calibrated successfully. Saved profile '{profile_key}'.")
        return thyl_range, ma_range, 90

    # Layer 3: OCR Header Calibration
    thyl_range, ma_range = await calibrate_columns(screenshot_pil, first_arrow[0], first_arrow[1], first_arrow[2])
    print("[Grid] Layer 3: OCR header calibrated successfully.")
    return thyl_range, ma_range, 75

async def focus_tab_by_name(tab_name="UPDATE TRẠNG THÁI DỊCH VỤ NỘI TRÚ"):
    """Search for a tab with the specified name in the top section using OCR and click it."""
    screenshot_pil = capture_screenshot()
    crop_w = int(screenshot_pil.width * 0.6)
    crop_h = 65
    top_crop = capture_screenshot(region=(0, 0, crop_w, crop_h))
    
    try:
        top_crop.save("tab_crop_debug.png")
    except Exception as e:
        print(f"[Grid] Error saving tab crop debug: {e}")
        
    try:
        ocr_res = await run_ocr_on_pil_image(top_crop)
    except Exception as e:
        print(f"[Grid] OCR for tabs failed: {e}")
        return False
        
    normalized_tab = remove_vietnamese_diacritics(tab_name).lower()
    target_x = None
    target_y = None
    
    for line in ocr_res.lines:
        line_text = remove_vietnamese_diacritics(line.text).lower()
        if "noi tru" in line_text or "dich vu noi tru" in line_text:
            words = list(line.words)
            if not words:
                continue
            x_coords = [w.bounding_rect.x for w in words]
            y_coords = [w.bounding_rect.y for w in words]
            r_coords = [w.bounding_rect.x + w.bounding_rect.width for w in words]
            b_coords = [w.bounding_rect.y + w.bounding_rect.height for w in words]
            
            x = min(x_coords)
            y = min(y_coords)
            w = max(r_coords) - x
            h = max(b_coords) - y
            
            target_x = int(x + w / 2)
            target_y = int(y + h / 2)
            print(f"[Grid] Found tab '{tab_name}' at screen: ({target_x}, {target_y}) -> '{line.text}'")
            break
            
    if target_x is not None and target_y is not None:
        pyautogui.click(target_x, target_y)
        time.sleep(0.5)
        return True
        
    return False

def locate_top_command_buttons(screenshot_pil):
    """Return top command buttons as (x, y, w, h), sorted left-to-right."""
    np_img = np.array(screenshot_pil.convert("RGB"))
    hsv = cv2.cvtColor(np_img, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array([95, 35, 70]), np.array([106, 255, 245]))
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if 70 <= w <= 280 and 18 <= h <= 45 and y < 180:
            candidates.append((x, y, w, h))

    rows = []
    for box in sorted(candidates, key=lambda b: (b[1], b[0])):
        x, y, w, h = box
        for row in rows:
            row_y = sum(item[1] for item in row) / len(row)
            if abs(y - row_y) <= 10:
                row.append(box)
                break
        else:
            rows.append([box])

    if not rows:
        return []

    command_row = max(rows, key=lambda row: (len(row), -sum(item[1] for item in row) / len(row)))
    return sorted(command_row, key=lambda b: b[0])

def is_command_button_active(screenshot_pil, button_box):
    """Detect the brighter active command button state."""
    np_img = np.array(screenshot_pil.convert("RGB"))
    x, y, w, h = button_box
    inset_x = max(2, min(10, w // 8))
    inset_y = max(2, min(6, h // 4))
    crop = np_img[y + inset_y:y + h - inset_y, x + inset_x:x + w - inset_x]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    mean_h = float(np.mean(hsv[:, :, 0]))
    mean_s = float(np.mean(hsv[:, :, 1]))
    mean_v = float(np.mean(hsv[:, :, 2]))
    return 92 <= mean_h <= 105 and mean_s >= 130 and mean_v >= 150

async def focus_sub_tab(sub_tab_name="Xóa thực THYL, Chứng từ"):
    """
    Search for a sub-tab with the specified name in the screen using OCR,
    and click it to bring it to focus. Retries up to 3 times with different crop regions.
    """
    crop_regions = [
        (0, 40, 0.8, 120),   # Narrow band near the top
        (0, 50, 0.8, 150),   # Slightly wider
        (0, 30, 1.0, 200),   # Even wider fallback
    ]

    screenshot_pil = capture_screenshot()
    buttons = locate_top_command_buttons(screenshot_pil)
    if len(buttons) >= 4:
        x, y, w, h = buttons[3]
        if is_command_button_active(screenshot_pil, buttons[3]):
            print("[Grid] THYL/Chung tu sub-tab is already active. Skipping click.")
            return True
        target_x = x + w // 2
        target_y = y + h // 2
        print(f"[Grid] Selecting THYL/Chung tu sub-tab by command-button row at ({target_x}, {target_y}).")
        pyautogui.click(target_x, target_y)
        time.sleep(0.7)
        verify_shot = capture_screenshot()
        verify_buttons = locate_top_command_buttons(verify_shot)
        if len(verify_buttons) >= 4 and is_command_button_active(verify_shot, verify_buttons[3]):
            return True
        print("[Grid] Clicked THYL/Chung tu sub-tab, but active state was not verified.")

    def click_button_row_fallback(screenshot_pil):
        """Fallback for the THYL button row when OCR misses white text on colored tabs."""
        np_img = np.array(screenshot_pil.convert("RGB"))
        screen_h, screen_w = np_img.shape[:2]
        y1 = min(max(85, 0), screen_h)
        y2 = min(max(165, y1), screen_h)
        x2 = min(int(screen_w * 0.65), screen_w)
        crop = np_img[y1:y2, 0:x2]
        if crop.size == 0:
            return False

        bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask_blue = cv2.inRange(hsv, np.array([88, 35, 55]), np.array([110, 255, 255]))
        mask_gray_blue = cv2.inRange(hsv, np.array([88, 8, 55]), np.array([112, 90, 170]))
        mask = cv2.bitwise_or(mask_blue, mask_gray_blue)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        buttons = []
        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            if bw >= 80 and 18 <= bh <= 45:
                buttons.append((x, y + y1, bw, bh))

        buttons = sorted(buttons, key=lambda b: b[0])
        if len(buttons) >= 4:
            x, y, bw, bh = buttons[3]
            cx = x + bw // 2
            cy = y + bh // 2
            print(f"[Grid] Fallback selected sub-tab by button row at screen: ({cx}, {cy})")
            pyautogui.click(cx, cy)
            time.sleep(0.5)
            return True

        active_mask = cv2.inRange(hsv, np.array([92, 90, 90]), np.array([105, 255, 255]))
        contours, _ = cv2.findContours(active_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        active_buttons = []
        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            if bw >= 90 and 18 <= bh <= 45:
                active_buttons.append((x, y + y1, bw, bh))

        for x, y, bw, bh in sorted(active_buttons, key=lambda b: b[0]):
            if int(screen_w * 0.25) <= x <= int(screen_w * 0.45):
                print("[Grid] Fallback detected active THYL sub-tab by its highlighted button color.")
                return True

        return False

    if click_button_row_fallback(screenshot_pil):
        return True

    last_screenshot = None
    for attempt, (rx1, ry1, rw_pct, ry2) in enumerate(crop_regions):
        print(f"[Grid] Searching for sub-tab '{sub_tab_name}' (Attempt {attempt+1}/{len(crop_regions)}, crop y={ry1}-{ry2})...")
        screenshot_pil = capture_screenshot()
        last_screenshot = screenshot_pil
        crop_x2 = int(screenshot_pil.width * rw_pct)
        crop_img = capture_screenshot(region=(rx1, ry1, crop_x2 - rx1, ry2 - ry1))
        
        if AppConfig.save_debug_images:
            try:
                crop_img.save("sub_tab_crop_debug.png")
            except Exception as e:
                print(f"[Grid] Error saving sub-tab crop debug: {e}")
            
        try:
            ocr_res = await run_ocr_on_pil_image(crop_img)
        except Exception:
            time.sleep(0.3)
            continue
            
        for line in ocr_res.lines:
            print(f"  [OCR sub-tab scan] '{line.text}'")
        
        target_x = None
        target_y = None
        
        for line in ocr_res.lines:
            line_text = remove_vietnamese_diacritics(line.text).lower()
            if "xoa" in line_text and ("thyl" in line_text or "thvl" in line_text or "chung" in line_text):
                words = list(line.words)
                if words:
                    x = min(w.bounding_rect.x for w in words)
                    y = min(w.bounding_rect.y for w in words)
                    w = max(w.bounding_rect.x + w.bounding_rect.width for w in words) - x
                    h = max(w.bounding_rect.y + w.bounding_rect.height for w in words) - y
                    target_x = int(x + w / 2)
                    target_y = int(ry1 + y + h / 2)
                    print(f"[Grid] Found sub-tab '{sub_tab_name}' at screen: ({target_x}, {target_y}) -> '{line.text}'")
                    break
                
        if target_x is not None and target_y is not None:
            pyautogui.click(target_x, target_y)
            time.sleep(0.5)
            return True
            
        time.sleep(0.3)

    if last_screenshot is not None and click_button_row_fallback(last_screenshot):
        return True

    if AppConfig.save_debug_images:
        try:
            (last_screenshot or screenshot_pil).save("sub_tab_failure_debug.png")
        except Exception as e:
            print(f"[Grid] Error saving sub-tab failure debug: {e}")
    print("[Grid] Sub-tab was not confirmed by command-button geometry or OCR.")
    return False
