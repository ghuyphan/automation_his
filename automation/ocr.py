import io
import re
import unicodedata
import numpy as np
import cv2
from PIL import Image, ImageOps, ImageEnhance
from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
from winsdk.windows.graphics.imaging import BitmapDecoder
from winsdk.windows.media.ocr import OcrEngine
from winsdk.windows.globalization import Language
from automation.config import AppConfig

# Global OCR engine cache
_ocr_engine = None

def get_ocr_engine():
    """Lazily initialize and cache the Windows Media OCR engine."""
    global _ocr_engine
    if _ocr_engine is None:
        try:
            _ocr_engine = OcrEngine.try_create_from_user_profile_languages()
        except Exception as e:
            print(f"[OCR] Warning: try_create_from_user_profile_languages failed: {e}")
        if not _ocr_engine:
            try:
                _ocr_engine = OcrEngine.try_create_from_language(Language('en-US'))
            except Exception as e:
                print(f"[OCR] Warning: try_create_from_language(en-US) failed: {e}")
    return _ocr_engine

async def run_ocr_on_pil_image(pil_img):
    """Perform in-memory OCR on a PIL Image using cached Windows Media OCR."""
    engine = get_ocr_engine()
    if not engine:
        raise RuntimeError("Windows OCR Engine could not be initialized. COM may not be initialized.")

    buf = io.BytesIO()
    pil_img.save(buf, format='PNG')
    img_bytes = buf.getvalue()
    
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(img_bytes)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    
    decoder = await BitmapDecoder.create_async(stream)
    software_bitmap = await decoder.get_software_bitmap_async()
    
    result = await engine.recognize_async(software_bitmap)
    return result

def remove_vietnamese_diacritics(text):
    """Normalize text to decomposed form and strip diacritical marks to handle English/Vietnamese OCR fallbacks."""
    normalized = unicodedata.normalize('NFD', text)
    cleaned = "".join(c for c in normalized if not unicodedata.combining(c))
    cleaned = cleaned.replace('đ', 'd').replace('Đ', 'D')
    return cleaned

def is_thyl_id_populated(text):
    """
    Heuristically determine if a cell contains a valid THYL_ID.
    If it is blank, or contains date/time characters (from column overlap), returns False.
    """
    t = text.strip()
    if not t:
        return False
    if '/' in t or ':' in t:
        return False
    cleaned = t.upper().replace('S', '5').replace('O', '0').replace('I', '1').replace('B', '8').replace('E', '8').replace('G', '6')
    digits = [c for c in cleaned if c.isdigit()]
    return len(digits) >= 3

def is_cell_visually_blank(pil_img, std_threshold=6.0, dark_pixel_threshold=200, dark_pixel_ratio=0.004):
    """Use image variance and pixel density to quickly determine if a crop is blank (no text)."""
    np_img = np.array(pil_img.convert("L"))
    
    # 1. Uniform background check using standard deviation
    std = np.std(np_img)
    if std < std_threshold:
        return True
        
    # 2. Dark pixel ratio check (blank cells have mostly white background)
    dark_count = np.sum(np_img < dark_pixel_threshold)
    ratio = dark_count / np_img.size
    if ratio < dark_pixel_ratio:
        return True
        
    return False

def preprocess_pil(crop_img, scale=2, padding=20, thresh_val=None):
    """Preprocess PIL image for optimized OCR reading."""
    if padding > 0:
        padded = ImageOps.expand(crop_img, border=padding, fill='white')
    else:
        padded = crop_img
        
    gray = padded.convert('L')
    w, h = gray.size
    resized = gray.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
    
    # Enhance contrast
    enhancer = ImageEnhance.Contrast(resized)
    contrasted = enhancer.enhance(2.5)
    
    if thresh_val is not None:
        np_img = np.array(contrasted)
        _, th = cv2.threshold(np_img, thresh_val, 255, cv2.THRESH_BINARY)
        return Image.fromarray(th)
    
    return contrasted

def clean_ma_code_perfect(raw_text):
    """Sanitize and correct typical OCR misread patterns for the 14-character document code."""
    temp = raw_text.strip().upper()
    if not temp:
        return ""
    
    # Remove spaces first
    temp = "".join(temp.split())
    
    # Replace common OCR misreads in the middle part "13260"
    temp = temp.replace("É", "6").replace("O", "0").replace("S", "5").replace("o", "0").replace("U", "4").replace("I", "1").replace("L", "1").replace("T", "1")
    temp = temp.replace("132É0", "13260").replace("132Ö0", "13260").replace("132O0", "13260").replace("13200", "13260")
    
    # Find "13260" core
    idx = temp.find("13260")
    if idx != -1:
        temp = "019X13260" + temp[idx + 5:]
        
    # Replace non-alphanumeric (like dots, hyphens) to clean up before matching tails
    temp = "".join(c for c in temp if c.isalnum())
    
    # Tail correction rules
    temp = temp.replace("52Z43", "58443").replace("52243", "58443").replace("53443", "58443").replace("38443", "58443").replace("5W3", "58443").replace("5443", "58443")
    temp = temp.replace("53444", "58444").replace("5444", "58444")
    temp = temp.replace("5U24", "58424").replace("S8424", "58424")
    temp = temp.replace("S8331", "58331").replace("5833T", "58331")
    temp = temp.replace("S8188", "58188").replace("5818B", "58188")
    
    # Clean trailing letters/artifacts
    if len(temp) == 14:
        if temp.endswith("T") or temp.endswith("I"):
            temp = temp[:-1] + "1"
            
    # Final cleanup of any lingering letters in the tail
    # A valid code should be exactly 14 characters: 019X13260 + 5 digits
    if len(temp) == 14:
        tail = list(temp[9:])
        for i in range(len(tail)):
            c = tail[i]
            if c == 'S': tail[i] = '5'
            elif c == 'O' or c == 'D' or c == 'Q' or c == 'U': tail[i] = '0'
            elif c == 'I' or c == 'L' or c == 'T' or c == 'Y' or c == 'J': tail[i] = '1'
            elif c == 'B' or c == 'C': tail[i] = '8'
            elif c == 'Z': tail[i] = '2'
            elif c == 'G': tail[i] = '6'
            elif c == 'A': tail[i] = '4'
        temp = temp[:9] + "".join(tail)
        
    return temp

