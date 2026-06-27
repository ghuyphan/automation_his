import os
import time
import asyncio
import cv2
import re
import numpy as np
from PIL import Image
from automation.config import AppConfig
from automation.screen import capture_screenshot, focus_window, minimize_console
from automation.templates import locate_all_templates, locate_single_template
from automation.ocr import (
    run_ocr_on_pil_image, 
    remove_vietnamese_diacritics, 
    is_cell_visually_blank,
    extract_document_code, 
    clean_ma_code_perfect
)
from automation.grid import (
    calibrate_layered, 
    get_visible_row_arrows, 
    scroll_main_grid_down,
    has_visible_data_rows
)
from automation.patient import (
    find_ma_nguoi_benh_label, 
    read_current_patient_code, 
    select_so_benh_an,
    normalize_patient_code
)
from automation.waiters import (
    wait_until_dropdown_visible, 
    wait_until_co_button_visible,
    is_filter_popup_open, 
    wait_until_filter_popup_state, 
    wait_until_grid_refreshed
)
from automation.deletion import perform_row_deletion
from automation.diagnostics import SessionLogger, generate_dry_run_report

async def perform_batch_ocr(screenshot_pil, arrows, ma_range, scale_factor):
    """
    OCR extract document codes for all rows at once to minimize OCR latency.
    Returns: a dict mapping row index -> (raw_text, cleaned_text)
    """
    if not arrows:
        return {}
        
    first_arrow = arrows[0]
    last_arrow = arrows[-1]
    
    # Calculate crop region that covers the document code column for all rows
    x1 = ma_range[0]
    x2 = ma_range[1]
    y1 = first_arrow[1] - int(12 * scale_factor)
    y2 = last_arrow[1] + last_arrow[3] + int(12 * scale_factor)
    
    crop_w = x2 - x1
    crop_h = y2 - y1
    
    # Targeted screenshot of the entire column height
    column_crop = capture_screenshot(region=(x1, y1, crop_w, crop_h))
    
    # Preprocess: Grayscale, upscale by 2x
    gray = column_crop.convert('L')
    upscaled = gray.resize((crop_w * 2, crop_h * 2), Image.Resampling.LANCZOS)
    
    results = {}
    try:
        ocr_result = await run_ocr_on_pil_image(upscaled)
        
        # Match lines to rows based on Y-coordinate alignment
        for line in ocr_result.lines:
            words = list(line.words)
            if not words:
                continue
            y_coords = [w.bounding_rect.y for w in words]
            b_coords = [w.bounding_rect.y + w.bounding_rect.height for w in words]
            
            # Map upscaled crop Y back to normal crop coordinates (dividing by 4)
            line_y = (min(y_coords) + max(b_coords)) / 4.0
            screen_line_y = y1 + line_y
            
            cleaned = clean_ma_code_perfect(line.text)
            if len(cleaned) == 14 and cleaned.upper().startswith("019X"):
                # Find matching row whose Y coordinate aligns
                for idx, arrow in enumerate(arrows):
                    row_y = arrow[1] + arrow[3] // 2
                    if abs(screen_line_y - row_y) < 16 * scale_factor:
                        results[idx] = (line.text, cleaned)
                        break
    except Exception as e:
        print(f"[Engine] Batch OCR failed: {e}")
        
    return results

