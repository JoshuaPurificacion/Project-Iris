import mss
import cv2
import numpy as np
import os

def capture_screen():
    # Ensure captures directory exists
    os.makedirs('captures', exist_ok=True)
    
    with mss.mss() as sct:
        # Get information of monitor 1 (monitor 0 is all monitors combined)
        monitor = sct.monitors[1]
        
        # Capture the screen
        sct_img = sct.grab(monitor)
        
        # Convert to numpy array
        # mss returns image as BGRA, which OpenCV also uses natively
        img = np.array(sct_img)
        
        # Save the image using OpenCV
        filepath = os.path.join('captures', 'test_screen.png')
        cv2.imwrite(filepath, img)
        print(f"Screenshot saved to {filepath}")

if __name__ == '__main__':
    capture_screen()
