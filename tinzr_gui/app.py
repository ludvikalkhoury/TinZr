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

    # ---- Battery: create and mount INLINE on the top-left row (next to search) ----
    battery_tab = BatteryTab(app, ble, icon_width=70, icon_height=25)
    app.attach_battery_inline(battery_tab)     # <— NEW: put the whole battery UI in the top row
    app.battery_tab = battery_tab              # keep handle for direct updates

    # ---- Other tabs (LED / Sensors) still go in the notebook ----
    led_tab = LedTab(app, ble)
    app.add_tab(led_tab, "LED")
    app.led_tab = led_tab

    imu_tab = ImuTab(app, ble)
    app.add_tab(imu_tab, "Sensors")
    app.imu_tab = imu_tab

    # Pump BLE queue into Tk virtual events + direct tab calls
    def pump_ble_queue():
        try:
            while True:
                kind, payload = q.get_nowait()

                if kind == "bat":
                    app.event_generate("<<BLE:bat>>", when="tail", data=str(payload))
                    try:
                        if getattr(app, "battery_tab", None):
                            app.battery_tab.handle_raw_bat(str(payload))
                    except Exception:
                        pass

                elif kind == "bat_val":
                    app.event_generate("<<BLE:bat_val>>", when="tail", data=str(payload))
                    try:
                        if getattr(app, "battery_tab", None):
                            app.battery_tab.handle_bat_val(str(payload))
                    except Exception:
                        pass

                elif kind == "imu":
                    app.event_generate("<<BLE:imu>>", when="tail", data=str(payload))
                    try:
                        if getattr(app, "imu_tab", None):
                            app.imu_tab.handle_imu_line(str(payload))
                    except Exception:
                        pass

                elif kind == "ppg":
                    app.event_generate("<<BLE:ppg>>", when="tail", data=str(payload))
                    try:
                        if getattr(app, "imu_tab", None):
                            app.imu_tab.handle_ppg_line(str(payload))
                    except Exception:
                        pass

                elif kind == "connected":
                    app.event_generate("<<BLE:connected>>", when="tail", data=str(payload))
                    # Immediately request a fresh battery (in case first notify was missed)
                    try:
                        if payload is True or str(payload).lower() == "true":
                            ble.write_line("READ_BAT")
                    except Exception:
                        pass

                elif kind == "scan_result":
                    devices = payload if isinstance(payload, list) else []
                    if hasattr(app, "set_ble_devices") and callable(getattr(app, "set_ble_devices")):
                        try:
                            app.set_ble_devices(devices)
                        except Exception as e:
                            print(f"set_ble_devices error: {e}")
                    app.event_generate("<<BLE:scan>>", when="tail", data=str(devices))

                    # ensure the Scan button stops spinning when results arrive
                    if hasattr(app, "stop_scanning_ui"):
                        try:
                            app.stop_scanning_ui()
                        except Exception:
                            pass

                # Optional lifecycle signals
                elif kind == "scan_start":
                    if hasattr(app, "start_scanning_ui"):
                        try:
                            app.start_scanning_ui()
                        except Exception:
                            pass

                elif kind == "scan_done":
                    if hasattr(app, "stop_scanning_ui"):
                        try:
                            app.stop_scanning_ui()
                        except Exception:
                            pass

                elif kind == "notify":
                    app.event_generate("<<BLE:notify>>", when="tail", data=str(payload))

                elif kind == "log":
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
        app.winfo_toplevel().protocol("WM_DELETE_WINDOW", on_close)

    app.mainloop()

if __name__ == "__main__":
    main()
