# =========================
# File: modules/imu.py
# =========================
import tkinter as tk
from tkinter import ttk
from collections import deque

# Reuse the same pretty toggle switch from the LED tab
from modules.led import ToggleSwitch

try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    _HAVE_MPL = True
except Exception:
    _HAVE_MPL = False

# ---- plotting / buffer params (defaults) ----
DEFAULT_HISTORY_SAMPLES = 300   # ~30s @ 10 Hz
REDRAW_EVERY_MS         = 100   # redraw throttle
CENTER_WINDOW           = 100   # samples for rolling centering

# Fixed y-limits per channel (tweak as you like)
ACC_YLIM = (-30, 30)        # g
GYR_YLIM = (-500, 500)      # dps

# PPG auto-scale guard rails
PPG_MIN_RANGE       = 50.0   # if data is flat, enforce at least this range
PPG_MARGIN_RATIO    = 0.10   # 10% margin top & bottom
PPG_WINDOW_FRACTION = 1.0    # use full visible window for autoscale (1.0 = all points)

# ---------- helpers ----------
def _mean(dq: deque):
    return (sum(dq) / len(dq)) if dq else 0.0

def _resize_deque(dq: deque, new_maxlen: int) -> deque:
    """Return a *new* deque with the same tail of items and the new maxlen."""
    new_maxlen = max(0, int(new_maxlen))
    return deque(list(dq)[-new_maxlen:], maxlen=new_maxlen)


