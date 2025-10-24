# =========================
# File: modules/led.py
# =========================
import tkinter as tk
from tkinter import ttk, colorchooser

class LedTab(ttk.Frame):
    def __init__(self, master, ble, *_, **__):
        super().__init__(master, padding=12, style="Card.TFrame")
        self.ble = ble

        # --- Brightness ---
        ttk.Label(self, text="Brightness (0–255)", style="Lbl.TLabel").grid(row=0, column=0, sticky="w")
        self.br_var = tk.IntVar(value=128)
        ttk.Scale(
            self, from_=0, to=255, orient="horizontal",
            variable=self.br_var
        ).grid(row=0, column=1, sticky="we", padx=8)
        ttk.Button(self, text="Set", command=self._send_bright).grid(row=0, column=2, padx=4)

        # --- Color ---
        ttk.Label(self, text="Color", style="Lbl.TLabel").grid(row=1, column=0, sticky="w", pady=(8,0))
        self.color_preview = tk.Canvas(self, width=40, height=24, bg="#ff0000")
        self.color_preview.grid(row=1, column=1, sticky="w", padx=8, pady=(8,0))
        ttk.Button(self, text="Pick…", command=self._pick_color).grid(row=1, column=2, padx=4, pady=(8,0))
        ttk.Button(self, text="Off", command=self._turn_off).grid(row=1, column=3, padx=4, pady=(8,0))

        # --- Apply both ---
        ttk.Button(self, text="Apply Brightness + Color", command=self._apply_bright_and_color)\
            .grid(row=2, column=0, columnspan=2, sticky="w", pady=(10,0))

        # --- Rainbow toggle ---
        self.rainbow = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="Rainbow animation", variable=self.rainbow,
                        command=self._toggle_rainbow).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10,0))

        for c in range(4):
            self.columnconfigure(c, weight=1)

    # ------------ helpers ------------
    def _current_rgb(self):
        """Return (r,g,b) ints from preview canvas bg."""
        hx = self.color_preview["bg"].lstrip("#")
        if len(hx) == 6:
            r = int(hx[0:2], 16)
            g = int(hx[2:4], 16)
            b = int(hx[4:6], 16)
            return r, g, b
        return 255, 0, 0

    # ------------ BLE send wrappers ------------
    def _send_bright(self):
        b = int(self.br_var.get())
        self.ble.write_line(f"BRIGHT {b}")

    def _send_rgb(self, r, g, b):
        self.ble.write_line(f"RGB {r} {g} {b}")

    def _apply_bright_and_color(self):
        """Send BRIGHT then RGB so device always reflects both."""
        b = int(self.br_var.get())
        r, g, c = self._current_rgb()
        # Order matters: set brightness first, then the solid color
        self.ble.write_line(f"BRIGHT {b}")
        self.ble.write_line(f"RGB {r} {g} {c}")
        # Also ensure rainbow is off on the device
        if self.rainbow.get():
            self.rainbow.set(False)
            self.ble.write_line("RAINBOW OFF")

    # ------------ UI actions ------------
    def _pick_color(self):
        color = colorchooser.askcolor(color=self.color_preview['bg'])[0]
        if color:
            r, g, b = [int(v) for v in color]
            self.color_preview.configure(bg=f"#{r:02x}{g:02x}{b:02x}")
            # Send brightness + color together to reflect slider immediately
            self._apply_bright_and_color()

    def _turn_off(self):
        # Off = brightness respected + color 0,0,0
        b = int(self.br_var.get())
        self.ble.write_line(f"BRIGHT {b}")
        self.ble.write_line("RGB 0 0 0")
        if self.rainbow.get():
            self.rainbow.set(False)
            self.ble.write_line("RAINBOW OFF")

    def _toggle_rainbow(self):
        self.ble.write_line("RAINBOW ON" if self.rainbow.get() else "RAINBOW OFF")
