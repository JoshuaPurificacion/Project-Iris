import tkinter as tk
from PIL import Image, ImageTk
import threading
import time
import random

AVATAR_SIZE = (400, 531)  # Displayed size in pixels — preserves 1792x2380 aspect ratio

STATES = {
    "idle":      "assets/avatar/open_eyes_mouth_close.png",
    "speaking":  "assets/avatar/open_eyes_mouth_open.png",
    "thinking":  "assets/avatar/close_eyes_mouth_close.png",
    "surprised": "assets/avatar/close_eyes_mouth_open.png",
}

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
                img = Image.open(path).convert("RGBA").resize(AVATAR_SIZE, Image.LANCZOS)
                self.images[state] = ImageTk.PhotoImage(img)
            except Exception as e:
                print(f"[AvatarWindow] Could not load image for state '{state}': {e}")
                self.images[state] = None

        # Display label
        self.label = tk.Label(self.window, image=self.images["idle"], bg="black", bd=0)
        self.label.pack()

        # ── Text input bar ────────────────────────────────────────────────
        self.input_frame = tk.Frame(self.window, bg="#2a2a3e")
        self.input_entry = tk.Entry(
            self.input_frame,
            font=("Arial", 12),
            bg="#2a2a3e",
            fg="white",
            insertbackground="white",
            relief="flat",
            width=28
        )
        self.input_entry.pack(side="left", padx=6, pady=4)
        self.send_button = tk.Button(
            self.input_frame,
            text="➤",
            font=("Arial", 12),
            bg="#e63946",
            fg="white",
            relief="flat",
            command=self._on_send
        )
        self.send_button.pack(side="right", padx=4)
        self.input_frame.pack(side="bottom", fill="x")
        self.input_entry.bind("<Return>", lambda e: self._on_send())
        self.on_text_input = None   # wired from wake_up.py

        # ── Caption bar ───────────────────────────────────────────────────
        self.caption_label = tk.Label(
            self.window,
            text="",
            font=("Arial Rounded MT Bold", 14),
            fg="white",
            bg="#1a1a2e",
            wraplength=380,
            justify="center",
            padx=10,
            pady=6
        )
        self.caption_label.pack(side="bottom", fill="x")

        # Position bottom-right
        self.window.update_idletasks()
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        win_w = self.window.winfo_reqwidth()
        win_h = self.window.winfo_reqheight()
        margin = 20
        self.base_x = screen_w - win_w - margin
        self.base_y = screen_h - win_h - margin - 40  # above taskbar
        self.window.geometry(f"+{self.base_x}+{self.base_y}")

        self.current_state = "idle"

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
        """Display text in the caption bar, then auto-clear after 4 seconds."""
        self.window.after(0, lambda: self.caption_label.config(text=text))
        self.window.after(4000, lambda: self.caption_label.config(text=""))

    def _on_send(self):
        """Called when the user presses Enter or the send button."""
        text = self.input_entry.get().strip()
        if text and self.on_text_input:
            self.input_entry.delete(0, tk.END)
            self.on_text_input(text)

    # ------------------------------------------------------------------ #
    #  State management                                                    #
    # ------------------------------------------------------------------ #

    def set_state(self, state):
        """Thread-safe state change — schedules onto the tkinter main loop."""
        self.current_state = state
        self.window.after(0, lambda: self._apply_state(state))

    def _apply_state(self, state):
        self.jump()
        if self.images.get(state):
            self.label.configure(image=self.images[state])
        if state == "speaking":
            self.shake_loop()
        else:
            self.window.geometry(f"+{self.base_x}+{self.base_y}")

    # ------------------------------------------------------------------ #
    #  Animation helpers                                                   #
    # ------------------------------------------------------------------ #

    def jump(self):
        """Quick 3-frame jump: up → slightly past base → settle."""
        self.window.geometry(f"+{self.base_x}+{self.base_y - 18}")
        self.window.after(80,  lambda: self.window.geometry(f"+{self.base_x}+{self.base_y + 6}"))
        self.window.after(140, lambda: self.window.geometry(f"+{self.base_x}+{self.base_y}"))

    def shake_loop(self):
        """20 fps positional jitter during speaking state."""
        if self.current_state != "speaking":
            # Stop shaking; reset to base position
            self.window.geometry(f"+{self.base_x}+{self.base_y}")
            return
        x_offset = random.randint(-4, 4)
        y_offset = random.randint(-4, 4)
        self.window.geometry(f"+{self.base_x + x_offset}+{self.base_y + y_offset}")
        self.window.after(50, self.shake_loop)

    def bob(self):
        """Quick double-bounce animation on click."""
        self.window.geometry(f"+{self.base_x}+{self.base_y - 12}")
        self.window.after(80,  lambda: self.window.geometry(f"+{self.base_x}+{self.base_y + 8}"))
        self.window.after(160, lambda: self.window.geometry(f"+{self.base_x}+{self.base_y - 5}"))
        self.window.after(220, lambda: self.window.geometry(f"+{self.base_x}+{self.base_y}"))

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
        Periodic blink animation. Runs forever in a daemon thread.
        Closes eyes (thinking state image) then opens them again with head-bob jumps.
        """
        while True:
            # Wait 4-7 seconds between blinks
            time.sleep(random.uniform(4.0, 7.0))

            # Only blink while idle — don't interrupt other states
            if self.current_state != "idle":
                continue

            # Eyes close — schedule on tkinter thread, then trigger a jump on close
            def _close_eyes():
                if self.current_state != "idle":
                    return
                if self.images.get("thinking"):
                    self.label.configure(image=self.images["thinking"])
                self.jump()

            self.window.after(0, _close_eyes)

            # Eyes open after 150 ms with another jump
            time.sleep(0.15)

            def _open_eyes():
                if self.current_state != "idle":
                    return
                if self.images.get("idle"):
                    self.label.configure(image=self.images["idle"])
                self.jump()

            self.window.after(0, _open_eyes)

    # ------------------------------------------------------------------ #
    #  Entry point (blocks on main thread)                                 #
    # ------------------------------------------------------------------ #

    def run(self):
        """Start the tkinter event loop. Must be called from the main thread."""
        self.window.mainloop()