# ===================== main IMU tab =====================
class ImuTab(ttk.Frame):
    """IMU+PPG live view with 9 subplots laid out as:
       ┌──────────── top row ────────────┐
       │ Accel (ax/ay/az) | Gyro (gx/gy/gz) │
       └───────────────────────────────────┘
       ┌──────────── bottom row ──────────┐
       │       PPG (IR/RED/GREEN)         │
       └───────────────────────────────────┘
    """
    def __init__(self, master, ble, *_, **__):
        super().__init__(master, padding=12, style="Card.TFrame")
        self.ble = ble

        # ---- settings / state ----
        self.history_len = DEFAULT_HISTORY_SAMPLES

        # --- accel histories & rolling windows ---
        self.ax_hist = deque(maxlen=self.history_len)
        self.ay_hist = deque(maxlen=self.history_len)
        self.az_hist = deque(maxlen=self.history_len)
        self.ax_win  = deque(maxlen=CENTER_WINDOW)
        self.ay_win  = deque(maxlen=CENTER_WINDOW)
        self.az_win  = deque(maxlen=CENTER_WINDOW)

        # --- gyro histories & rolling windows ---
        self.gx_hist = deque(maxlen=self.history_len)
        self.gy_hist = deque(maxlen=self.history_len)
        self.gz_hist = deque(maxlen=self.history_len)
        self.gx_win  = deque(maxlen=CENTER_WINDOW)
        self.gy_win  = deque(maxlen=CENTER_WINDOW)
        self.gz_win  = deque(maxlen=CENTER_WINDOW)

        # --- ppg histories & rolling windows ---
        self.ir_hist   = deque(maxlen=self.history_len)
        self.red_hist  = deque(maxlen=self.history_len)
        self.grn_hist  = deque(maxlen=self.history_len)
        self.ir_win    = deque(maxlen=CENTER_WINDOW)
        self.red_win   = deque(maxlen=CENTER_WINDOW)
        self.grn_win   = deque(maxlen=CENTER_WINDOW)

        # --- plot (acc+gyro top row; ppg bottom spanning both) ---
        self._canvas = None
        if _HAVE_MPL:
            fig = Figure(figsize=(6, 3), dpi=100)
            fig.subplots_adjust(top=0.95)

            # 2x2 grid (PPG spans bottom row)
            outer = fig.add_gridspec(
                2, 2,
                height_ratios=[1, 1],
                width_ratios=[1, 1],
                hspace=0.35,
                wspace=0.25
            )

            acc_gs = outer[0, 0].subgridspec(3, 1, hspace=0.0)
            gyr_gs = outer[0, 1].subgridspec(3, 1, hspace=0.0)
            ppg_gs = outer[1, :].subgridspec(3, 1, hspace=0.0)

            # Accel axes (share x within)
            self.ax_acc_ax = fig.add_subplot(acc_gs[0])
            self.ax_acc_ay = fig.add_subplot(acc_gs[1], sharex=self.ax_acc_ax)
            self.ax_acc_az = fig.add_subplot(acc_gs[2], sharex=self.ax_acc_ax)

            # Gyro axes
            self.ax_gyr_gx = fig.add_subplot(gyr_gs[0])
            self.ax_gyr_gy = fig.add_subplot(gyr_gs[1], sharex=self.ax_gyr_gx)
            self.ax_gyr_gz = fig.add_subplot(gyr_gs[2], sharex=self.ax_gyr_gx)

            # PPG axes
            self.ax_ppg_ir  = fig.add_subplot(ppg_gs[0])
            self.ax_ppg_red = fig.add_subplot(ppg_gs[1], sharex=self.ax_ppg_ir)
            self.ax_ppg_grn = fig.add_subplot(ppg_gs[2], sharex=self.ax_ppg_ir)

            # Y labels / limits — keep axis labels, remove tick marks & numbers
            for ax, lab in [(self.ax_acc_ax, "ax"), (self.ax_acc_ay, "ay"), (self.ax_acc_az, "az")]:
                ax.set_ylabel(lab, fontsize=7, labelpad=4)
                ax.set_ylim(*ACC_YLIM)
                ax.grid(False)
                ax.tick_params(left=False, labelleft=False, bottom=False, labelbottom=False)

            for ax, lab in [(self.ax_gyr_gx, "gx"), (self.ax_gyr_gy, "gy"), (self.ax_gyr_gz, "gz")]:
                ax.set_ylabel(lab, fontsize=7, labelpad=4)
                ax.set_ylim(*GYR_YLIM)
                ax.grid(False)
                ax.tick_params(left=False, labelleft=False, bottom=False, labelbottom=False)

            # --- PPG axis label colors ---
            self.ax_ppg_ir.set_ylabel("IR",   fontsize=7, labelpad=4, color="#000000")
            self.ax_ppg_red.set_ylabel("Red", fontsize=7, labelpad=4, color="#ef4444")
            self.ax_ppg_grn.set_ylabel("Green", fontsize=7, labelpad=4, color="#22c55e")
            for ax in (self.ax_ppg_ir, self.ax_ppg_red, self.ax_ppg_grn):
                ax.set_ylim(-100, 100)
                ax.grid(False)
                ax.tick_params(left=False, labelleft=False, bottom=False, labelbottom=False)

            # Lines (one per axis)
            (self.l_ax,)   = self.ax_acc_ax.plot([], [])
            (self.l_ay,)   = self.ax_acc_ay.plot([], [])
            (self.l_az,)   = self.ax_acc_az.plot([], [])

            (self.l_gx,)   = self.ax_gyr_gx.plot([], [])
            (self.l_gy,)   = self.ax_gyr_gy.plot([], [])
            (self.l_gz,)   = self.ax_gyr_gz.plot([], [])

            # --- PPG line colors ---
            (self.l_ir,)   = self.ax_ppg_ir.plot([], [], color="#000000")
            (self.l_red,)  = self.ax_ppg_red.plot([], [], color="#ef4444")
            (self.l_grn,)  = self.ax_ppg_grn.plot([], [], color="#22c55e")

            canvas = FigureCanvasTkAgg(fig, master=self)
            canvas.draw()
            canvas.get_tk_widget().pack(fill="both", expand=True)
            self._canvas = canvas

        # --- controls (single line: toggles + points-to-show + Clear) ---
        ctr = ttk.Frame(self, style="Card.TFrame"); ctr.pack(fill="x", pady=(8, 0))

        # Toggles (reuse the same ToggleSwitch from LED tab)
        tog = ttk.Frame(ctr, style="Card.TFrame"); tog.pack(side="left")

        self._imu_on = tk.BooleanVar(value=False)
        ToggleSwitch(
            tog, variable=self._imu_on,
            command=lambda: self._toggle_imu(self._imu_on.get()),
            width=40, height=20
        ).pack(side="left")
        ttk.Label(tog, text="IMU").pack(side="left", padx=(6, 12))

        self._ppg_on = tk.BooleanVar(value=False)
        ToggleSwitch(
            tog, variable=self._ppg_on,
            command=lambda: self._toggle_ppg(self._ppg_on.get()),
            width=40, height=20
        ).pack(side="left")
        ttk.Label(tog, text="PPG").pack(side="left", padx=(6, 12))

        # Points to show (inline, same row)
        pts = ttk.Frame(ctr, style="Card.TFrame"); pts.pack(side="left", padx=(12, 0))
        ttk.Label(pts, text="Points to show:", style="Lbl.TLabel").pack(side="left")
        self.history_var = tk.IntVar(value=self.history_len)
        sp = ttk.Spinbox(pts, from_=100, to=5000, increment=50,
                         textvariable=self.history_var, width=7, command=self._apply_history_len)
        sp.pack(side="left", padx=(6, 0))
        sp.bind("<Return>", lambda e: self._apply_history_len())
        sp.bind("<FocusOut>", lambda e: self._apply_history_len())

        # Clear button comes after (same row)
        ttk.Button(ctr, text="Clear", command=self._clear).pack(side="left", padx=(12, 0))

        # --- event subscriptions ---
        self.bind_all("<<BLE:imu>>", self._on_imu_evt, add="+")
        self.bind_all("<<BLE:ppg>>", self._on_ppg_evt, add="+")

        # --- redraw ticker ---
        self._redraw_pending = False
        if _HAVE_MPL:
            self.after(REDRAW_EVERY_MS, self._redraw_timer)

    # ===== BLE toggle callbacks =====
    def _toggle_imu(self, on: bool):
        try:
            self.ble.write_line("START_IMU" if on else "STOP_IMU")
        except Exception:
            pass

    def _toggle_ppg(self, on: bool):
        try:
            self.ble.write_line("START_PPG" if on else "STOP_PPG")
        except Exception:
            pass

    # ===== callbacks =====
    def _apply_history_len(self):
        try:
            n = int(self.history_var.get())
        except Exception:
            return
        n = max(50, min(10000, n))
        self.history_var.set(n)
        self.history_len = n

        # Reassign each history deque to a resized one
        self.ax_hist  = _resize_deque(self.ax_hist,  n)
        self.ay_hist  = _resize_deque(self.ay_hist,  n)
        self.az_hist  = _resize_deque(self.az_hist,  n)
        self.gx_hist  = _resize_deque(self.gx_hist,  n)
        self.gy_hist  = _resize_deque(self.gy_hist,  n)
        self.gz_hist  = _resize_deque(self.gz_hist,  n)
        self.ir_hist  = _resize_deque(self.ir_hist,  n)
        self.red_hist = _resize_deque(self.red_hist, n)
        self.grn_hist = _resize_deque(self.grn_hist, n)

        self._redraw_pending = True

    # ===== Public direct handlers (optional) =====
    def handle_imu_line(self, text: str):
        if not text: return
        self._handle_imu_common(text)

    def handle_ppg_line(self, text: str):
        if not text: return
        self._handle_ppg_common(text)

    # ===== Tk event handlers =====
    def _on_imu_evt(self, evt):
        self._handle_imu_common(str(getattr(evt, "data", "")))

    def _on_ppg_evt(self, evt):
        self._handle_ppg_common(str(getattr(evt, "data", "")))

    # ===== Core parse/update: IMU =====
    def _handle_imu_common(self, payload: str):
        try:
            parts = [p.strip() for p in payload.split(",")]
            if not parts or parts[0] != "IMU" or len(parts) < 8:
                return
            raw_ax, raw_ay, raw_az = float(parts[1]), float(parts[2]), float(parts[3])
            gx, gy, gz = float(parts[4]), float(parts[5]), float(parts[6])
        except Exception:
            return

        # update rolling windows
        self.ax_win.append(raw_ax); self.ay_win.append(raw_ay); self.az_win.append(raw_az)
        self.gx_win.append(gx);     self.gy_win.append(gy);     self.gz_win.append(gz)

        # rolling mean centering
        bax, bay, baz = _mean(self.ax_win), _mean(self.ay_win), _mean(self.az_win)
        bgx, bgy, bgz = _mean(self.gx_win), _mean(self.gy_win), _mean(self.gz_win)

        # centered
        ax = raw_ax - bax; ay = raw_ay - bay; az = raw_az - baz
        cgx = gx - bgx;   cgy = gy - bgy;     cgz = gz - bgz

        # histories
        self.ax_hist.append(ax)
        self.ay_hist.append(ay)
        self.az_hist.append(az)
        self.gx_hist.append(cgx)
        self.gy_hist.append(cgy)
        self.gz_hist.append(cgz)

        self._redraw_pending = True

    # ===== Core parse/update: PPG =====
    def _handle_ppg_common(self, payload: str):
        try:
            parts = [p.strip() for p in payload.split(",")]
            if not parts or parts[0] != "PPG" or len(parts) < 4:
                return
            raw_ir = float(parts[1]); raw_red = float(parts[2]); raw_grn = float(parts[3])
        except Exception:
            return

        # update rolling windows
        self.ir_win.append(raw_ir); self.red_win.append(raw_red); self.grn_win.append(raw_grn)

        # rolling-mean centering
        bir, bred, bgrn = _mean(self.ir_win), _mean(self.red_win), _mean(self.grn_win)
        cir  = raw_ir  - bir
        cred = raw_red - bred
        cgrn = raw_grn - bgrn

        # histories
        self.ir_hist.append(cir)
        self.red_hist.append(cred)
        self.grn_hist.append(cgrn)

        self._redraw_pending = True

    # ===== Plot helpers =====
    def _clear(self):
        # accel
        self.ax_hist.clear(); self.ay_hist.clear(); self.az_hist.clear()
        self.ax_win.clear();  self.ay_win.clear();  self.az_win.clear()
        # gyro
        self.gx_hist.clear(); self.gy_hist.clear(); self.gz_hist.clear()
        self.gx_win.clear();  self.gy_win.clear();  self.gz_win.clear()
        # ppg
        self.ir_hist.clear(); self.red_hist.clear(); self.grn_hist.clear()
        self.ir_win.clear();  self.red_win.clear();  self.grn_win.clear()
        if _HAVE_MPL:
            self._update_lines()
            self._canvas.draw_idle()

    def _redraw_timer(self):
        if self._redraw_pending and self._canvas:
            self._redraw_pending = False
            self._update_lines()
            self._canvas.draw_idle()
        self.after(REDRAW_EVERY_MS, self._redraw_timer)

    def _update_lines(self):
        if not _HAVE_MPL:
            return

        # --- set line data with per-series x length ---
        # accel
        xs_ax = list(range(len(self.ax_hist)))
        xs_ay = list(range(len(self.ay_hist)))
        xs_az = list(range(len(self.az_hist)))
        self.l_ax.set_data(xs_ax, list(self.ax_hist))
        self.l_ay.set_data(xs_ay, list(self.ay_hist))
        self.l_az.set_data(xs_az, list(self.az_hist))

        # gyro
        xs_gx = list(range(len(self.gx_hist)))
        xs_gy = list(range(len(self.gy_hist)))
        xs_gz = list(range(len(self.gz_hist)))
        self.l_gx.set_data(xs_gx, list(self.gx_hist))
        self.l_gy.set_data(xs_gy, list(self.gy_hist))
        self.l_gz.set_data(xs_gz, list(self.gz_hist))

        # ppg
        xs_ir  = list(range(len(self.ir_hist)))
        xs_red = list(range(len(self.red_hist)))
        xs_grn = list(range(len(self.grn_hist)))
        self.l_ir.set_data(xs_ir, list(self.ir_hist))
        self.l_red.set_data(xs_red, list(self.red_hist))
        self.l_grn.set_data(xs_grn, list(self.grn_hist))

        # --- x limits ---
        def _set_xlim(ax, n):
            ax.set_xlim(max(0, n - self.history_len), max(self.history_len, n))

        _set_xlim(self.ax_acc_az, len(self.ax_hist))
        _set_xlim(self.ax_gyr_gz, len(self.gx_hist))
        _set_xlim(self.ax_ppg_grn, len(self.ir_hist))

        # --- reassert fixed y-lims for acc & gyro ---
        for ax in (self.ax_acc_ax, self.ax_acc_ay, self.ax_acc_az):
            ax.set_ylim(*ACC_YLIM)
        for ax in (self.ax_gyr_gx, self.ax_gyr_gy, self.ax_gyr_gz):
            ax.set_ylim(*GYR_YLIM)

        # --- always autoscale PPG ---
        self._autoscale_ppg_axis(self.ax_ppg_ir,  self.ir_hist)
        self._autoscale_ppg_axis(self.ax_ppg_red, self.red_hist)
        self._autoscale_ppg_axis(self.ax_ppg_grn, self.grn_hist)

    # ---- PPG autoscale helpers ----
    def _visible_slice(self, axis, data: deque):
        if not data:
            return []
        x0, x1 = axis.get_xlim()
        n = len(data)
        i0 = max(0, int(x0))
        i1 = min(n, int(x1) + 1)
        seg = list(data)[i0:i1]
        return seg if seg else list(data)

    def _smooth_set_ylim(self, axis, target_lo, target_hi, alpha=0.35):
        cur_lo, cur_hi = axis.get_ylim()
        new_lo = cur_lo + alpha * (target_lo - cur_lo)
        new_hi = cur_hi + alpha * (target_hi - cur_hi)
        axis.set_ylim(new_lo, new_hi)

    def _autoscale_ppg_axis(self, axis, data: deque):
        ys = self._visible_slice(axis, data)
        if not ys:
            return
        y_min, y_max = min(ys), max(ys)
        rng = max(PPG_MIN_RANGE, (y_max - y_min))
        mid = 0.5 * (y_max + y_min)
        half = 0.5 * rng * (1.0 + 2.0 * PPG_MARGIN_RATIO)
        lo, hi = (mid - half), (mid + half)
        self._smooth_set_ylim(axis, lo, hi, alpha=0.35)

    # ===== Cleanup =====
    def destroy(self):
        try:
            if self._canvas:
                self._canvas.get_tk_widget().destroy()
        except Exception:
            pass
        return super().destroy()
