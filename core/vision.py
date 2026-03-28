import os
import mss
import pytesseract
from PIL import Image

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def capture_screen(output_path="captures/test_screen.png"):
    """Capture a full-screen screenshot to a predictable path for OCR tools."""
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            sct_img = sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            img.save(output_path)
        return output_path
    except Exception as e:
        print(f"[Vision] Screen capture failed: {e}")
        return ""


def get_screen_text(max_chars=400):
    """Takes a lightning-fast screenshot and extracts text."""
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            sct_img = sct.grab(monitor)

            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

            img = img.resize((img.width // 2, img.height // 2))

            text = pytesseract.image_to_string(img)

            clean_text = " ".join(
                [line.strip() for line in text.split("\n") if line.strip()]
            )
            return clean_text[:max_chars]
    except Exception as e:
        print(f"[Vision] OCR failed: {e}")
        return ""
