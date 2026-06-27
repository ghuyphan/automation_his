import re
import time
import pyautogui
import numpy as np
import cv2
from PIL import Image, ImageOps, ImageEnhance
from automation.config import AppConfig
from automation.screen import capture_screenshot
from automation.ocr import run_ocr_on_pil_image, remove_vietnamese_diacritics
from automation.waiters import wait_until_dropdown_visible

def normalize_patient_code(code):
    return "".join(c for c in str(code) if c.isdigit() or c == ".")

def is_so_benh_an(text, is_cap_cuu=False):
    """Checks if a string is a valid Số bệnh án format (XX.XXXXXX) matching the is_cap_cuu constraint."""
    text_upper = text.upper()
    has_cc = "CC" in text_upper
    if is_cap_cuu and not has_cc:
        return False
    if not is_cap_cuu and has_cc:
        return False
    # Look for 2-3 digits, dot, 5-6 digits
    match = re.search(r'\b\d{2,3}\.\d{5,6}\b', text)
    return match is not None

async def find_ma_nguoi_benh_label():
    """
    Search for 'Mã người bệnh' label on the screen using OCR within a targeted region.
    Returns (x, y, w, h) of the label bounding box in screen coordinates or None.
    Retries up to 5 times with a 0.5s delay to handle page load latency.
    """
    for attempt in range(5):
        print(f"[Patient] Searching for 'Mã người bệnh' label (Attempt {attempt+1}/5)...")
        # Grab target left region (0 to 45% screen width, 50 to 350 height)
        screenshot_pil = capture_screenshot()
        crop_x1 = 0
        crop_y1 = 50
        crop_x2 = int(screenshot_pil.width * 0.45)
        crop_y2 = 350
        
        crop_img = capture_screenshot(region=(crop_x1, crop_y1, crop_x2 - crop_x1, crop_y2 - crop_y1))
        
        if AppConfig.save_debug_images:
            try:
                crop_img.save("label_crop_debug.png")
            except Exception as e:
                print(f"[Patient] Error saving label crop debug: {e}")
            
        try:
            ocr_res = await run_ocr_on_pil_image(crop_img)
        except Exception as e:
            print(f"[Patient] OCR for label failed: {e}")
            time.sleep(0.5)
            continue
            
        matches = []
        for line in ocr_res.lines:
            line_text = remove_vietnamese_diacritics(line.text).lower()
            if "ma" in line_text and "benh" in line_text:
                # Union bounding rect of line words
                words = list(line.words)
                if not words:
                    continue
                x_coords = [w.bounding_rect.x for w in words]
                y_coords = [w.bounding_rect.y for w in words]
                r_coords = [w.bounding_rect.x + w.bounding_rect.width for w in words]
                b_coords = [w.bounding_rect.y + w.bounding_rect.height for w in words]
                
                x1 = min(x_coords)
                y1 = min(y_coords)
                w = max(r_coords) - x1
                h = max(b_coords) - y1
                
                matches.append((crop_x1 + x1, crop_y1 + y1, w, h))
                
        if matches:
            # Sort by screen Y coordinate ascending (top-most first)
            matches = sorted(matches, key=lambda m: m[1])
            top_match = matches[0]
            print(f"[Patient] Found label 'Mã người bệnh' at screen ({top_match[0]}, {top_match[1]}) with size {top_match[2]}x{top_match[3]}")
            return top_match
            
        time.sleep(0.5)
        
    return None