class AutomationEngine:
    """Core automation runner managing eHospital tasks orchestrations."""
    def __init__(self, callbacks=None):
        """
        callbacks dictionary can include:
        - 'on_log': callable(str)
        - 'on_status': callable(str)
        - 'on_progress': callable(scanned, processed, total_pct)
        - 'on_complete': callable(summary, backup_file)
        - 'stop_checker': callable() -> bool
        """
        self.callbacks = callbacks or {}
        
    def _log(self, msg):
        cb = self.callbacks.get('on_log')
        if cb:
            cb(msg)
        else:
            print(msg)
            
    def _status(self, status):
        cb = self.callbacks.get('on_status')
        if cb:
            cb(status)
            
    def _progress(self, scanned, processed, total_pct):
        cb = self.callbacks.get('on_progress')
        if cb:
            cb(scanned, processed, total_pct)
            
    def _is_stopped(self):
        cb = self.callbacks.get('stop_checker')
        return cb() if cb else False

    async def preflight_grid_check(self):
        """Check template and grid alignment integrity before starting deletions."""
        self._log("Kiểm tra cấu hình màn hình trước khi chạy...")
        icon_path, co_path = AppConfig.get_template_paths()
        
        missing = [p for p in (icon_path, co_path) if not os.path.exists(p)]
        if missing:
            self._log(f"Lỗi: Thiếu file nhận dạng: {', '.join(missing)}")
            return False

        screenshot_pil = capture_screenshot()
        arrows = get_visible_row_arrows(screenshot_pil)
        if not arrows:
            self._log("Lỗi: Không tìm thấy dòng mũi tên xanh trên màn hình. Hãy chắc chắn bảng HIS đang hiển thị.")
            return False
            
        thyl_range, ma_range, conf = await calibrate_layered(screenshot_pil, arrows)
        if not thyl_range or not ma_range:
            self._log("Lỗi: Không hiệu chuẩn được các cột lưới bảng.")
            return False
            
        self._log(f"Kiểm tra OK: phát hiện {len(arrows)} dòng, hiệu chuẩn cột đạt {conf}% tin cậy.")
        return True

    async def run(self, ma_nguoi_benh, is_cap_cuu, is_dry_run):
        """Execute the core eHospital automation sequence."""
        self._status("Khởi tạo")
        self._log(f"=== Bắt đầu Tự động hóa HIS (Phiên bản {AppConfig.VERSION}) ===")
        self._log(f"Mã người bệnh: {ma_nguoi_benh} | Cấp cứu: {is_cap_cuu} | Chạy thử: {is_dry_run}")
        
        # COM initialization must be done in caller thread
        minimize_console()
        time.sleep(0.5)
        
        # 1. Focus Window
        self._status("Đang tìm app HIS")
        self._log("Đang định vị cửa sổ eHospital...")
        if not focus_window():
            self._log("Lỗi: Không thể tìm thấy hoặc tập trung vào cửa sổ HIS. Dừng chạy.")
            return
            
        # 2. Focus Tab
        if self._is_stopped(): return
        self._status("Đang chọn Tab")
        self._log("Đang tìm tab UPDATE TRẠNG THÁI DỊCH VỤ NỘI TRÚ...")
        if await focus_tab_by_name("UPDATE TRẠNG THÁI DỊCH VỤ NỘI TRÚ"):
            self._log("Đã chọn tab nội trú thành công.")
        else:
            self._log("Không tìm thấy tab. Có thể tab đã được chọn sẵn.")
            
        # 3. Handle Patient Input
        if self._is_stopped(): return
        self._status("Đang nhập Mã NB")
        self._log("Đang tìm ô nhập Mã người bệnh...")
        label_rect = await find_ma_nguoi_benh_label()
        if not label_rect:
            self._log("Lỗi: Không thể tìm thấy ô nhập Mã người bệnh trên màn hình.")
            return
            
        lx, ly, lw, lh = label_rect
        click_x = lx + int(1.75 * lw)
        click_y = ly + int(lh / 2)
        
        # Read current patient code (avoid double typing if matching)
        pyautogui.click(click_x, click_y)
        time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.05)
        pyautogui.hotkey('ctrl', 'c')
        time.sleep(0.1)
        
        # Read from clipboard directly without external dependencies
        import ctypes
        from automation.config import AppConfig
        
        # ClipboardUnicode reads
        from automate import get_windows_clipboard_text
        current_code = normalize_patient_code(get_windows_clipboard_text())
        if not current_code:
            current_code = await read_current_patient_code(label_rect)
            
        requested_code = normalize_patient_code(ma_nguoi_benh)
        if current_code == requested_code:
            self._log(f"Mã người bệnh '{ma_nguoi_benh}' đã khớp sẵn. Bỏ qua nhập lại.")
        else:
            self._log(f"Mã hiện tại: '{current_code or '(trống)'}'. Đang dán mã mới '{ma_nguoi_benh}'...")
            pyautogui.click(click_x, click_y)
            time.sleep(0.05)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.05)
            pyautogui.press('delete')
            time.sleep(0.05)
            
            # Direct clipboard write
            from gui import copy_text_to_clipboard
            copy_text_to_clipboard(requested_code)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
            
        # Select patient from dropdown list
        if self._is_stopped(): return
        self._status("Chọn bệnh án")
        mode_str = "chứa /CC" if is_cap_cuu else "không chứa /CC"
        selected = await select_so_benh_an(click_x, click_y, lw, is_cap_cuu=is_cap_cuu, stop_checker=self._is_stopped, log_cb=self._log)
        if not selected:
            self._log(f"Lỗi: Không thể chọn Số bệnh án {mode_str} từ danh sách.")
            return
            
        # 4. Switch to Delete sub-tab
        if self._is_stopped(): return
        self._status("Chọn Sub-tab")
        self._log("Đang chuyển sang sub-tab Xóa thực THYL, Chứng từ...")
        if await focus_sub_tab("Xóa thực THYL, Chứng từ"):
            self._log("Đã chọn sub-tab thành công.")
        else:
            self._log("Lỗi: Không thể chuyển sang sub-tab Xóa thực THYL.")
            return
        time.sleep(0.4)
        
        # 5. Countdown manual filter
        if self._is_stopped(): return
        self._status("Chờ lọc thủ công")
        self._log("Chờ người dùng tự áp dụng bộ lọc Thời gian YL trên HIS...")
        
        # We will loop countdown externally in GUI or here
        # For CLI / CLI mode: wait config.manual_filter_seconds
        for remaining in range(AppConfig.manual_filter_seconds, 0, -1):
            if self._is_stopped(): return
            self._status(f"Lọc thủ công ({remaining}s)")
            time.sleep(1)
            
        # 6. Preflight grid validation
        if self._is_stopped(): return
        if not await self.preflight_grid_check():
            self._log("Lỗi: Kiểm tra an toàn màn hình không đạt. Dừng chạy.")
            return
            
        # Start logger
        logger = SessionLogger(ma_nguoi_benh, is_dry_run)
        
        scanned_rows_count = 0
        processed_rows_count = 0
        blank_thyl_count = 0
        ocr_failed_count = 0
        cleared_codes = []
        
        iteration = 1
        
        while not self._is_stopped():
            self._status(f"Quét lần {iteration}")
            self._log(f"--- Đang quét lưới bảng (Lần {iteration}) ---")
            
            screenshot_pil = capture_screenshot()
            arrows = get_visible_row_arrows(screenshot_pil)
            if not arrows:
                self._log("Không tìm thấy mũi tên nào trên lưới bảng. Tiến trình hoàn tất.")
                break
                
            total_arrows = len(arrows)
            self._log(f"Phát hiện {total_arrows} dòng dữ liệu trên màn hình.")
            
            # Calibrate layered X column bounds
            thyl_range, ma_range, conf = await calibrate_layered(screenshot_pil, arrows)
            if not thyl_range or not ma_range:
                self._log("Cảnh báo: Không thể hiệu chuẩn lưới bảng lần này. Dừng chạy.")
                break
                
            scale_factor = arrows[0][2] / 26.0
            
            # Optimization wins: Batch OCR for the column if fast mode is enabled
            batch_ocr_results = {}
            if AppConfig.opt_mode == "fast":
                self._status("Batch OCR")
                self._log("Đang chạy Batch OCR trích xuất tất cả Mã chứng từ...")
                batch_ocr_results = await perform_batch_ocr(screenshot_pil, arrows, ma_range, scale_factor)
                self._log(f"Batch OCR trích xuất được {len(batch_ocr_results)} mã.")
                
            rows_processed_this_scan = 0
            
            for idx, arrow in enumerate(arrows):
                if self._is_stopped(): break
                
                # Progress update
                progress_pct = (idx + 1) / total_arrows
                self._progress(scanned_rows_count + idx + 1, processed_rows_count, progress_pct)
                
                ax, ay, aw, ah = arrow
                row_y = ay + ah // 2
                scanned_rows_count += 1
                
                # Check visual blank cell check first (extremely fast!)
                thyl_w = thyl_range[1] - thyl_range[0]
                thyl_h = int(20 * scale_factor)
                thyl_crop = screenshot_pil.crop((thyl_range[0], row_y - int(10 * scale_factor), thyl_range[1], row_y + int(10 * scale_factor)))
                
                if is_cell_visually_blank(thyl_crop):
                    blank_thyl_count += 1
                    self._log(f"Dòng {idx+1} (Y={row_y}): THYL_ID trống (kiểm tra pixel). Bỏ qua.")
                    continue
                    
                # Read THYL ID text (only if not visually blank)
                # Retry OCR up to 3 shifts
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
                    blank_thyl_count += 1
                    self._log(f"Dòng {idx+1} (Y={row_y}): THYL_ID trống (sau OCR). Bỏ qua.")
                    continue
                    
                # Get document code
                ma_text, cleaned_ma = "", ""
                if idx in batch_ocr_results:
                    ma_text, cleaned_ma = batch_ocr_results[idx]
                    
                if not cleaned_ma:
                    # Fallback to row-specific OCR
                    ma_text, cleaned_ma = await extract_document_code(
                        screenshot_pil, ma_range, row_y, scale_factor, stop_checker=self._is_stopped
                    )
                    
                sanitized_ma = ""
                if cleaned_ma:
                    sanitized_ma = "".join(c for c in cleaned_ma if c.isalnum())
                    self._log(f"Dòng {idx+1} (Y={row_y}): Trích xuất Mã chứng từ = '{ma_text}' (đã chuẩn hóa: '{cleaned_ma}')")
                else:
                    ocr_failed_count += 1
                    self._log(f"Cảnh báo: Dòng {idx+1} (Y={row_y}): Không đọc được Mã chứng từ.")
                    
                # Perform action / Deletion
                if is_dry_run:
                    self._log(f"[CHẠY THỬ] Dòng {idx+1} (Y={row_y}): Ô THYL_ID = '{thyl_text}' (sẽ xóa khi chạy thật).")
                    if sanitized_ma:
                        cleared_codes.append(sanitized_ma)
                    processed_rows_count += 1
                    logger.log_row(idx+1, thyl_text, sanitized_ma, thyl_crop, status="dry_run")
                    rows_processed_this_scan += 1
                    continue
                    
                # Live Deletion
                self._status(f"Xóa dòng {idx+1}")
                success, crop_evidence = await perform_row_deletion(
                    arrow, thyl_range, ma_range, sanitized_ma, scale_factor, stop_checker=self._is_stopped, log_cb=self._log
                )
                
                if success:
                    processed_rows_count += 1
                    if sanitized_ma:
                        cleared_codes.append(sanitized_ma)
                    logger.log_row(idx+1, thyl_text, sanitized_ma, crop_evidence, status="deleted")
                    rows_processed_this_scan += 1
                    self._log(f"Dòng {idx+1} đã xóa thành công.")
                    # Recapturing screen is necessary since grid has modified
                    break
                else:
                    self._log(f"Dòng {idx+1} bỏ qua do lỗi verification.")
                    logger.log_row(idx+1, thyl_text, sanitized_ma, thyl_crop, status="failed_verification")
                    
            if self._is_stopped():
                break
                
            if is_dry_run:
                if scroll_main_grid_down(screenshot_pil):
                    self._log("[CHẠY THỬ] Đã cuộn xuống lưới danh sách để quét thêm...")
                    iteration += 1
                    continue
                break
                
            if rows_processed_this_scan == 0:
                if scroll_main_grid_down(screenshot_pil):
                    self._log("Các dòng hiển thị đều trống THYL_ID. Đã cuộn xuống để quét tiếp...")
                    iteration += 1
                    continue
                break
                
            iteration += 1
            if iteration > 50:
                self._log("Giới hạn an toàn 50 lần quét đạt. Dừng tiến trình.")
                break
                
        # Finalize log
        logger.finalize(scanned_rows_count, blank_thyl_count, ocr_failed_count, cleared_codes)
        log_file = logger.save_log()
        
        unique_codes = sorted(list(set(cleared_codes)))
        backup_file = "backup_extracted_codes.txt"
        try:
            with open(backup_file, "w", encoding="utf-8") as f:
                for code in unique_codes:
                    f.write(code + "\n")
        except Exception as e:
            self._log(f"Lỗi lưu file sao lưu: {e}")
            
        summary = logger.summary
        if is_dry_run:
            report = generate_dry_run_report(summary)
            self._log("\n" + report)
            
        self._status("Hoàn tất")
        complete_cb = self.callbacks.get('on_complete')
        if complete_cb:
            complete_cb(summary, backup_file)

async def focus_sub_tab(sub_tab_name="Xóa thực THYL, Chứng từ"):
    """Search and click sub-tab tab header button using OCR and horizontal separators fallbacks."""
    from automation.screen import capture_screenshot
    from automate import focus_sub_tab as raw_focus_sub_tab
    return await raw_focus_sub_tab(sub_tab_name)
