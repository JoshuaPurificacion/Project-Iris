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

        # Display label directly in window
        self.label = tk.Label(self.window, image=self.images["idle"], bg="black", bd=0)
        self.label.pack()

        # ── Emoji float canvas (overlaid on label, same size as avatar) ───────
        self.canvas = tk.Canvas(
            self.window,
            width=AVATAR_SIZE[0],
            height=AVATAR_SIZE[1],
            bg="black",
            highlightthickness=0
        )
        # Place directly on top of the avatar label — pixel-perfect
        self.canvas.place(x=0, y=0)
        # Make the canvas transparent so it doesn't block the avatar image
        self.window.attributes("-transparentcolor", "black")
        # Prevent the canvas from eating mouse events meant for the label below
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        # ── Audio wave indicator (5 vertical bars, right edge of avatar) ──────
        self.wave_canvas = tk.Canvas(
            self.window,
            width=30,
            height=80,
            bg="#1a1a2e",
            highlightthickness=0
        )
        self.wave_canvas.place(x=390, y=200)
        self.wave_bars = []
        bar_x = 4
        for i in range(5):
            bar = self.wave_canvas.create_rectangle(
                bar_x, 80, bar_x + 3, 80,
                fill="#00ff88", outline=""
            )
            self.wave_bars.append(bar)
            bar_x += 6

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
            font=("Arial Rounded MT Bold", 24),
            fg="white",
            bg="#1a1a2e",
            wraplength=380,
            justify="center",
            padx=10,
            pady=6
        )
        self.caption_label.pack(side="bottom", fill="x")

        self.status_label = tk.Label(
            self.window,
            text="",
            font=("Arial Rounded MT Bold", 11),
            fg="#00ff88",        # bright green
            bg="#1a1a2e",
            padx=8,
            pady=3
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
        """Display text in the caption bar."""
        self._caption_time = time.time()
        self.window.after(0, lambda: self.caption_label.config(text=text))

    def clear_caption(self):
        """Clear text in the caption bar."""
        self.window.after(0, lambda: self.caption_label.config(text=""))

    def set_status(self, text, color="#00ff88"):
        self.window.after(0, lambda: self.status_label.config(
            text=text, fg=color
        ))

    def show_listening(self):
        self.set_status("🎤 Listening...", "#00ff88")   # green

    def show_thinking(self):
        self.set_status("💭 Thinking...", "#ffaa00")    # amber

    def show_speaking(self):
        self.set_status("🔊 Speaking...", "#4fc3f7")    # blue

    def show_idle(self):
        self.set_status("", "#ffffff")                  # clear

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
        self.window.after(80,  lambda: self.window.geometry(f"+{self.base_x}+{self.base_y + 6}"))
        self.window.after(140, lambda: self.window.geometry(f"+{self.base_x}+{self.base_y}"))

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
        Periodic blink animation. Closes eyes for 150ms without jumping.
        """
        while True:
            # Wait 4-7 seconds between blinks
            time.sleep(random.uniform(4.0, 7.0))

            # Only blink while idle — don't interrupt other states
            if self.current_state != "idle":
                continue

            # Close eyes
            self.window.after(0, lambda: self.label.configure(image=self.images["thinking"]) if self.images.get("thinking") else None)
            
            # Eyes open after 150 ms
            time.sleep(0.15)

            # Reopen eyes — only if still idle
            if self.current_state == "idle":
                self.window.after(0, lambda: self.label.configure(image=self.images["idle"]) if self.images.get("idle") else None)

    # ------------------------------------------------------------------ #
    #  Floating emoji system                                               #
    # ------------------------------------------------------------------ #

    def float_emoji(self, emoji, color="#ffffff"):
        """Spawn a floating emoji that drifts upward and fades out."""
        try:
            x = random.randint(150, 280)
            y = 60  # above Iris's head
            item = self.canvas.create_text(
                x, y,
                text=emoji,
                font=("Segoe UI Emoji", 22),
                fill=color
            )
            # Keep emojis above all other canvas items
            self.canvas.tag_raise(item)
            self._animate_float(item, y, 0)
        except Exception as e:
            print(f"[AvatarWindow] float_emoji error: {e}")

    def _animate_float(self, item, y, step):
        """Animate emoji floating upward and fading out over 20 frames."""
        if step >= 20:
            try:
                self.canvas.delete(item)
            except Exception:
                pass
            return
        try:
            self.canvas.move(item, 0, -3)
        except Exception:
            return
        self.window.after(60, lambda: self._animate_float(item, y - 3 * step, step + 1))

    # ------------------------------------------------------------------ #
    #  Audio wave indicator                                                #
    # ------------------------------------------------------------------ #

    def update_wave(self, volume):
        """Update audio bars based on RMS volume (0.0 to 1.0)."""
        if not self.wave_bars:
            return
        try:
            for i, bar in enumerate(self.wave_bars):
                noise = random.uniform(0.7, 1.0)
                height = int(volume * 70 * noise)
                height = max(2, min(70, height))
                self.wave_canvas.coords(bar, i * 6 + 2, 80 - height, i * 6 + 5, 80)
        except Exception as e:
            print(f"[AvatarWindow] update_wave error: {e}")

    def set_wave_color(self, color):
        """Set the fill color of all wave bars."""
        try:
            for bar in self.wave_bars:
                self.wave_canvas.itemconfig(bar, fill=color)
        except Exception as e:
            print(f"[AvatarWindow] set_wave_color error: {e}")

    # ------------------------------------------------------------------ #
    #  Entry point (blocks on main thread)                                 #
    # ------------------------------------------------------------------ #

    def run(self):
        """Start the tkinter event loop. Must be called from the main thread."""
        self.window.mainloop()
