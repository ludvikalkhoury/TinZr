# =========================
# File: app.py
# =========================
import tkinter as tk
from queue import Queue, Empty
from ble_worker import AsyncBleWorker
from ui.shell import AppShell
from modules.battery import BatteryTab
from modules.led import LedTab
from modules.imu import ImuTab

PUMP_INTERVAL_MS = 30  # how often we poll the BLE->UI queue

def main():
    q = Queue()
    ble = AsyncBleWorker(ui_queue=q)

    app = AppShell(ble)  # AppShell should be a tk.Tk or ttk.Frame with .after/.event_generate

    # Add tabs (modular)
    app.add_tab(BatteryTab(app, ble), "Battery")
    app.add_tab(LedTab(app, ble), "LED")
    app.add_tab(ImuTab(app, ble), "Sensors")

    # Pump BLE queue into Tk virtual events
    def pump_ble_queue():
        try:
            while True:
                kind, payload = q.get_nowait()
                # Forward to the Tk app as virtual events that tabs listen for
                if kind == "bat":
                    app.event_generate("<<BLE:bat>>", when="tail", data=str(payload))
                elif kind == "imu":
                    app.event_generate("<<BLE:imu>>", when="tail", data=str(payload))
                elif kind == "ppg":
                    app.event_generate("<<BLE:ppg>>", when="tail", data=str(payload))
                elif kind == "connected":
                    app.event_generate("<<BLE:connected>>", when="tail", data=str(payload))
                elif kind == "scan_result":
                    app.event_generate("<<BLE:scan>>", when="tail", data=str(payload))
                elif kind == "notify":
                    app.event_generate("<<BLE:notify>>", when="tail", data=str(payload))
                elif kind == "log":
                    # You can route this to a log pane if you have one; fallback to print
                    print(payload)
                # else: ignore unknown kinds
        except Empty:
            pass
        app.after(PUMP_INTERVAL_MS, pump_ble_queue)

    pump_ble_queue()

    # Graceful shutdown: stop BLE worker thread when window closes
    def on_close():
        try:
            ble.stop()
        except Exception:
            pass
        app.destroy()

    if isinstance(app, tk.Tk):
        app.protocol("WM_DELETE_WINDOW", on_close)
    else:
        # If AppShell isn't a Tk, bind to its toplevel
        app.winfo_toplevel().protocol("WM_DELETE_WINDOW", on_close)

    app.mainloop()

if __name__ == "__main__":
    main()
