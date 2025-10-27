# =========================
# File: modules/led.py
# =========================
import math
import tkinter as tk
from tkinter import ttk

# Optional: pull colors from your shell so the box matches your theme
try:
    from ui.shell import SB_SURFACE, SB_SUBTEXT
except Exception:
    SB_SURFACE = "#f1f5f9"
    SB_SUBTEXT = "#334155"

# ---- Tweakable defaults ----
DEFAULT_RING_SIZE    = 140   # base canvas for the hue ring (we add brightness arc within)
DEFAULT_RING_WIDTH   = 26    # hue ring thickness
DEFAULT_KNOB_RADIUS  = 9     # hue knob radius
DEFAULT_CENTER_R     = 18    # center disc radius

# Brightness arc design (semi-circle; thin→thick for low→high)
BR_GAP_PX            = 12     # gap between hue ring and brightness arc
BR_ARC_DEG           = 160    # sweep of the brightness arc (<= 180 looks nice)
BR_MIN_THICK         = 5      # narrow end (low brightness)
BR_MAX_THICK         = 14     # thick end (high brightness)
BR_COLOR             = "#94a3b8"  # slate-ish track color
BR_KNOB_R            = 7      # brightness knob radius

# Place 0° (red) at the RIGHT side like typical HSV wheels
HUE_OFFSET_DEG       = -90.0


# ---------- HSV → RGB helper ----------
def hsv_to_rgb_bytes(h, s=1.0, v=1.0):
    """
    h in [0, 360), s,v in [0,1] → (r,g,b) in [0,255]
    """
    h = float(h % 360.0)
    s = max(0.0, min(1.0, float(s)))
    v = max(0.0, min(1.0, float(v)))
    c = v * s
    x = c * (1 - abs(((h / 60.0) % 2) - 1))
    m = v - c

    if   0 <= h < 60:   rp, gp, bp = c, x, 0
    elif 60 <= h < 120: rp, gp, bp = x, c, 0
    elif 120 <= h < 180:rp, gp, bp = 0, c, x
    elif 180 <= h < 240:rp, gp, bp = 0, x, c
    elif 240 <= h < 300:rp, gp, bp = x, 0, c
    else:               rp, gp, bp = c, 0, x

    r = int(round((rp + m) * 255))
    g = int(round((gp + m) * 255))
    b = int(round((bp + m) * 255))
    return r, g, b


# ---------- Canvas-based pill toggle switch ----------
class ToggleSwitch(ttk.Frame):
	"""
	A simple pill-style ON/OFF toggle.
	- variable: tk.BooleanVar to bind state
	- command:  optional callback called after state changes
	Keyboard: Space/Enter toggles.
	"""
	def __init__(self, master, variable=None, command=None, width=54, height=30, **kw):
		super().__init__(master, **kw)
		self.width_px  = int(width)
		self.height_px = int(height)
		self.radius_px = self.height_px // 2
		self.pad_px    = 2
		self._cmd      = command
		self.variable  = variable or tk.BooleanVar(value=False)

		# Colors (tweak to your theme)
		self._on_bg   = "#22c55e"   # green
		self._off_bg  = "#cbd5e1"   # slate-300
		self._knob    = "#ffffff"   # white
		self._border  = "#94a3b8"   # slate-400

		try:
			bg = master.cget("background")
		except Exception:
			bg = SB_SURFACE

		self.canvas = tk.Canvas(self, width=self.width_px, height=self.height_px,
		                        bg=bg, highlightthickness=0, bd=0)
		self.canvas.pack()

		# Hit/keyboard
		self.canvas.bind("<Button-1>", self._toggle_click)
		self.canvas.bind("<Key-space>", self._toggle_key)
		self.canvas.bind("<Return>", self._toggle_key)
		self.canvas.configure(cursor="hand2")
		self.canvas.focus_set()

		# Redraw when var changes
		self.variable.trace_add("write", lambda *_: self._redraw())

		self._redraw()

	def get(self):
		return bool(self.variable.get())

	def set(self, val: bool):
		self.variable.set(bool(val))
		self._redraw()

	def _toggle_click(self, _ev=None):
		self.variable.set(not self.variable.get())
		if callable(self._cmd):
			self._cmd()

	def _toggle_key(self, _ev=None):
		self._toggle_click()

	def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
		"""Draw a rounded rectangle as a single polygon curve."""
		points = [
			x1+r, y1,
			x2-r, y1,
			x2, y1,
			x2, y1+r,
			x2, y2-r,
			x2, y2,
			x2-r, y2,
			x1+r, y2,
			x1, y2,
			x1, y2-r,
			x1, y1+r,
			x1, y1,
		]
		return self.canvas.create_polygon(points, smooth=True, **kw)

	def _redraw(self):
		self.canvas.delete("all")
		is_on = bool(self.variable.get())
		bg = self._on_bg if is_on else self._off_bg

		# Track
		self._rounded_rect(1, 1, self.width_px-1, self.height_px-1, self.radius_px-1,
		                   fill=bg, outline=self._border, width=1)

		# Knob position
		x_left  = self.pad_px
		x_right = self.width_px - self.height_px + self.pad_px
		x = x_right if is_on else x_left

		# Knob (circle)
		self.canvas.create_oval(
			x, self.pad_px,
			x + self.height_px - 2*self.pad_px, self.height_px - self.pad_px,
			fill=self._knob, outline=self._border, width=1
		)


