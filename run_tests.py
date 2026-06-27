import sys
import unittest
import numpy as np
from PIL import Image

# Add path to load modules
from automation.patient import normalize_patient_code, is_so_benh_an
from automation.ocr import clean_ma_code_perfect, is_thyl_id_populated, is_cell_visually_blank

class TestHISAutomationLogic(unittest.TestCase):
    def test_normalize_patient_code(self):
        self.assertEqual(normalize_patient_code("123.4567"), "123.4567")
        self.assertEqual(normalize_patient_code("  123 4567 "), "1234567")
        self.assertEqual(normalize_patient_code("abc12.34xyz"), "12.34")

    def test_is_so_benh_an(self):
        # Format is XX.XXXXXX or CC/C/C indicators
        self.assertTrue(is_so_benh_an("23.01456", is_cap_cuu=False))
        self.assertFalse(is_so_benh_an("23.01456 CC", is_cap_cuu=False))
        
        self.assertTrue(is_so_benh_an("23.01456 CC", is_cap_cuu=True))
        self.assertTrue(is_so_benh_an("23.01456 /CC", is_cap_cuu=True))
        self.assertFalse(is_so_benh_an("23.01456", is_cap_cuu=True))
        self.assertFalse(is_so_benh_an("INVALID", is_cap_cuu=False))

    def test_clean_ma_code_perfect(self):
        # Expected format is 14 chars starting with 019X13260 + 5 digits
        raw_valid = " 019X1326O58443 "
        self.assertEqual(clean_ma_code_perfect(raw_valid), "019X1326058443")
        
        # Test replacement in middle and tails
        raw_dirty = "O19X132Ö052Z43"
        self.assertEqual(clean_ma_code_perfect(raw_dirty), "019X1326058443")
        
        raw_tail_letters = "019X132605833T"
        self.assertEqual(clean_ma_code_perfect(raw_tail_letters), "019X1326058331")

    def test_is_thyl_id_populated(self):
        self.assertTrue(is_thyl_id_populated("12345"))
        self.assertTrue(is_thyl_id_populated("YL890"))
        # Date/time character detection
        self.assertFalse(is_thyl_id_populated("12/06/2026"))
        self.assertFalse(is_thyl_id_populated("07:15:00"))
        self.assertFalse(is_thyl_id_populated("   "))

    def test_is_cell_visually_blank(self):
        # Create a completely blank (white) image
        blank_img = Image.new("RGB", (100, 20), color="white")
        self.assertTrue(is_cell_visually_blank(blank_img))
        
        # Create a blank image with a light selection blue background (typical HIS selected row)
        selected_blank = Image.new("RGB", (100, 20), color=(220, 235, 255))
        self.assertTrue(is_cell_visually_blank(selected_blank))
        
        # Create an image with dark text pixels (not blank)
        text_img = Image.new("RGB", (100, 20), color="white")
        # Draw a black line mimicking text stroke
        np_img = np.array(text_img)
        np_img[5:15, 20:30] = 0 # draw a dark square
        text_img = Image.fromarray(np_img)
        self.assertFalse(is_cell_visually_blank(text_img))

if __name__ == "__main__":
    unittest.main()