async def read_current_patient_code(label_rect):
    """OCR the current patient-code textbox next to the Ma nguoi benh label."""
    screenshot_pil = capture_screenshot()
    lx, ly, lw, lh = label_rect
    candidate_boxes = []

    x1 = max(0, int(lx + lw + 4))
    y1 = max(0, int(ly - max(8, lh * 0.7)))
    candidate_boxes.append((x1, y1, min(screenshot_pil.width, x1 + 230), min(screenshot_pil.height, y1 + 44)))

    candidate_boxes.extend([
        (130, 52, 330, 98),
        (140, 58, 315, 92),
        (0, 48, 380, 120),
    ])

    best_cleaned = ""
    for idx, box in enumerate(candidate_boxes):
        x1, y1, x2, y2 = box
        crop_img = capture_screenshot(region=(x1, y1, x2 - x1, y2 - y1))
        if AppConfig.save_debug_images:
            try:
                crop_img.save(f"patient_code_crop_debug_{idx+1}.png")
            except Exception as e:
                print(f"[Patient] Error saving patient code crop debug: {e}")

        ocr_input = ImageOps.expand(crop_img.convert("L"), border=20, fill="white")
        ocr_input = ocr_input.resize((ocr_input.width * 4, ocr_input.height * 4), Image.Resampling.LANCZOS)
        ocr_input = ImageEnhance.Contrast(ocr_input).enhance(2.5)
        np_crop = np.array(ocr_input)
        _, thresholded = cv2.threshold(np_crop, 180, 255, cv2.THRESH_BINARY)
        ocr_img = Image.fromarray(thresholded)

        if AppConfig.save_debug_images:
            try:
                ocr_img.save(f"patient_code_ocr_debug_{idx+1}.png")
            except Exception as e:
                print(f"[Patient] Error saving patient code OCR debug: {e}")

        try:
            ocr_res = await run_ocr_on_pil_image(ocr_img)
            raw_text = ocr_res.text.strip()
        except Exception as e:
            print(f"[Patient] OCR patient code candidate {idx+1} failed: {e}")
            continue
            
        matches = re.findall(r"\d{5,8}\.\d{6,12}", raw_text)
        cleaned = matches[0] if matches else "".join(c for c in raw_text if c.isdigit() or c == ".")
        print(f"[Patient] Current patient code OCR crop {idx+1}: raw='{raw_text}', cleaned='{cleaned}'")
        if re.fullmatch(r"\d{5,8}\.\d{6,12}", cleaned):
            return cleaned
        if len(cleaned) > len(best_cleaned):
            best_cleaned = cleaned

    return best_cleaned

async def select_so_benh_an(click_x, click_y, label_w, is_cap_cuu=False, stop_checker=None, log_cb=None):
    """
    OCR-scans the dropdown list under the input field and clicks the Số bệnh án row matching is_cap_cuu.
    Retries up to 3 times to account for UI load lag.
    """
    # Wait adaptively for dropdown list to load standard items
    loaded = await wait_until_dropdown_visible(click_x, click_y, label_w, timeout=2.0, stop_checker=stop_checker, log_cb=log_cb)
    
    for attempt in range(3):
        if stop_checker and stop_checker():
            return False
            
        if log_cb:
            log_cb(f"Quét dropdown bệnh án (Cấp cứu={is_cap_cuu}) (Lần {attempt+1}/3)...")
            
        # Crop the dropdown list area relative to input box coordinates and label width
        crop_x1 = max(0, int(click_x - label_w))
        crop_y1 = int(click_y + 10)
        crop_w = int(3.5 * label_w)
        crop_h = 350
        
        dropdown_crop = capture_screenshot(region=(crop_x1, crop_y1, crop_w, crop_h))
        
        if AppConfig.save_debug_images:
            try:
                dropdown_crop.save(f"dropdown_crop_debug_attempt_{attempt+1}.png")
            except Exception as e:
                print(f"[Patient] Error saving dropdown crop debug: {e}")
                
        try:
            ocr_result = await run_ocr_on_pil_image(dropdown_crop)
        except Exception as e:
            print(f"[Patient] OCR dropdown scan failed: {e}")
            time.sleep(0.5)
            continue
            
        target_x = None
        target_y = None
        
        for line in ocr_result.lines:
            line_text = line.text.upper()
            
            # Filter matches by cap cuu constraint
            has_cc = "CC" in line_text or "C/C" in line_text or "/CC" in line_text
            if is_cap_cuu and not has_cc:
                continue
            if not is_cap_cuu and has_cc:
                continue
                
            for word in line.words:
                word_text = word.text
                if is_so_benh_an(word_text, is_cap_cuu):
                    rect = word.bounding_rect
                    target_x = crop_x1 + rect.x + rect.width / 2
                    target_y = crop_y1 + rect.y + rect.height / 2
                    if log_cb:
                        log_cb(f"Tìm thấy 'Số bệnh án' (Cấp cứu={is_cap_cuu}): '{word_text}' tại ({target_x}, {target_y})")
                    break
            if target_x is not None:
                break
                
        if target_x is not None and target_y is not None:
            pyautogui.click(target_x, target_y)
            time.sleep(0.5)
            return True
            
        time.sleep(0.5)
        
    return False
