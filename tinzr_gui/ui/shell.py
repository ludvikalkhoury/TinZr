# =========================
# File: ui/shell.py
# =========================
from queue import Queue, Empty
import tkinter as tk
from tkinter import ttk

SB_BLUE      = "#2563eb"
SB_BLUE_DARK = "#1d4ed8"
SB_TEXT      = "#0f172a"
SB_SUBTEXT   = "#334155"
SB_SURFACE   = "#f1f5f9"
SB_BG        = "#e2e8f0"

class AppShell(tk.Tk):
    def __init__(self, ble_worker, title="TinZr Control (BLE)"):
        super().__init__()
        self.title(title)
        self.geometry("980x620")
        self.configure(bg=SB_BG)
        self.ble = ble_worker
        self.uiq: Queue = ble_worker._uiq  # shared queue from worker

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=SB_BG)
        style.configure("Card.TFrame", background=SB_SURFACE, relief="flat")
        style.configure("Title.TLabel", background=SB_BG, foreground=SB_TEXT, font=("Segoe UI", 16, "bold"))
        style.configure("Lbl.TLabel", background=SB_SURFACE, foreground=SB_SUBTEXT)
        style.configure("Btn.TButton", background=SB_BLUE, foreground="white")
        style.map("Btn.TButton", background=[("active", SB_BLUE_DARK)])

        header = ttk.Frame(self, style="TFrame"); header.pack(fill="x", padx=12, pady=8)
        ttk.Label(header, text="TinZr Control", style="Title.TLabel").pack(side="left")
        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(header, textvariable=self.status_var, style="Title.TLabel").pack(side="right")

        # Connection row
        conn = ttk.Frame(self, padding=12, style="Card.TFrame"); conn.pack(fill="x", padx=12, pady=8)
        self.cbo = ttk.Combobox(conn, width=48, state="readonly"); self.cbo.grid(row=0, column=0, sticky="we")
        ttk.Button(conn, text="Scan", command=self._scan, style="Btn.TButton").grid(row=0, column=1, padx=6)
        ttk.Button(conn, text="Connect", command=self._connect, style="Btn.TButton").grid(row=0, column=2, padx=6)
        ttk.Button(conn, text="Disconnect", command=self._disconnect, style="Btn.TButton").grid(row=0, column=3, padx=6)
        conn.columnconfigure(0, weight=1)

        # Notebook where modules add their tabs
        self.nb = ttk.Notebook(self); self.nb.pack(fill="both", expand=True, padx=12, pady=8)

        # Log panel
        logf = ttk.Frame(self, padding=12, style="Card.TFrame"); logf.pack(fill="both", expand=False, padx=12, pady=6)
        ttk.Label(logf, text="Log", style="Lbl.TLabel").pack(anchor="w")
        self.txt = tk.Text(logf, height=8, bg="white"); self.txt.pack(fill="both", expand=True, pady=(6,0))

        self.devices = []
        self.selected_addr = None
        self.after(200, self._scan)
        self.after(50, self._poll)

    def add_tab(self, frame: ttk.Frame, title: str):
        self.nb.add(frame, text=title)

    def _append(self, line: str):
        self.txt.insert("end", line + "\n"); self.txt.see("end")

    # Connection handlers
    def _scan(self):
        self.status_var.set("Scanning…")
        self.ble.scan(4.0)

    def _connect(self):
        if not self.selected_addr and self.devices:
            # default to first
            self.selected_addr = self.devices[0][1]
            self.cbo.set(f"{self.devices[0][0]} [{self.devices[0][1]}]")
        if self.selected_addr:
            self.status_var.set("Connecting…")
            self.ble.connect(self.selected_addr)

    def _disconnect(self):
        self.ble.disconnect()

    def _poll(self):
        try:
            while True:
                tag, payload = self.uiq.get_nowait()
                if tag == "log":
                    self._append(payload)
                elif tag == "scan_result":
                    self.devices = [(d.name, d.address) for d in payload]
                    labels = [f"{n} [{a}]" for n,a in self.devices]
                    self.cbo["values"] = labels
                    self.status_var.set("Disconnected")
                elif tag == "connected":
                    self.status_var.set("Connected" if payload else "Disconnected")
                elif tag == "notify":
                    self._append(f"← {payload}")
                else:
                    # fan-out to tabs (battery/imu/ppg)
                    self.event_generate(f"<<BLE:{tag}>>", when="tail", data=payload)
        except Empty:
            pass
        self.after(50, self._poll)

    def destroy(self):
        try:
            self.ble.stop()
        except Exception:
            pass
        return super().destroy()
