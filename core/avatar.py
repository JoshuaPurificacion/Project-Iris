import tkinter as tk
from PIL import Image, ImageTk
import threading
import time
import random

from core.logger import log_system

SCALE_FACTOR = 1.5
AVATAR_SIZE = (int(400 * SCALE_FACTOR), int(531 * SCALE_FACTOR))  # 1.5x larger: 600x797
EMOJI_SIZE = int(24 * SCALE_FACTOR)  # 1.5x larger emoji: 36

STATES = {
    "idle": "assets/avatar/open_eyes_mouth_close.png",
    "speaking": "assets/avatar/open_eyes_mouth_open.png",
    "thinking": "assets/avatar/close_eyes_mouth_close.png",
    "surprised": "assets/avatar/close_eyes_mouth_open.png",
}


class PrototypeWindow:
    def __init__(self, parent_window):
        self.window = tk.Toplevel(parent_window)
        self.window.withdraw()
        self.window.configure(bg="#1a1a2e")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)

        self.label = tk.Label(self.window, bg="#1a1a2e")
        self.label.pack()

        self.current_image = None

    def show(self, image_path, target_height, anchor_y):
        try:
            img = Image.open(image_path)
            aspect_ratio = img.width / img.height
            new_width = int(target_height * aspect_ratio)
            img = img.resize((new_width, target_height), Image.LANCZOS)
            self.current_image = ImageTk.PhotoImage(img)
            self.label.configure(image=self.current_image)

            self.window.update_idletasks()

            # --- THE "LEFT ANCHOR" MATH ---
            padding_left = 50  # 50 pixels from the left edge of the monitor

            self.window.geometry(
                f"{new_width}x{target_height}+{padding_left}+{anchor_y}"
            )

            self.window.deiconify()
        except Exception as e:
            print(f"[PrototypeWindow] Could not load image {image_path}: {e}")

    def hide(self):
        self.window.withdraw()
        self.current_image = None

    def is_visible(self):
        return self.window.winfo_viewable()


