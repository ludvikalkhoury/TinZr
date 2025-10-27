# =========================
# File: modules/battery.py
# =========================
import json
import tkinter as tk
from tkinter import ttk

REFRESH_INTERVAL_MS = 10 * 60 * 1000  # 10 minutes

def _clamp(v, lo, hi): return max(lo, min(hi, v))

class BatteryTab(ttk.Frame):
    """Battery icon + percent (stacked). Scales to the canvas size."""
    def __init__(self, master, ble, *, vmin=3.30, vmax=4.20,
                 icon_width=120, icon_height=48, **_):
        # Keep the card look; style sets background to SB_SURFACE via Card.TFrame
        super().__init__(master, padding=12, style="Card.TFrame")

        self.ble = ble

        # Configurable LiPo curve endpoints (single-cell)
        self.vmin = float(vmin)
        self.vmax = float(vmax)

        # State
        self.volts = 0.0
        self.percent = 0
        self._ema_v = None      # EMA of voltage for smoother UI
        self._ema_alpha = 0.3   # smoothing factor
        self._timer_id = None

        # ---------- UI ----------
        top = ttk.Frame(self, style="Card.TFrame")
        top.pack(fill="x")

        # Left column: icon + text stacked (text under icon)
        icon_col = ttk.Frame(top, style="Card.TFrame")
        icon_col.pack(side="left", anchor="w")

        # Use the same gray as the card for canvas bg
        bg_color = self._bg(icon_col)

        # Canvas (battery icon) — scalable
        self.canvas = tk.Canvas(
            icon_col, width=icon_width, height=icon_height,
            bg=bg_color, highlightthickness=0, bd=0
        )
        self.canvas.pack(side="top", padx=(0, 12))

        # Percent + volts under the icon
        self.lbl = ttk.Label(icon_col, text="0% (0.00 V)", style="Lbl.TLabel")
        self.lbl.pack(side="top", anchor="w", pady=(4, 0))

        # Right of the battery: clickable refresh symbol (NOT in the canvas)
        base_fg = "#2563eb"      # blue
        hover_fg = "#1d4ed8"     # darker blue

        # Vertical centering relative to the canvas height (roughly)
        pad_y = max(0, int(icon_height / 2 - 10))  # 10 ~ half of ~20px glyph height

        self.refresh_icon = ttk.Label(
            top,
            text="🔄",
            font=("Segoe UI Symbol", 12, "bold"),
            foreground=base_fg,
            background=bg_color,
        )
        # Place it to the RIGHT of the icon column
        self.refresh_icon.place(x=icon_width - 5, y=pad_y-2)

        self.refresh_icon.configure(cursor="hand2")
        self.refresh_icon.bind("<Button-1>", lambda e: self.refresh())
        self.refresh_icon.bind("<Enter>", lambda e: self.refresh_icon.configure(foreground=hover_fg))
        self.refresh_icon.bind("<Leave>", lambda e: self.refresh_icon.configure(foreground=base_fg))

        self._draw_icon(0)

        # Listen for BLE battery messages from anywhere in the app (virtual events)
        self.bind_all("<<BLE:bat>>",      self._on_bat_line_evt, add="+")     # raw line e.g. "BAT,3.87"
        self.bind_all("<<BLE:bat_val>>",  self._on_bat_val_evt,  add="+")     # dict e.g. {"volts":3.87}
        self.bind_all("<<BLE:notify>>",   self._on_notify_maybe_bat_evt, add="+")
        self.bind_all("<<BLE:connected>>", self._on_connected_evt, add="+")

        # First quick read, then periodic every 10 minutes
        self.after(600, self.refresh)
        self._schedule_periodic()

        # Clean up timers on destroy
        self.bind("<Destroy>", self._on_destroy, add="+")

    # ---------- Public direct-update helpers (called by app.py) ----------
    def handle_raw_bat(self, text: str):
        if not text: return
        s = text.strip()
        if s.startswith(("'", '"')) and s.endswith(("'", '"')):
            s = s[1:-1]
        v = self._parse_voltage_from_line(s)
        if v is not None:
            self._update_voltage(v)

    def handle_bat_val(self, obj_str: str):
        if not obj_str: return
        s = str(obj_str)
        try:
            try:
                obj = json.loads(s)
            except Exception:
                obj = json.loads(s.replace("'", '"'))
            v = float(obj.get("volts"))
        except Exception:
            return
        self._update_voltage(v)

    # ---------- Timer plumbing ----------
    def _schedule_periodic(self):
        if self._timer_id is not None:
            try: self.after_cancel(self._timer_id)
            except Exception: pass
        self._timer_id = self.after(REFRESH_INTERVAL_MS, self._tick)

    def _tick(self):
        self.refresh()
        self._schedule_periodic()

    def _on_destroy(self, _e):
        if self._timer_id is not None:
            try: self.after_cancel(self._timer_id)
            except Exception: pass
            self._timer_id = None

    # ---------- BLE interaction ----------
    def refresh(self):
        self.ble.write_line("READ_BAT", require_response=False)

    # ---------- Virtual-event handlers ----------
    def _on_connected_evt(self, evt):
        try:
            is_on = str(getattr(evt, "data", "")).strip().lower() in ("true", "1")
        except Exception:
            is_on = False
        if is_on:
            self.after(300, self.refresh)

    def _on_bat_line_evt(self, evt):
        self.handle_raw_bat(str(getattr(evt, "data", "")))

    def _on_bat_val_evt(self, evt):
        self.handle_bat_val(str(getattr(evt, "data", "")))

    def _on_notify_maybe_bat_evt(self, evt):
        txt = str(getattr(evt, "data", "") or "").strip()
        if not txt: return
        if txt.startswith(("'", '"')) and txt.endswith(("'", '"')):
            txt = txt[1:-1]
        if txt.startswith(("BAT,", "VBAT,")):
            self.handle_raw_bat(txt)

    # ---------- Parsing & UI update ----------
    @staticmethod
    def _parse_voltage_from_line(txt: str):
        try:
            if "," in txt:
                _tag, num = txt.split(",", 1)
                return float(num.strip())
            return float(txt.strip().split()[-1])
        except Exception:
            return None

    def _update_voltage(self, v: float):
        # Smooth UI (simple EMA to tame ADC jitter)
        if self._ema_v is None:
            self._ema_v = v
        else:
            a = self._ema_alpha
            self._ema_v = a*v + (1-a)*self._ema_v

        self.volts = v
        p = self._v_to_percent(self._ema_v)
        self.percent = p

        self.lbl.config(text=f"{p}% ({v:.2f} V)")
        self._draw_icon(p)

    # ---------- mapping & colors ----------
    def _v_to_percent(self, v):
        pct = int(round((v - self.vmin) / (self.vmax - self.vmin) * 100))
        return _clamp(pct, 0, 100)

    def _fill_color(self, pct):
        if pct <= 20:  return "#ef4444"  # red-500
        if pct <= 50:  return "#eab308"  # yellow-500
        return "#22c55e"                 # green-500

    # ---------- drawing (scaled) ----------
    def _bg(self, widget):
        # Try to use the widget/ttk style bg; fall back to a neutral light gray
        try:
            bg = widget.cget("background")
            if bg:
                return bg
        except Exception:
            pass
        return "#f1f5f9"  # SB_SURFACE fallback

    def _draw_icon(self, percent):
        c = self.canvas
        # Delete only the battery drawing, keep everything else
        c.delete("bat")

        # Current canvas size
        w = int(float(c.cget("width")))
        h = int(float(c.cget("height")))

        # Base design size (original coordinates)
        BW, BH = 120.0, 48.0
        sx, sy = w / BW, h / BH
        s = (sx + sy) / 2.0  # for stroke thickness scaling

        def X(x): return x * sx
        def Y(y): return y * sy

        stroke = "#1f2937"  # gray-800
        stroke_w = max(1, int(2 * s))

        # body (scaled)
        x0, y0, x1, y1 = X(10), Y(10), X(100), Y(38)
        c.create_rectangle(x0, y0, x1, y1, outline=stroke, width=stroke_w, tags="bat")

        # cap (scaled)
        c.create_rectangle(X(100), Y(16), X(110), Y(32), outline=stroke, width=stroke_w, tags="bat")

        # segments (scaled)
        segs = 5
        inner_pad = X(8)  # horizontal inset inside body
        seg_w = (x1 - x0 - inner_pad) / segs
        filled = _clamp((percent + (100//segs - 1)) // (100//segs), 0, segs)
        color_on = self._fill_color(percent)
        color_off = "#e5e7eb"  # gray-200

        for i in range(segs):
            sx0 = x0 + X(4) + i * seg_w
            sx1 = sx0 + seg_w - X(2)
            c.create_rectangle(sx0, y0 + Y(4), sx1, y1 - Y(4),
                               outline="", fill=(color_on if i < filled else color_off), tags="bat")