# Adaptive OCR caching index
_last_successful_ocr_method_idx = 0

async def extract_document_code(screenshot_pil, ma_range, row_y, scale_factor, stop_checker=None):
    """
    OCR extract document code with adaptive preprocessing method caching and shift retries.
    Checks stop_checker regularly to abort early.
    """
    global _last_successful_ocr_method_idx
    
    # Define shifts to try
    ranges = [
        (ma_range[0], ma_range[1]),  # Range 1 (620 to 755)
        (ma_range[0] + int(18 * scale_factor), ma_range[1] + int(18 * scale_factor)), # Range 2 (638 to 773)
        (ma_range[0] + int(10 * scale_factor), ma_range[1] + int(10 * scale_factor)), # Range 3 (630 to 765)
    ]
    
    y_shifts = [0, -1, 1, -2, 2]
    
    # 14 distinct preprocessing methods
    def get_method_img(ma_crop, idx):
        methods = [
            lambda: ma_crop.resize((ma_crop.size[0] * 2, ma_crop.size[1] * 2), Image.Resampling.LANCZOS),
            lambda: ImageOps.expand(ma_crop, border=20, fill='white').resize((ma_crop.size[0] * 2 + 80, ma_crop.size[1] * 2 + 80), Image.Resampling.LANCZOS),
            lambda: ma_crop.resize((ma_crop.size[0] * 3, ma_crop.size[1] * 3), Image.Resampling.LANCZOS),
            lambda: ImageOps.expand(ma_crop, border=20, fill='white').resize((ma_crop.size[0] * 3 + 120, ma_crop.size[1] * 3 + 120), Image.Resampling.LANCZOS),
            lambda: preprocess_pil(ma_crop, scale=2, padding=0, thresh_val=None),
            lambda: preprocess_pil(ma_crop, scale=2, padding=20, thresh_val=None),
            lambda: preprocess_pil(ma_crop, scale=3, padding=0, thresh_val=None),
            lambda: preprocess_pil(ma_crop, scale=3, padding=20, thresh_val=None),
            lambda: preprocess_pil(ma_crop, scale=4, padding=0, thresh_val=None),
            lambda: preprocess_pil(ma_crop, scale=4, padding=20, thresh_val=None),
            lambda: preprocess_pil(ma_crop, scale=4, padding=20, thresh_val=150),
            lambda: preprocess_pil(ma_crop, scale=4, padding=0, thresh_val=150),
            lambda: preprocess_pil(ma_crop, scale=4, padding=20, thresh_val=127),
            lambda: preprocess_pil(ma_crop, scale=4, padding=0, thresh_val=127),
        ]
        if idx < len(methods):
            return methods[idx]()
        return None

    # Safe limits
    max_methods = 14
    ocr_depth = min(AppConfig.ocr_retry_depth, max_methods)
    
    # Create method index evaluation sequence: try last successful first
    method_sequence = [_last_successful_ocr_method_idx] + [i for i in range(ocr_depth) if i != _last_successful_ocr_method_idx]
    # Restruct list based on user configured depth
    method_sequence = [m for m in method_sequence if m < ocr_depth]

    for r_start, r_end in ranges:
        for y_shift in y_shifts:
            if stop_checker and stop_checker():
                return "", ""
                
            shift_y = row_y + int(y_shift * scale_factor)
            ma_crop = screenshot_pil.crop((r_start, shift_y - int(10 * scale_factor), r_end, shift_y + int(10 * scale_factor)))
            
            # Skip if cropped region is visually blank
            if is_cell_visually_blank(ma_crop):
                continue
                
            for m_idx in method_sequence:
                if stop_checker and stop_checker():
                    return "", ""
                    
                img = get_method_img(ma_crop, m_idx)
                if img is None:
                    continue
                    
                try:
                    ocr_res = await run_ocr_on_pil_image(img)
                    raw_text = ocr_res.text.strip()
                    cleaned = clean_ma_code_perfect(raw_text)
                    if len(cleaned) == 14 and cleaned.upper().startswith("019X"):
                        # Update global successful cache index
                        _last_successful_ocr_method_idx = m_idx
                        return raw_text, cleaned
                except Exception as e:
                    # Log warning or pass
                    pass
                    
    return "", ""