# ---------- ColorRing with outer BrightnessArc ----------
class ColorRing(tk.Canvas):
    def __init__(self, master,
                 size=DEFAULT_RING_SIZE, ring_width=DEFAULT_RING_WIDTH,
                 knob_radius=DEFAULT_KNOB_RADIUS, center_radius=DEFAULT_CENTER_R,
                 on_hue=None, on_brightness=None, **kw):
        """
        Hue wheel + tapered brightness arc outside it.

        Callbacks:
          on_hue(hue_deg, (r,g,b_full))            # fired on hue drag/click (RGB is full-bright)
          on_brightness(bright_0_255: int)         # fired on brightness drag/click
        """
        try:
            bg = master.cget("background")
        except tk.TclError:
            bg = SB_SURFACE

        # Add margin to fit outer brightness arc without clipping
        extra = BR_GAP_PX + BR_MAX_THICK // 2 + 6
        total = int(size + 2 * extra)

        super().__init__(master, width=total, height=total,
                         bg=bg, highlightthickness=0, bd=0, **kw)

        self.base_size     = int(size)
        self.size          = total
        self.ring_width    = int(ring_width)
        self.knob_radius   = int(knob_radius)
        self.center_radius = int(center_radius)

        self.on_hue        = on_hue
        self.on_brightness = on_brightness

        # Geometry
        self.cx = self.size // 2
        self.cy = self.size // 2
        self.r_outer_hue = (self.base_size // 2) - 2
        self.r_inner_hue = self.r_outer_hue - self.ring_width
        # Brightness arc is centered at this radius
        self.r_bright     = self.r_outer_hue + BR_GAP_PX + (BR_MAX_THICK // 2)

        # State (start red + mid-bright)
        self.hue   = 90.0     # geometry angle (0°=top, clockwise). 90° → red after offset
        self.value = 0.5      # 0..1 starting mid brightness
        self.sat   = 1.0
        self._drag_mode = None  # 'hue' | 'bright' | None

        # Draw static pieces
        self._draw_hue_ring()
        self._draw_center_disc()
        self._draw_brightness_arc()

        # Knobs
        self._hue_knob = self.create_oval(0, 0, 0, 0, outline="#0f172a", width=2, fill="#ffffff")
        self._bright_knob = self.create_oval(0, 0, 0, 0, outline="#0f172a", width=2, fill="#ffffff")

        # Initial positions
        self._update_hue_knob()
        self._update_center_disc()
        self._update_brightness_knob()

        # Events
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(cursor="hand2")

        # Fire initial callbacks to set device (red + mid brightness)
        if callable(self.on_brightness):
            self.on_brightness(self._brightness_to_255())
        if callable(self.on_hue):
            self.on_hue(self.hue, self._rgb_full())

    # ---- Public helpers ----
    def _rgb_full(self):
        """Current RGB at full brightness (V=1)."""
        h_adj = (self.hue + HUE_OFFSET_DEG) % 360.0
        return hsv_to_rgb_bytes(h_adj, self.sat, 1.0)

    def _brightness_to_255(self):
        return int(round(self.value * 255.0))

    # ---- Drawing ----
    def _draw_hue_ring(self):
        pad = (self.size - self.base_size) // 2
        x1, y1 = pad + 2, pad + 2
        x2, y2 = self.size - pad - 2, self.size - pad - 2
        step = 2
        for a in range(0, 360, step):
            h_adj = (a + HUE_OFFSET_DEG) % 360.0
            r, g, b = hsv_to_rgb_bytes(h_adj, 1.0, 1.0)
            color = f"#{r:02x}{g:02x}{b:02x}"
            tk_start = 90 - a - step/2
            self.create_arc(x1, y1, x2, y2, start=tk_start, extent=step,
                            style="arc", outline=color, width=self.ring_width)

    def _draw_center_disc(self):
        # Always full-bright preview of hue; no outline
        r, g, b = self._rgb_full()
        cr = self.center_radius
        self._center_disc = self.create_oval(self.cx - cr, self.cy - cr, self.cx + cr, self.cy + cr,
                                             fill=f"#{r:02x}{g:02x}{b:02x}", outline="", width=0)

    def _draw_brightness_arc(self):
        # Tapered arc (thin → thick) over BR_ARC_DEG degrees, centered at bottom (270°)
        self._br_items = []
        start = 270 - BR_ARC_DEG/2
        end   = 270 + BR_ARC_DEG/2
        step  = 3
        for a in range(int(start), int(end), step):
            t = (a - start) / (end - start)  # 0..1 along arc
            width = BR_MIN_THICK + t * (BR_MAX_THICK - BR_MIN_THICK)
            tk_start = 90 - a - step/2
            item = self.create_arc(self.cx - self.r_bright, self.cy - self.r_bright,
                                   self.cx + self.r_bright, self.cy + self.r_bright,
                                   start=tk_start, extent=step,
                                   style="arc", outline=BR_COLOR, width=width)
            self._br_items.append(item)

    # ---- Updates ----
    def _update_hue_knob(self):
        x, y = self._polar_xy(self.hue, self.r_inner_hue + self.ring_width/2)
        kr = self.knob_radius
        self.coords(self._hue_knob, x - kr, y - kr, x + kr, y + kr)

    def _update_brightness_knob(self):
        # Map value 0..1 → angle along brightness arc (start→end)
        start = 270 - BR_ARC_DEG/2
        end   = 270 + BR_ARC_DEG/2
        a = start + self.value * (end - start)
        x, y = self._polar_xy(a, self.r_bright)
        br = BR_KNOB_R
        self.coords(self._bright_knob, x - br, y - br, x + br, y + br)

    def _update_center_disc(self):
        r, g, b = self._rgb_full()
        self.itemconfig(self._center_disc, fill=f"#{r:02x}{g:02x}{b:02x}")

    # ---- Geometry helpers ----
    def _polar_xy(self, angle_deg, radius):
        theta = math.radians(angle_deg - 90)  # our 0° at top → math 0° at +x
        return (self.cx + radius * math.cos(theta),
                self.cy + radius * math.sin(theta))

    def _angle_from_xy(self, x, y):
        dx, dy = x - self.cx, y - self.cy
        return (math.degrees(math.atan2(dy, dx)) + 90.0) % 360.0

    # ---- Hit tests ----
    def _hit_hue(self, r):
        return self.r_inner_hue <= r <= self.r_outer_hue

    def _hit_brightness(self, r):
        inner = self.r_bright - (BR_MAX_THICK/2) - 2
        outer = self.r_bright + (BR_MAX_THICK/2) + 2
        return inner <= r <= outer

    def _angle_in_brightness_arc(self, a):
        start = (270 - BR_ARC_DEG/2) % 360
        end   = (270 + BR_ARC_DEG/2) % 360
        if start < end:
            return start <= a <= end
        else:
            return a >= start or a <= end

    # ---- Interaction ----
    def _on_click(self, ev):
        a = self._angle_from_xy(ev.x, ev.y)
        r = math.hypot(ev.x - self.cx, ev.y - self.cy)

        if self._hit_hue(r):
            self._drag_mode = 'hue'
            self._set_hue_from_angle(a, fire=True)
        elif self._hit_brightness(r) and self._angle_in_brightness_arc(a):
            self._drag_mode = 'bright'
            self._set_brightness_from_angle(a, fire=True)
        else:
            self._drag_mode = None

    def _on_drag(self, ev):
        if not self._drag_mode:
            return
        a = self._angle_from_xy(ev.x, ev.y)
        if self._drag_mode == 'hue':
            self._set_hue_from_angle(a, fire=True)
        elif self._drag_mode == 'bright':
            if self._angle_in_brightness_arc(a):
                self._set_brightness_from_angle(a, fire=True)
            else:
                # clamp to nearest end
                start = 270 - BR_ARC_DEG/2
                end   = 270 + BR_ARC_DEG/2
                a_clamp = start if (a - start) % 360 < (end - a) % 360 else end
                self._set_brightness_from_angle(a_clamp, fire=True)

    def _on_release(self, _ev):
        self._drag_mode = None

    def _set_hue_from_angle(self, angle_deg, fire=True):
        h = float(angle_deg) % 360.0
        if abs(h - self.hue) > 1e-6:
            self.hue = h
            self._update_hue_knob()
            self._update_center_disc()
            if fire and callable(self.on_hue):
                self.on_hue(self.hue, self._rgb_full())

    def _set_brightness_from_angle(self, angle_deg, fire=True):
        start = 270 - BR_ARC_DEG/2
        end   = 270 + BR_ARC_DEG/2
        # Normalize to 0..1 along arc
        t = (angle_deg - start) / (end - start)
        v = max(0.0, min(1.0, t))
        if abs(v - self.value) > 1e-4:
            self.value = v
            self._update_brightness_knob()
            if fire and callable(self.on_brightness):
                self.on_brightness(self._brightness_to_255())


class LedTab(ttk.Frame):
    def __init__(self, master, ble, *_, **__):
        super().__init__(master, padding=12, style="Card.TFrame")
        self.ble = ble

        # Remember last color we selected (full-bright RGB) and brightness
        self._last_rgb_full = (255, 0, 0)    # updated on hue changes (even when power OFF)
        self._current_brightness = 128       # live UI brightness (0..255)
        self._last_nonzero_brightness = 128  # used to restore after power-on if current==0

        # ---- Local styles for the labeled box ----
        style = ttk.Style(self)
        style.configure("Group.TLabelframe", background=SB_SURFACE, relief="flat")
        style.configure("Group.TLabelframe.Label", background=SB_SURFACE, foreground=SB_SUBTEXT, font=("Segoe UI", 12, "bold"))
        style.configure("GroupLbl.TLabel", background=SB_SURFACE, foreground=SB_SUBTEXT)

        # =========================
        # LED Control section (boxed)
        # =========================
        box = ttk.LabelFrame(self, text="LED Control", padding=8, style="Group.TLabelframe")
        box.grid(row=0, column=0, sticky="nw", padx=6, pady=6)

        # --- Hue + Brightness ring widget ---
        ring_frame = ttk.Frame(box, style="Group.TLabelframe")
        ring_frame.grid(row=0, column=0, columnspan=3, sticky="w", pady=(2, 4))
        ttk.Label(ring_frame, text="Hue ring (inside) • Brightness arc (outside)", style="GroupLbl.TLabel").pack(anchor="w")

        # Define before ColorRing so callbacks can read it
        self.rainbow = tk.BooleanVar(value=False)
        self.power_on = tk.BooleanVar(value=True)  # power state

        # Power row with toggle switch + label + rainbow
        power_row = ttk.Frame(box, style="Group.TLabelframe")
        power_row.grid(row=1, column=0, sticky="w", pady=(4, 2))

        self.power_switch = ToggleSwitch(power_row, variable=self.power_on, command=self._toggle_power, width=40, height=20)
        self.power_switch.pack(side="left")
        ttk.Label(power_row, text="LED Power", style="GroupLbl.TLabel").pack(side="left", padx=(8, 0))

        ttk.Checkbutton(
            power_row, text="Rainbow animation", variable=self.rainbow, command=self._toggle_rainbow
        ).pack(side="left", padx=(16, 0))

        self.ring = ColorRing(
            ring_frame,
            size=DEFAULT_RING_SIZE,
            ring_width=DEFAULT_RING_WIDTH,
            knob_radius=DEFAULT_KNOB_RADIUS,
            center_radius=DEFAULT_CENTER_R,
            on_hue=self._on_hue_changed,
            on_brightness=self._on_brightness_changed,
        )
        self.ring.pack(anchor="center", pady=6)

        # Layout
        box.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)
        self.columnconfigure(0, weight=0)

    # ------------ BLE send wrappers ------------
    def _send_bright(self, b):
        self.ble.write_line(f"BRIGHT {int(b)}")

    def _send_rgb(self, r, g, b):
        self.ble.write_line(f"RGB {r} {g} {b}")

    # ------------ Callbacks ------------
    def _on_hue_changed(self, _hue_deg, rgb_full):
        # Always remember the selection, even when power is OFF (staging)
        self._last_rgb_full = tuple(rgb_full)

        if not self.power_on.get():
            # Staging only; do not send while off
            return

        # If rainbow is on, turn it off when a solid color is picked
        if self.rainbow.get():
            self.rainbow.set(False)
            self.ble.write_line("RAINBOW OFF")

        # Use current brightness (or last nonzero) when applying color
        br = self._current_brightness if self._current_brightness > 0 else max(1, self._last_nonzero_brightness)
        self._send_bright(br)
        r, g, b = rgb_full
        self._send_rgb(r, g, b)

    def _on_brightness_changed(self, b_0_255):
        prev = self._current_brightness
        b = int(b_0_255)
        self._current_brightness = b
        if b > 0:
            self._last_nonzero_brightness = b

        if not self.power_on.get():
            # Staging only; do not send while off
            return

        self._send_bright(b)  # live update

        # If we just came up from 0, many devices need the color resent
        if prev == 0 and b > 0:
            r, g, b_rgb = self._last_rgb_full
            self._send_rgb(r, g, b_rgb)

    # ------------ UI actions ------------
    def _toggle_power(self):
        if self.power_on.get():
            # OFF -> ON : restore last staged settings
            br = self._current_brightness if self._current_brightness > 0 else max(1, self._last_nonzero_brightness)
            self._send_bright(br)
            r, g, b = self._last_rgb_full
            self._send_rgb(r, g, b)
            self.ring.configure(cursor="hand2")
        else:
            # ON -> OFF : stop any rainbow and go black
            if self.rainbow.get():
                self.rainbow.set(False)
                self.ble.write_line("RAINBOW OFF")
            self.ble.write_line("RGB 0 0 0")
            self.ring.configure(cursor="arrow")

    def _toggle_rainbow(self):
        # Ignore rainbow requests while powered OFF (but keep checkbox state false)
        if not self.power_on.get():
            if self.rainbow.get():
                self.rainbow.set(False)
            return  # exit early; don't send anything

        # Send the BLE command normally
        self.ble.write_line("RAINBOW ON" if self.rainbow.get() else "RAINBOW OFF")

