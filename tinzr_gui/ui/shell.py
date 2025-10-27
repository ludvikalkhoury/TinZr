# =========================
# File: ui/shell.py
# =========================
from queue import Queue
import tkinter as tk
from tkinter import ttk

SB_BLUE      = "#2563eb"
SB_BLUE_DARK = "#1d4ed8"
SB_TEXT      = "#0f172a"
SB_SUBTEXT   = "#334155"
SB_SURFACE   = "#f1f5f9"
SB_BG        = "#e2e8f0"
SB_TRACK_OFF = "#cbd5e1"
SB_KNOB      = "#ffffff"

def _rounded_pill(canvas: tk.Canvas, x1, y1, x2, y2, fill, outline=""):
    r = (y2 - y1) / 2
    left  = canvas.create_oval(x1, y1, x1 + 2*r, y2, fill=fill, outline=outline, width=0)
    right = canvas.create_oval(x2 - 2*r, y1, x2, y2, fill=fill, outline=outline, width=0)
    mid   = canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=outline, width=0)
    return (left, right, mid)

class ToggleSwitch(tk.Canvas):
    def __init__(self, master, width=66, height=30, on_color=SB_BLUE, off_color=SB_TRACK_OFF,
                 knob_color=SB_KNOB, command=None, **kw):
        try:
            bg = master.cget("background")
        except tk.TclError:
            bg = SB_BG
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, **kw)

        self.on_color = on_color
        self.off_color = off_color
        self.knob_color = knob_color
        self.command = command
        self.value = False
        self._track_items = _rounded_pill(self, 2, 2, width-2, height-2, fill=self.off_color)
        self._knob = self.create_oval(2, 2, height-2, height-2, fill=self.knob_color, outline="", width=0)
        self.bind("<Button-1>", self._on_click)
        self.configure(cursor="hand2")
        self._render()

    def set(self, value: bool, fire: bool = True):
        value = bool(value)
        if self.value != value:
            self.value = value
            self._render()
            if fire and callable(self.command):
                self.command(self.value)

    def get(self): return self.value
    def _on_click(self, _=None): self.set(not self.value, fire=True)

    def _render(self):
        w, h = int(self.cget("width")), int(self.cget("height"))
        color = self.on_color if self.value else self.off_color
        for iid in self._track_items: self.itemconfig(iid, fill=color)
        pad = 2
        if self.value:
            self.coords(self._knob, w - h + pad, pad, w - pad, h - pad)
        else:
            self.coords(self._knob, pad, pad, h - pad, h - pad)

