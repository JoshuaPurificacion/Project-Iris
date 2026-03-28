"""
ui/game_window.py

Tkinter game panel for Project Iris RPG.
Appears center-screen, ~620x460px.
Shows: floor, room, HP bars, weapon/inventory, scrolling event log.
All updates are thread-safe via window.after().
"""

import tkinter as tk
from tkinter import font as tkfont

# ── Color palette ─────────────────────────────────────────────
BG_DARK      = "#0d0d1a"
BG_PANEL     = "#1a1a2e"
BG_CARD      = "#16213e"
ACCENT_RED   = "#e63946"
ACCENT_BLUE  = "#4fc3f7"
ACCENT_GREEN = "#00ff88"
ACCENT_GOLD  = "#ffd700"
ACCENT_GRAY  = "#8888aa"
TEXT_WHITE   = "#f0f0ff"
TEXT_DIM     = "#9999bb"
HP_GREEN     = "#2ecc71"
HP_YELLOW    = "#f1c40f"
HP_RED       = "#e74c3c"


def _hp_color(hp, max_hp):
    ratio = hp / max_hp if max_hp > 0 else 0
    if ratio > 0.5:
        return HP_GREEN
    elif ratio > 0.25:
        return HP_YELLOW
    return HP_RED


class GameWindow:
    def __init__(self, parent):
        self.window = tk.Toplevel(parent)
        self.window.title("Iris's Adventure")
        self.window.configure(bg=BG_DARK)
        self.window.resizable(False, False)
        self.window.attributes("-topmost", True)

        W, H = 620, 460
        self.window.geometry(f"{W}x{H}")
        self._center(W, H)
        self.window.withdraw()   # hidden until game starts

        self._build_ui()

    def _center(self, w, h):
        self.window.update_idletasks()
        sw = self.window.winfo_screenwidth()
        sh = self.window.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.window.geometry(f"{w}x{h}+{x}+{y}")

    # ── UI Construction ───────────────────────────────────────

    def _build_ui(self):
        # ── Title bar ─────────────────────────────────────────
        self.title_var = tk.StringVar(value="🏰  Iris's Adventure — Floor 1")
        title_lbl = tk.Label(
            self.window, textvariable=self.title_var,
            font=("Segoe UI", 13, "bold"),
            fg=ACCENT_GOLD, bg=BG_DARK, pady=6
        )
        title_lbl.pack(fill="x")

        # ── Room description ──────────────────────────────────
        self.room_var = tk.StringVar(value="...")
        room_lbl = tk.Label(
            self.window, textvariable=self.room_var,
            font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_DARK,
            wraplength=580, justify="center", pady=2
        )
        room_lbl.pack(fill="x")

        tk.Frame(self.window, bg=ACCENT_GRAY, height=1).pack(fill="x", padx=10, pady=4)

        # ── Combat row ────────────────────────────────────────
        combat_frame = tk.Frame(self.window, bg=BG_DARK)
        combat_frame.pack(fill="x", padx=10)

        # Player card
        self.player_frame = self._make_card(combat_frame, side="left")
        # Enemy card
        self.enemy_frame  = self._make_card(combat_frame, side="right")

        # Player internals
        self.player_name_lbl = tk.Label(
            self.player_frame, text="🧝 IRIS",
            font=("Segoe UI", 11, "bold"), fg=ACCENT_BLUE, bg=BG_CARD
        )
        self.player_name_lbl.pack(anchor="w", padx=8, pady=(6, 2))

        self.player_hp_bar  = self._make_hp_bar(self.player_frame)
        self.player_hp_lbl  = self._make_label(self.player_frame, "HP: —", TEXT_WHITE)
        self.player_wpn_lbl = self._make_label(self.player_frame, "⚔  —", ACCENT_GOLD)
        self.player_inv_lbl = self._make_label(self.player_frame, "🎒  —", ACCENT_GREEN)
        self.player_gld_lbl = self._make_label(self.player_frame, "💰  0 gold", ACCENT_GOLD)

        # Enemy internals
        self.enemy_name_lbl = tk.Label(
            self.enemy_frame, text="👹 ???",
            font=("Segoe UI", 11, "bold"), fg=ACCENT_RED, bg=BG_CARD
        )
        self.enemy_name_lbl.pack(anchor="w", padx=8, pady=(6, 2))

        self.enemy_hp_bar = self._make_hp_bar(self.enemy_frame)
        self.enemy_hp_lbl = self._make_label(self.enemy_frame, "HP: —", TEXT_WHITE)
        self.enemy_atk_lbl = self._make_label(self.enemy_frame, "⚔  ATK: —", ACCENT_RED)

        tk.Frame(self.window, bg=ACCENT_GRAY, height=1).pack(fill="x", padx=10, pady=4)

        # ── Event log ─────────────────────────────────────────
        log_header = tk.Label(
            self.window, text="📜  Event Log",
            font=("Segoe UI", 9, "bold"), fg=TEXT_DIM, bg=BG_DARK, anchor="w"
        )
        log_header.pack(fill="x", padx=14)

        log_frame = tk.Frame(self.window, bg=BG_PANEL)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(2, 8))

        self.log_text = tk.Text(
            log_frame,
            font=("Consolas", 9),
            bg=BG_PANEL, fg=TEXT_WHITE,
            relief="flat", bd=0,
            state="disabled",
            height=8,
            wrap="word",
            cursor="arrow",
        )
        self.log_text.pack(fill="both", expand=True, padx=6, pady=4)

        # Tag colours for log entries
        self.log_text.tag_config("crit",    foreground=ACCENT_GOLD)
        self.log_text.tag_config("death",   foreground=ACCENT_RED)
        self.log_text.tag_config("loot",    foreground=ACCENT_GREEN)
        self.log_text.tag_config("system",  foreground=TEXT_DIM)
        self.log_text.tag_config("normal",  foreground=TEXT_WHITE)

        # ── Status bar ────────────────────────────────────────
        self.status_var = tk.StringVar(value="Waiting for Iris...")
        status_lbl = tk.Label(
            self.window, textvariable=self.status_var,
            font=("Segoe UI", 8), fg=ACCENT_GRAY, bg=BG_DARK, pady=3
        )
        status_lbl.pack(fill="x")

    def _make_card(self, parent, side):
        frame = tk.Frame(parent, bg=BG_CARD, relief="flat", bd=0)
        frame.pack(side=side, fill="both", expand=True, padx=(0, 4) if side == "left" else (4, 0))
        return frame

    def _make_hp_bar(self, parent):
        canvas = tk.Canvas(parent, height=12, bg=BG_CARD, highlightthickness=0)
        canvas.pack(fill="x", padx=8, pady=2)
        return canvas

    def _make_label(self, parent, text, color):
        lbl = tk.Label(
            parent, text=text,
            font=("Segoe UI", 9), fg=color, bg=BG_CARD, anchor="w"
        )
        lbl.pack(fill="x", padx=8, pady=1)
        return lbl

    # ── HP bar drawing ────────────────────────────────────────

    def _draw_hp_bar(self, canvas, hp, max_hp):
        canvas.delete("all")
        w = canvas.winfo_width() or 260
        ratio = max(0.0, hp / max_hp) if max_hp > 0 else 0
        # Background
        canvas.create_rectangle(0, 0, w, 12, fill="#333355", outline="")
        # Fill
        fill_w = int(w * ratio)
        color = _hp_color(hp, max_hp)
        if fill_w > 0:
            canvas.create_rectangle(0, 0, fill_w, 12, fill=color, outline="")

    # ── Public refresh (thread-safe) ──────────────────────────

    def refresh(self, state: dict):
        """Called from the game loop thread — schedules UI update on main thread."""
        self.window.after(0, lambda: self._apply_state(state))

    def _apply_state(self, state: dict):
        floor  = state.get("floor", 1)
        room   = state.get("room_name", "???")
        status = state.get("status", "active")

        # Title
        status_tag = {"victory": "⚔ VICTORY!", "defeat": "💀 DEFEATED", "escaped": "🏃 ESCAPED"}.get(status, f"Floor {floor}")
        self.title_var.set(f"🏰  Iris's Adventure — {status_tag}")

        # Room
        self.room_var.set(state.get("room_desc", ""))

        # Player
        p_hp     = state["player_hp"]
        p_max    = state["player_max_hp"]
        wpn      = state["weapon_name"]
        wpn_atk  = state["weapon_atk"]
        wpn_frg  = state["weapon_forge"]
        potions  = state["potions"]
        gold     = state["player_gold"]

        self.player_hp_lbl.config(text=f"HP: {p_hp} / {p_max}")
        self._draw_hp_bar(self.player_hp_bar, p_hp, p_max)
        self.player_wpn_lbl.config(text=f"⚔  {wpn}  (ATK {wpn_atk}, forge {wpn_frg})")
        self.player_inv_lbl.config(text=f"🎒  {potions}")
        self.player_gld_lbl.config(text=f"💰  {gold} gold")

        # Enemy
        e_name = state.get("enemy_name") or "—"
        e_hp   = state.get("enemy_hp", 0)
        e_max  = state.get("enemy_max_hp", 1)
        e_atk  = state.get("enemy_atk", 0)

        self.enemy_name_lbl.config(text=f"👹 {e_name.upper()}")
        self.enemy_hp_lbl.config(text=f"HP: {e_hp} / {e_max}")
        self._draw_hp_bar(self.enemy_hp_bar, e_hp, e_max)
        self.enemy_atk_lbl.config(text=f"⚔  ATK: {e_atk}")

        # Status bar
        self.status_var.set(f"Rooms cleared: {state.get('rooms_cleared', 0)}  |  Gold: {gold}")

        # Log
        self._refresh_log(state.get("full_log", []))

    def _refresh_log(self, log_lines):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        for line in log_lines[-20:]:
            tag = "normal"
            ll = line.lower()
            if "critical" in ll or "crit" in ll:
                tag = "crit"
            elif "defeated" in ll or "fallen" in ll or "game over" in ll:
                tag = "death"
            elif "looted" in ll or "gained" in ll or "equipped" in ll or "found" in ll:
                tag = "loot"
            elif "enter" in ll or "descend" in ll or "floor" in ll:
                tag = "system"
            self.log_text.insert("end", f"> {line}\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def set_status(self, text):
        self.window.after(0, lambda: self.status_var.set(text))

    def show(self):
        self.window.after(0, self.window.deiconify)

    def hide(self):
        self.window.after(0, self.window.withdraw)
