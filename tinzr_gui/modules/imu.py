

# =========================
# File: modules/imu.py
# =========================
import tkinter as tk
from tkinter import ttk

class ImuTab(ttk.Frame):
    """Shows latest IMU/Temp/PPG lines parsed from notifications."""
    def __init__(self, master, ble, *_, **__):
        super().__init__(master, padding=12, style="Card.TFrame")
        self.ble = ble
        # Simple labels; can be upgraded to plots later
        self.vars = {k: tk.StringVar(value="—") for k in ["ax","ay","az","gx","gy","gz","temp","ir","red","green"]}
        grid = ttk.Frame(self, style="Card.TFrame"); grid.pack(fill="x")
        labels = [
            ("Accel (g)", ["ax","ay","az"]),
            ("Gyro (dps)", ["gx","gy","gz"]),
            ("Temp (°C)", ["temp"]),
            ("PPG", ["ir","red","green"]) ,
        ]
        r=0
        for title, keys in labels:
            ttk.Label(grid, text=title, style="Lbl.TLabel").grid(row=r, column=0, sticky="w", pady=(6,0))
            c=1
            for k in keys:
                ttk.Label(grid, text=f"{k}:", style="Lbl.TLabel").grid(row=r, column=c, sticky="e")
                ttk.Label(grid, textvariable=self.vars[k]).grid(row=r, column=c+1, sticky="w")
                c += 2
            r+=1
        for i in range(8):
            grid.columnconfigure(i, weight=1)

        # Subscribe to BLE events
        master.bind("<<BLE:imu>>", self._on_imu)
        master.bind("<<BLE:ppg>>", self._on_ppg)

        # Control buttons
        ctr = ttk.Frame(self, style="Card.TFrame"); ctr.pack(fill="x", pady=(10,0))
        ttk.Button(ctr, text="Start IMU", command=lambda: self.ble.write_line("START_IMU")).pack(side="left")
        ttk.Button(ctr, text="Stop IMU", command=lambda: self.ble.write_line("STOP_IMU")).pack(side="left", padx=6)

    def _on_imu(self, evt):
        # Expect: IMU,ax,ay,az,gx,gy,gz,temp
        try:
            parts = str(evt.data).split(',')
            if parts[0] != 'IMU' or len(parts) < 8:
                return
            keys = ["ax","ay","az","gx","gy","gz","temp"]
            for k,val in zip(keys, parts[1:8]):
                self.vars[k].set(val)
        except Exception:
            pass

    def _on_ppg(self, evt):
        # Expect: PPG,ir,red,green
        try:
            parts = str(evt.data).split(',')
            if parts[0] != 'PPG' or len(parts) < 4:
                return
            keys = ["ir","red","green"]
            for k,val in zip(keys, parts[1:4]):
                self.vars[k].set(val)
        except Exception:
            pass