class AppShell(tk.Tk):
    """
    - Left cluster (combo + Scan + toggle + status) stays tight & left.
    - Battery module (full BatteryTab frame) mounts inline in the SAME TOP ROW,
      immediately to the right of the status, still on the left side.
    """
    def __init__(self, ble_worker, title="TinZr Control (BLE)"):
        super().__init__()
        self.title(title)
        self.geometry("700x800")
        self.configure(bg=SB_BG)
        self.iconbitmap("TinZr_small_logo.ico")  # path to your .ico file

        self.ble = ble_worker
        self.devices = []
        self.selected_addr = None

        # ---- Styles ----
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=SB_BG)
        style.configure("Card.TFrame", background=SB_SURFACE, relief="flat")
        style.configure("Title.TLabel", background=SB_BG, foreground=SB_TEXT, font=("Segoe UI", 16, "bold"))
        style.configure("Lbl.TLabel", background=SB_SURFACE, foreground=SB_SUBTEXT)
        style.configure("Btn.TButton", background=SB_BLUE, foreground="white")
        style.map("Btn.TButton", background=[("active", SB_BLUE_DARK)])

        # ---- Header (title only) ----
        header = ttk.Frame(self, style="TFrame"); header.pack(fill="x", padx=12, pady=8)
        ttk.Label(header, text="TinZr Control", style="Title.TLabel").pack(side="left")

        # ---- Top connection row (outer) ----
        self.conn_row = ttk.Frame(self, padding=1, style="Card.TFrame")
        self.conn_row.pack(fill="x", padx=12, pady=1)

        # Left-aligned cluster (search/scan/toggle/status)
        self.left_cluster = ttk.Frame(self.conn_row, style="Card.TFrame")
        self.left_cluster.pack(side="left", anchor="w")

        # Put widgets close together horizontally (keep heights default)
        self.cbo = ttk.Combobox(self.left_cluster, width=25, state="readonly")
        self.cbo.pack(side="left", padx=(0, 6))

        self.scan_btn = ttk.Button(self.left_cluster, text="Scan", command=self._scan, style="Btn.TButton")
        self.scan_btn.pack(side="left", padx=(0, 6))

        self.conn_toggle = ToggleSwitch(self.left_cluster, width=50, height=25, command=self._on_toggle_changed)
        self.conn_toggle.pack(side="left", padx=(0, 6))

        self.conn_status_lbl = ttk.Label(self.left_cluster, text="Disconnected", style="Lbl.TLabel")
        self.conn_status_lbl.pack(side="left", padx=(0, 8))

        # Spacer expands so anything added after stays on the left but can keep going
        ttk.Frame(self.conn_row, style="Card.TFrame").pack(side="left", expand=True, fill="x")

        # A holder where we will attach the BatteryTab inline (to the LEFT cluster area).
        # We keep it on the left side, after status, not at the far right.
        self.battery_holder = ttk.Frame(self.conn_row, style="Card.TFrame")
        self.battery_holder.pack(side="left", anchor="w", padx=(0, 0))

        # ---- Notebook (for other tabs) ----
        nb_frame = ttk.Frame(self, style="TFrame")
        nb_frame.pack(fill="x", expand=False, padx=12, pady=4)  # reduced height region

        self.nb = ttk.Notebook(nb_frame, height=420)  # limit the tab area height
        self.nb.pack(fill="both", expand=False)


        # ---- Log panel ----
        logf = ttk.Frame(self, padding=12, style="Card.TFrame")
        logf.pack(fill="both", expand=False, padx=12, pady=6)
        ttk.Label(logf, text="Log", style="Lbl.TLabel").pack(anchor="w")
        self.txt = tk.Text(logf, height=8, bg="white")
        self.txt.pack(fill="both", expand=True, pady=(6, 0))

        # ---- BLE events ----
        self.bind("<<BLE:connected>>", self._on_connected_evt)
        self.bind("<<BLE:notify>>", self._on_notify_evt)

        # ---- Scan spinner state ----
        self._is_scanning = False
        self._spin_job = None
        self._spin_i = 0

        # Auto-scan shortly after startup
        self.after(200, self._scan)

    # ----------------- Battery inline attach API -----------------
    def attach_battery_inline(self, battery_frame: ttk.Frame):
        """
        Mount the full BatteryTab frame inline on the top row,
        right after the search/scan/toggle/status cluster (still on the left).
        """
        # Reparent and pack next to the cluster, with a small gap
        # BatteryTab is already a ttk.Frame; we just pack it here.
        battery_frame.master = self.battery_holder  # not strictly necessary in Tk, but clarifies intent
        battery_frame.pack(in_=self.battery_holder, side="left", padx=(12, 0))  # show it next to the cluster

    # ---------------- Public API for tabs ----------------
    def add_tab(self, frame: ttk.Frame, title: str):
        self.nb.add(frame, text=title)

    def set_ble_devices(self, devices):
        self.devices = devices or []
        labels = [f'{d.get("name","(no-name)")} [{d.get("address","?")}]' for d in self.devices]
        self.cbo["values"] = labels
        if labels:
            self.cbo.current(0)
            self.selected_addr = self.devices[0].get("address")
        else:
            self.cbo.set(""); self.selected_addr = None

    # ---------------- Scan animation ----------------
    def start_scanning_ui(self):
        if self._is_scanning: return
        self._is_scanning = True; self._spin_i = 0
        try: self.scan_btn.state(["disabled"])
        except Exception: pass
        def _tick():
            if not self._is_scanning: return
            dots = "." * (self._spin_i % 4)
            self.scan_btn.config(text=f"Scanning{dots}")
            self._spin_i += 1
            self._spin_job = self.after(200, _tick)
        _tick()

    def stop_scanning_ui(self):
        if not self._is_scanning: return
        self._is_scanning = False
        if self._spin_job:
            try: self.after_cancel(self._spin_job)
            except Exception: pass
            self._spin_job = None
        try: self.scan_btn.state(["!disabled"])
        except Exception: pass
        self.scan_btn.config(text="Scan")

    # ---------------- UI helpers ----------------
    def _append(self, line: str):
        self.txt.insert("end", line + "\n"); self.txt.see("end")

    def _on_combo_selected(self, _=None):
        idx = self.cbo.current()
        if 0 <= idx < len(self.devices): self.selected_addr = self.devices[idx].get("address")

    # ---------------- Button/toggle callbacks ----------------
    def _scan(self):
        self.start_scanning_ui()
        try: self.ble.scan(4.0)
        except Exception as e:
            self._append(f"Scan error: {e}"); self.stop_scanning_ui()
        self.after(6000, lambda: (self._is_scanning and self.stop_scanning_ui()))

    def _on_toggle_changed(self, is_on: bool):
        if is_on:
            self.conn_status_lbl.config(text="Connecting…")
            self._connect()
        else:
            self.conn_status_lbl.config(text="Disconnected")
            self._disconnect()

    def _connect(self):
        if not self.selected_addr and self.devices:
            self.selected_addr = self.devices[0].get("address")
            self.cbo.set(f'{self.devices[0].get("name","(no-name)")} [{self.selected_addr}]')
        try:
            self.ble.connect(self.selected_addr)
        except Exception as e:
            self._append(f"Connect error: {e}")
            self.conn_status_lbl.config(text="Disconnected")

    def _disconnect(self):
        try: self.ble.disconnect()
        except Exception as e: self._append(f"Disconnect error: {e}")
        self.conn_status_lbl.config(text="Disconnected")

    # ---------------- Event handlers ----------------
    def _on_connected_evt(self, _evt):
        # Treat any <<BLE:connected>> as 'connected established' signal.
        if self.conn_toggle.get():
            self.conn_status_lbl.config(text="Connected")
        else:
            self.conn_status_lbl.config(text="Disconnected")

    def _on_notify_evt(self, evt):
        line = str(getattr(evt, "data", "")).rstrip()
        if line: self._append(f"← {line}")

    def destroy(self):
        try: self.ble.stop()
        except Exception: pass
        return super().destroy()
