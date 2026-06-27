import os
import cv2
import numpy as np

# In-memory caches for original and rescaled template images
_template_cache = {}
_scaled_template_cache = {}

def get_cached_template(template_path):
    """Load and cache the template image from file path."""
    abs_path = os.path.abspath(template_path)
    if abs_path not in _template_cache:
        if not os.path.exists(abs_path):
            print(f"[Templates] Error: Template file '{abs_path}' not found!")
            return None
        template = cv2.imread(abs_path)
        if template is None:
            print(f"[Templates] Error: Template file '{abs_path}' could not be read!")
            return None
        _template_cache[abs_path] = template
    return _template_cache[abs_path]

def get_scaled_templates(template_path):
    """Retrieve or precompute scaled versions of the template image (from 80% to 150%)."""
    abs_path = os.path.abspath(template_path)
    if abs_path not in _scaled_template_cache:
        template = get_cached_template(abs_path)
        if template is None:
            return []
        
        # Hardcoded DPI scale increments
        scales = [0.8, 0.9, 1.0, 1.1, 1.15, 1.2, 1.25, 1.3, 1.4, 1.5]
        scaled_list = []
        for scale in scales:
            resized_t = cv2.resize(template, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            scaled_list.append((scale, resized_t))
        _scaled_template_cache[abs_path] = scaled_list
    return _scaled_template_cache[abs_path]

def locate_all_templates(template_path, screenshot_bgr, threshold=0.72):
    """Locate all non-overlapping matches of a template image on the screen using pre-scaled cached targets."""
    scaled_templates = get_scaled_templates(template_path)
    if not scaled_templates:
        return []
        
    best_matches = []
    for scale, resized_t in scaled_templates:
        h, w = resized_t.shape[:2]
        
        # Skip templates larger than the screen/crop
        if h > screenshot_bgr.shape[0] or w > screenshot_bgr.shape[1]:
            continue
            
        res = cv2.matchTemplate(screenshot_bgr, resized_t, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        
        for pt in zip(*loc[::-1]):
            score = res[pt[1], pt[0]]
            best_matches.append((pt[0], pt[1], w, h, score))
            
    # Sort matches by template score descending
    best_matches = sorted(best_matches, key=lambda x: x[4], reverse=True)
    
    unique_matches = []
    for pt in best_matches:
        x, y, w, h, score = pt
        too_close = False
        for ux, uy, uw, uh, uscore in unique_matches:
            # Prevent multiple overlapping detections
            if abs(x - ux) < w // 2 and abs(y - uy) < h // 2:
                too_close = True
                break
        if not too_close:
            unique_matches.append((x, y, w, h, score))
            
    return [(x, y, w, h) for (x, y, w, h, score) in unique_matches]

def locate_single_template(template_path, screenshot_bgr, threshold=0.72):
    """Locate a single match of a template on the screen and return its center coordinate."""
    matches = locate_all_templates(template_path, screenshot_bgr, threshold)
    if matches:
        x, y, w, h = matches[0]
        return (x + w // 2, y + h // 2)
    return None
