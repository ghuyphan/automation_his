import os
import json
import time
import base64
import io
from PIL import Image
from automation.config import AppConfig

class SessionLogger:
    """
    Orchestrates session logging, collecting runtime metrics, and saving base64
    screen crops of deleted rows as 'undo evidence' verification.
    """
    def __init__(self, patient_code, is_dry_run):
        self.patient_code = patient_code
        self.is_dry_run = is_dry_run
        self.start_time = time.time()
        self.records = []
        self.summary = {}
        
    def log_row(self, row_idx, thyl_id, ma_chung_tu, crop_pil=None, status="success"):
        """Append details of a processed row. If crop_pil is provided, embed it as base64."""
        evidence_b64 = ""
        if crop_pil is not None:
            try:
                buffered = io.BytesIO()
                crop_pil.save(buffered, format="PNG")
                evidence_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
            except Exception as e:
                print(f"[Diagnostics] Warning: Could not encode row evidence image: {e}")
            
        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "row_index": row_idx,
            "thyl_id": thyl_id,
            "ma_chung_tu": ma_chung_tu,
            "status": status,
            "evidence_image_b64": evidence_b64
        }
        self.records.append(record)
        
    def finalize(self, total_scanned, blank_count, ocr_fail_count, deleted_codes):
        """Compile session final summary statistics."""
        duration = time.time() - self.start_time
        self.summary = {
            "patient_code": self.patient_code,
            "is_dry_run": self.is_dry_run,
            "mode": "Chạy thử (Dry Run)" if self.is_dry_run else "Chạy thật (Live Deletion)",
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.start_time)),
            "end_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": round(duration, 2),
            "total_scanned_rows": total_scanned,
            "blank_thyl_count": blank_count,
            "ocr_failed_count": ocr_fail_count,
            "deleted_rows_count": len(self.records),
            "unique_deleted_codes_count": len(set(deleted_codes)),
            "unique_deleted_codes": sorted(list(set(deleted_codes)))
        }
        
    def save_log(self):
        """Write session summary and evidence records to a local JSON log file."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        os.makedirs(AppConfig.SESSION_LOG_DIR, exist_ok=True)
        filename = f"session_log_{self.patient_code}_{timestamp}.json"
        filepath = os.path.join(AppConfig.SESSION_LOG_DIR, filename)
        
        session_data = {
            "summary": self.summary,
            "deletions": self.records
        }
        
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            print(f"[Diagnostics] Session evidence log saved to {filepath}")
            return filepath
        except Exception as e:
            print(f"[Diagnostics] Error saving session log: {e}")
            return None

def generate_dry_run_report(summary):
    """Format a detailed dry-run report for output or printing."""
    lines = [
        "==================================================",
        "BÁO CÁO CHẠY THỬ (DRY RUN REPORT)",
        "==================================================",
        f"Mã người bệnh:   {summary.get('patient_code', 'N/A')}",
        f"Thời gian chạy:  {summary.get('start_time', 'N/A')} -> {summary.get('end_time', 'N/A')}",
        f"Thời lượng:      {summary.get('duration_seconds', 0)} giây",
        f"Dòng đã quét:    {summary.get('total_scanned_rows', 0)}",
        f"Dòng trống:      {summary.get('blank_thyl_count', 0)} (bỏ qua)",
        f"Lỗi đọc mã:      {summary.get('ocr_failed_count', 0)}",
        f"Dòng có thể xóa: {summary.get('deleted_rows_count', 0)}",
        f"Mã chứng từ duy nhất có thể xóa ({summary.get('unique_deleted_codes_count', 0)}):",
    ]
    for code in summary.get('unique_deleted_codes', []):
        lines.append(f"  - {code}")
    lines.append("==================================================")
    return "\n".join(lines)