class AvatarWindow:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("Iris")
        self.window.configure(bg="black")

        # Window chrome
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-transparentcolor", "black")

        # Load all state images
        self.images = {}
        for state, path in STATES.items():
            try:
                img = (
                    Image.open(path).convert("RGBA").resize(AVATAR_SIZE, Image.LANCZOS)
                )
                self.images[state] = ImageTk.PhotoImage(img)
            except Exception as e:
                print(f"[AvatarWindow] Could not load image for state '{state}': {e}")
                self.images[state] = None

        # Display label directly in window
        self.label = tk.Label(self.window, image=self.images["idle"], bg="black", bd=0)
        self.label.pack()

        # ── Text input bar ────────────────────────────────────────────────
        self.input_frame = tk.Frame(self.window, bg="#2a2a3e")
        self.input_entry = tk.Entry(
            self.input_frame,
            font=("Segoe UI", int(10 * SCALE_FACTOR)),
            bg="#2a2a3e",
            fg="white",
            insertbackground="white",
            relief="flat",
            width=28,
        )
        self.input_entry.pack(side="left", padx=6, pady=4)
        self.send_button = tk.Button(
            self.input_frame,
            text="➤",
            font=("Segoe UI", int(10 * SCALE_FACTOR)),
            bg="#e63946",
            fg="white",
            relief="flat",
            command=self._on_send,
        )
        self.input_entry.pack(side="left", padx=6, pady=4)
        self.send_button = tk.Button(
            self.input_frame,
            text="➤",
            font=("Segoe UI", int(12 * SCALE_FACTOR)),
            bg="#e63946",
            fg="white",
            relief="flat",
            command=self._on_send,
        )
        self.send_button.pack(side="right", padx=4)
        self.input_frame.pack(side="bottom", fill="x")
        self.input_entry.bind("<Return>", lambda e: self._on_send())
        self.on_text_input = None  # wired from wake_up.py

        # ── Caption bar ───────────────────────────────────────────────────
        self.caption_label = tk.Label(
            self.window,
            text="",
            font=("Segoe UI", int(16 * SCALE_FACTOR)),
            fg="white",
            bg="#1a1a2e",
            wraplength=int(380 * SCALE_FACTOR),
            justify="center",
            padx=int(10 * SCALE_FACTOR),
            pady=int(6 * SCALE_FACTOR),
        )
        self.caption_label.pack(side="bottom", fill="x")

        self.status_label = tk.Label(
            self.window,
            text="",
            font=("Segoe UI", int(9 * SCALE_FACTOR)),
            fg="#00ff88",  # bright green
            bg="#1a1a2e",
            padx=int(8 * SCALE_FACTOR),
            pady=int(3 * SCALE_FACTOR),
        )
        self.status_label.pack(side="bottom", fill="x")

        # Position bottom-right
        self.window.update_idletasks()
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        win_w = self.window.winfo_reqwidth()
        win_h = self.window.winfo_reqheight()
        margin = 20
        self.base_x = screen_w - win_w - margin
        self.base_y = screen_h - win_h - margin - 40  # above taskbar
        # Lock size so place() doesn't collapse the window
        self.window.geometry(f"{win_w}x{win_h}+{self.base_x}+{self.base_y}")

        # Prototype window (hologram display) - create before positioning
        self.prototype_window = PrototypeWindow(self.window)

        # --- DETERMINISTIC IMAGE WATCHDOG ---
        self.prototype_images = {
            "car_parking": "assets/prototype_images/car_parking.jpg",
            "smart_light_saving": "assets/prototype_images/smart_light_saving.jpg",
            "flood_management": "assets/prototype_images/flood_management.jpg",
            "rainwater_harvesting": "assets/prototype_images/rainwater_harvesting.jpg",
            "smart_lock": "assets/prototype_images/smart_lock.jpg",
            "omnisense": "assets/prototype_images/omnisense.jpg",
            "smart_ergonomic_backpack": "assets/prototype_images/smart_ergonomic_backpack.jpg",
        }

        self.project_keywords = {
            "omnisense": [
                "omnisense",
                "pet feeder",
                "food dispenser",
                "canny",
                "edge detection",
            ],
            "car_parking": ["car parking", "street light", "parking detection"],
            "smart_light_saving": ["smart light", "energy saving", "occupancy"],
            "flood_management": ["flood", "water level", "floodgate"],
            "rainwater_harvesting": [
                "rainwater",
                "harvesting",
                "soil moisture",
                "irrigation",
            ],
            "smart_lock": ["smart lock", "vault", "security", "three verification"],
            "smart_ergonomic_backpack": [
                "backpack",
                "ergonomic",
                "load sensing",
                "weight alert",
                "overweight",
            ],
        }

        self.current_state = "idle"
        self._caption_time = 0.0

        # Drag state
        self._press_time = 0.0
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._dragging = False

        # Mouse bindings
        self.label.bind("<ButtonPress-1>", self.on_press)
        self.label.bind("<B1-Motion>", self.on_drag)
        self.label.bind("<ButtonRelease-1>", self.on_release)

    # ------------------------------------------------------------------ #
    #  Caption + text input                                                #
    # ------------------------------------------------------------------ #

    def show_caption(self, text):
        """Display text in the caption bar and check for image triggers."""
        log_system(f"[WATCHDOG] Received: {text[:60]}...")
        self._caption_time = time.time()
        self.window.after(0, lambda: self.caption_label.config(text=text))

        # --- THE WATCHDOG INTERCEPT ---
        lower_text = text.lower()
        for image_key, keywords in self.project_keywords.items():
            if any(kw in lower_text for kw in keywords):
                log_system(f"[WATCHDOG] TRIGGER MATCH! Opening '{image_key}'")
                self.show_prototype(image_key)
                break

    def clear_caption(self):
        """Clear text in the caption bar."""
        self.window.after(0, lambda: self.caption_label.config(text=""))

    def set_status(self, text, color="#00ff88"):
        self.window.after(0, lambda: self.status_label.config(text=text, fg=color))

    def show_listening(self):
        self.set_status("Listening...", "#00ff88")  # green

    def show_thinking(self):
        self.set_status("Thinking...", "#ffaa00")  # amber

    def show_speaking(self):
        self.set_status("Speaking...", "#4fc3f7")  # blue

    def show_idle(self):
        self.set_status("", "#ffffff")  # clear

    # ------------------------------------------------------------------ #
    #  Prototype (hologram) display                                       #
    # ------------------------------------------------------------------ #
    def show_prototype(self, image_key, image_paths=None):
        """Thread-safe call to show a prototype image."""
        self.window.after(0, lambda: self._apply_show_prototype(image_key, image_paths))

    def _apply_show_prototype(self, image_key, image_paths):
        """Internal method executed on the main UI thread."""
        paths = image_paths if image_paths else self.prototype_images
        if image_key not in paths:
            return
        image_path = paths[image_key]
        target_height = AVATAR_SIZE[1]

        # Pass Iris's base_y so the image sits level with her
        self.prototype_window.show(image_path, target_height, self.base_y)

    def hide_prototype(self):
        """Thread-safe call to hide the prototype window."""
        self.window.after(0, self.prototype_window.hide)

    def _on_send(self):
        """Called when the user presses Enter or the send button."""
        text = self.input_entry.get().strip()
        if text and self.on_text_input:
            self.input_entry.delete(0, tk.END)
            self.on_text_input(text)

    # ------------------------------------------------------------------ #
    #  State management                                                    #
    # ------------------------------------------------------------------ #

    def set_state(self, state, animated=True):
        """Thread-safe state change — schedules onto the tkinter main loop."""
        self.current_state = state
        self.window.after(0, lambda: self._apply_state(state, animated))

    def _apply_state(self, state, animated=True):
        if animated:
            self.jump()
        if self.images.get(state):
            self.label.configure(image=self.images[state])

        if state == "speaking":
            self.shake_loop()
        else:
            self.label.place(x=0, y=0)

        if state == "idle" and (time.time() - self._caption_time) > 3.0:
            self.clear_caption()

    # ------------------------------------------------------------------ #
    #  Animation helpers                                                   #
    # ------------------------------------------------------------------ #

    def jump(self):
        """Quick 3-frame jump: up → slightly past base → settle."""
        self.window.geometry(f"+{self.base_x}+{self.base_y - 18}")
        self.window.after(
            80, lambda: self.window.geometry(f"+{self.base_x}+{self.base_y + 6}")
        )
        self.window.after(
            140, lambda: self.window.geometry(f"+{self.base_x}+{self.base_y}")
        )

    def shake_loop(self):
        """20 fps positional jitter during speaking state."""
        if self.current_state != "speaking":
            # Stop shaking; reset to original relative position
            self.label.place(x=0, y=0)
            return
        x_offset = random.randint(-4, 4)
        y_offset = random.randint(-4, 4)
        # Directly offset label inside window using placed offsets
        self.label.place(x=x_offset, y=y_offset)
        self.window.after(50, self.shake_loop)

    def bob(self):
        """Quick double-bounce animation on click."""
        self.window.geometry(f"+{self.base_x}+{self.base_y - 12}")
        self.window.after(
            80, lambda: self.window.geometry(f"+{self.base_x}+{self.base_y + 8}")
        )
        self.window.after(
            160, lambda: self.window.geometry(f"+{self.base_x}+{self.base_y - 5}")
        )
        self.window.after(
            220, lambda: self.window.geometry(f"+{self.base_x}+{self.base_y}")
        )

    # ------------------------------------------------------------------ #
    #  Interaction handlers                                                #
    # ------------------------------------------------------------------ #

    def on_press(self, event):
        """Record press time and cursor offset for drag detection."""
        self._press_time = time.time()
        self._drag_offset_x = event.x
        self._drag_offset_y = event.y
        self._dragging = False

    def on_drag(self, event):
        """Enable dragging after a 2-second hold; update base position while dragging."""
        held = time.time() - self._press_time
        if held >= 2.0:
            self._dragging = True
            new_x = self.window.winfo_x() + event.x - self._drag_offset_x
            new_y = self.window.winfo_y() + event.y - self._drag_offset_y
            self.window.geometry(f"+{new_x}+{new_y}")
            self.base_x = new_x
            self.base_y = new_y

    def on_release(self, event):
        """Bob on a short click; reset drag flag on release."""
        if not self._dragging:
            self.bob()
        self._dragging = False

    def blink_loop(self):
        """
        Periodic blink animation. Closes eyes for 150ms without jumping.
        """
        while True:
            # Wait 4-7 seconds between blinks
            time.sleep(random.uniform(4.0, 7.0))

            # Only blink while idle — don't interrupt other states
            if self.current_state != "idle":
                continue

            # Close eyes
            self.window.after(
                0,
                lambda: (
                    self.label.configure(image=self.images["thinking"])
                    if self.images.get("thinking")
                    else None
                ),
            )

            # Eyes open after 150 ms
            time.sleep(0.15)

            # Reopen eyes — only if still idle
            if self.current_state == "idle":
                self.window.after(
                    0,
                    lambda: (
                        self.label.configure(image=self.images["idle"])
                        if self.images.get("idle")
                        else None
                    ),
                )

    def float_emoji(self, emoji, color="#ffff00", duration=2000):
        """Display an emoji briefly that disappears after duration."""
        window_width = self.window.winfo_width()
        window_height = self.window.winfo_height()

        label = tk.Label(
            self.window, text=emoji, font=("Segoe UI", EMOJI_SIZE), fg=color, bg="black"
        )
        label.place(x=window_width // 2 - 20, y=window_height // 2 - 80)

        # Remove after duration
        self.window.after(duration, label.destroy)

    # ------------------------------------------------------------------ #
    #  Entry point (blocks on main thread)                                 #
    # ------------------------------------------------------------------ #

    def run(self):
        """Start the tkinter event loop. Must be called from the main thread."""
        self.window.mainloop()
