# =========================
# File: modules/battery.py
# =========================
import tkinter as tk
from tkinter import ttk

REFRESH_INTERVAL_MS = 10 * 60 * 1000  # 10 minutes

def _clamp(v, lo, hi): return max(lo, min(hi, v))

class BatteryTab(ttk.Frame):
    """Battery icon + percent. Polls board via BLE 'READ_BAT' on demand and every 10 minutes."""
    def __init__(self, master, ble, *, vmin=3.30, vmax=4.20, **_):
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

        # UI
        top = ttk.Frame(self, style="Card.TFrame"); top.pack(fill="x")
        self.canvas = tk.Canvas(top, width=120, height=48, bg=self._bg(top), highlightthickness=0)
        self.canvas.pack(side="left", padx=(0,12))
        self.lbl = ttk.Label(top, text="0% (0.00 V)", style="Lbl.TLabel"); self.lbl.pack(side="left")
        ttk.Button(top, text="Refresh now", command=self.refresh).pack(side="right")

        self._draw_icon(0)

        # Listen for BLE battery messages (root should event_generate("<<BLE:bat>>", data="BAT,3.87"))
        self.bind("<<BLE:bat>>", self._on_bat)
        # Also attach to toplevel so we catch events even if fired from elsewhere
        self.winfo_toplevel().bind("<<BLE:bat>>", self._on_bat, add="+")

        # First quick read, then periodic every 10 minutes
        self.after(600, self.refresh)
        self._schedule_periodic()

        # Clean up timers on destroy
        self.bind("<Destroy>", self._on_destroy, add="+")

    # ---- timers ----
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

    # ---- BLE interaction ----
    def refresh(self):
        """Ask the board for a fresh reading now."""
        self.ble.write_line("READ_BAT", require_response=True)

    def _on_bat(self, evt):
        """
        Expect payload like: "VBAT,3.87" or "BAT,3.87".
        We read evt.data (Tk 8.6 virtual event payload).
        """
        txt = ""
        try:
            txt = str(evt.data)
        except Exception:
            txt = getattr(evt, "data", "") or str(evt)

        if not txt:
            return

        # Extract the float voltage
        try:
            if "," in txt:
                _tag, num = txt.split(",", 1)
                v = float(num.strip())
            else:
                v = float(txt.strip().split()[-1])
        except Exception:
            return

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

    # ---- mapping & colors ----
    def _v_to_percent(self, v):
        # Linear map w/ clamp (swap with a LiPo curve later if desired)
        pct = int(round((v - self.vmin) / (self.vmax - self.vmin) * 100))
        return _clamp(pct, 0, 100)

    def _fill_color(self, pct):
        # traffic-light: <20% red, <50% yellow, else green
        if pct <= 20:  return "#ef4444"  # red-500
        if pct <= 50:  return "#eab308"  # yellow-500
        return "#22c55e"                 # green-500

    # ---- drawing ----
    def _bg(self, widget):
        try:
            return widget.cget("background")
        except Exception:
            return "white"

    def _draw_icon(self, percent):
        c = self.canvas
        c.delete("all")

        # body
        x0,y0,x1,y1 = 10,10,100,38
        stroke = "#1f2937"  # gray-800
        c.create_rectangle(x0,y0,x1,y1, outline=stroke, width=2)

        # cap
        c.create_rectangle(100,16,110,32, outline=stroke, width=2)

        # segments
        segs = 5
        seg_w = (x1 - x0 - 8) / segs
        filled = _clamp((percent + (100//segs - 1)) // (100//segs), 0, segs)
        color_on = self._fill_color(percent)
        color_off = "#e5e7eb"  # gray-200

        for i in range(segs):
            sx0 = x0 + 4 + i*seg_w
            sx1 = sx0 + seg_w - 2
            fill = color_on if i < filled else color_off
            c.create_rectangle(sx0, y0+4, sx1, y1-4, outline="", fill=fill)
