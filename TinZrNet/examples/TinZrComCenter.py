# TinZrComCenter.py

import tkinter as tk
from tkinter import ttk

from tab_flasher import FlasherTab
from tab_devices import DevicesTab
from tab_wifi_hub import WifiHubTab
from tab_ble_hub import BleHubTab

class TinZrComCenterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TinZr Com Center")

        self.devices = []  # shared mapping: list of dicts {hostname, port, ssid, battery}

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)

        # Create tabs
        self.tab_flasher  = FlasherTab(self.notebook, app=self)
        self.tab_devices  = DevicesTab(self.notebook, app=self)
        self.tab_wifi_hub = WifiHubTab(self.notebook, app=self)   # ✔ FIXED
        self.tab_ble_hub  = BleHubTab(self.notebook, app=self)    # ✔ FIXED

        # Add tabs to notebook
        # self.notebook.add(self.tab_flasher, text="Flasher")
        # self.notebook.add(self.tab_devices, text="Devices")
        self.notebook.add(self.tab_wifi_hub, text="WIFI Hub")
        self.notebook.add(self.tab_ble_hub, text="BLE Hub")        # ✔ FIXED

    def set_devices(self, devices):
        """
        Called by FlasherTab when it knows the mapping of ports -> hostnames.
        Devices is a list of dicts: {hostname, port, ssid, battery}
        """
        self.devices = devices
        self.tab_devices.update_devices_view(devices)


if __name__ == "__main__":
    root = tk.Tk()
    app = TinZrComCenterApp(root)
    root.mainloop()
