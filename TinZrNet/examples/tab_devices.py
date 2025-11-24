# tab_devices.py

import tkinter as tk
from tkinter import ttk
import subprocess
import platform
import threading
from urllib import request, error


def get_default_subnet_prefix():
    """
    Try to auto-detect a reasonable subnet prefix, e.g. '172.20.10.' or '192.168.1.'.
    On Windows, parses 'ipconfig' output for an IPv4 address.
    Falls back to '172.20.10.' if anything fails.
    """
    system = platform.system().lower()
    if system == "windows":
        try:
            result = subprocess.run(
                ["ipconfig"],
                capture_output=True,
                text=True,
                check=False,
            )
            text = result.stdout
            for line in text.splitlines():
                line = line.strip()
                if "IPv4 Address" in line and ":" in line:
                    # Example: "IPv4 Address. . . . . . . . . . . : 172.20.10.4"
                    ip_part = line.split(":", 1)[1].strip()
                    parts = ip_part.split(".")
                    if len(parts) == 4:
                        return ".".join(parts[:3]) + "."
        except Exception:
            pass

    # Fallback
    return "172.20.10."


def extract_hostname_from_html(html):
    """
    Parse the hostname from the HTML served by your current TinZr firmware.

    You showed this structure:

      <p><b>Hostname:</b> TinZr-ota3</p>

    We'll look for 'Hostname:' and grab text until the next '<'.
    """
    marker = "Hostname:"
    idx = html.find(marker)
    if idx == -1:
        return None

    # Substring starting right after "Hostname:"
    after = html[idx + len(marker):]

    # If there's a '>' (end of </b>), skip to after it
    gt = after.find(">")
    if gt != -1:
        after = after[gt + 1:]

    # Hostname ends before next '<'
    lt = after.find("<")
    if lt != -1:
        after = after[:lt]

    hostname = after.strip()
    return hostname or None


class DevicesTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        # last scan result
        self.current_devices = []

        # ---- Top controls ----
        top = tk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)

        left = tk.Frame(top)
        left.pack(side="left", fill="x", expand=True)

        tk.Label(left, text="TinZr Devices Overview", font=("Segoe UI", 11, "bold")).pack(anchor="w")

        tk.Label(
            left,
            text=(
                "Click Refresh to scan the network for TinZr boards.\n"
                "We scan IPs in the subnet below and look for the TinZr Web page.\n"
                "Your firmware serves an HTML page with 'TinZr Device' and Hostname/IP."
            ),
            fg="gray",
            justify="left"
        ).pack(anchor="w", pady=(2, 5))

        # Subnet entry (e.g. 172.20.10.)
        subnet_frame = tk.Frame(left)
        subnet_frame.pack(anchor="w", pady=(2, 0))
        tk.Label(subnet_frame, text="Subnet prefix (e.g. 172.20.10.):").pack(side="left")
        self.subnet_var = tk.StringVar(value=get_default_subnet_prefix())
        tk.Entry(subnet_frame, textvariable=self.subnet_var, width=16).pack(side="left", padx=4)

        # Refresh button + progress
        self.refresh_button = tk.Button(
            top,
            text="Refresh",
            command=self.on_refresh,
            width=10
        )
        self.refresh_button.pack(side="right", padx=5)

        self.progress_label = tk.Label(top, text="", fg="gray")
        self.progress_label.pack(side="right", padx=10)

        # Devices grid area
        self.devices_grid = tk.Frame(self)
        self.devices_grid.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Initial empty view
        self.update_devices_view([])

    # ------------------------------------------------------------------
    # Called by main app (optional – e.g. after Patch & Flash)
    # ------------------------------------------------------------------
    def update_devices_view(self, devices):
        """
        Redraw the devices grid and remember the latest device list.

        Each device dict:
            hostname (str)
            ip       (str, optional)
            battery  (int, optional; 0–100)
            online   (bool|None)
        """
        self.current_devices = devices

        for child in self.devices_grid.winfo_children():
            child.destroy()

        if not devices:
            tk.Label(
                self.devices_grid,
                text="Scan finished: 0 devices online.\n"
                     "• Make sure TinZrs are powered and on the same Wi-Fi\n"
                     "• Make sure the PC is on the same subnet\n"
                     "Then click Refresh again.",
                fg="gray",
                justify="center"
            ).pack(pady=20)
            return

        cols = 3
        for idx, dev in enumerate(devices):
            row = idx // cols
            col = idx % cols

            card = tk.Frame(self.devices_grid, bd=1, relief="solid", padx=8, pady=8)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="n")

            canvas = tk.Canvas(card, width=180, height=110, bg="white", highlightthickness=0)
            canvas.pack()

            online = dev.get("online", None)
            if online is True:
                board_fill = "#e0ffe0"   # green-ish
                status_text = "Online"
                status_fg = "#2e7d32"
            elif online is False:
                board_fill = "#f8f8f8"   # gray
                status_text = "Offline"
                status_fg = "#b71c1c"
            else:
                board_fill = "#e0f0ff"   # bluish / unknown
                status_text = "Unknown"
                status_fg = "#555555"

            # Board body
            canvas.create_rectangle(20, 25, 160, 90, outline="#444", fill=board_fill, width=2)
            # Chips
            canvas.create_rectangle(30, 35, 65, 60, outline="#555", fill="#a0c0ff")
            canvas.create_rectangle(85, 35, 150, 60, outline="#555", fill="#c0d8ff")

            # Battery icon
            bx1, by1, bx2, by2 = 120, 10, 165, 25
            canvas.create_rectangle(bx1, by1, bx2, by2, outline="#444", width=1)
            canvas.create_rectangle(bx2, by1+4, bx2+4, by2-4, outline="#444", fill="#ddd", width=1)

            level = dev.get("battery", 100)
            level = max(0, min(100, level))
            fill_width = (bx2 - bx1 - 2) * (level / 100.0)
            canvas.create_rectangle(
                bx1+1, by1+1, bx1+1+fill_width, by2-1,
                outline="", fill="#4caf50"
            )

            hostname = dev.get("hostname", "TinZr")
            ip = dev.get("ip", "")

            tk.Label(card, text=hostname, font=("Segoe UI", 10, "bold")).pack(pady=(4, 0))
            if ip:
                tk.Label(card, text=ip, font=("Segoe UI", 8), fg="#555555").pack(pady=(0, 2))
            tk.Label(card, text=status_text, font=("Segoe UI", 9, "bold"), fg=status_fg).pack(pady=(2, 0))

        for c in range(cols):
            self.devices_grid.grid_columnconfigure(c, weight=1)

    # ------------------------------------------------------------------
    # Refresh = HTTP-based discovery over IP range
    # ------------------------------------------------------------------
    def on_refresh(self):
        """
        Discover online TinZr devices by scanning IPs:

        - Read subnet prefix from the entry (e.g. '172.20.10.')
        - For host part in [1..254], attempt GET http://<ip>/
        - If HTML contains 'TinZr' and a Hostname line, treat as TinZr device
        """
        if hasattr(self, "_scan_thread") and self._scan_thread.is_alive():
            # Already scanning
            return

        subnet_prefix = self.subnet_var.get().strip()
        if not subnet_prefix or not subnet_prefix.endswith("."):
            tk.messagebox.showerror("TinZr Com Center", "Subnet prefix must look like '172.20.10.'")
            return

        self.refresh_button.config(state="disabled", text="Scanning...")
        self._set_progress("Starting scan...")

        self._scan_thread = threading.Thread(
            target=self._discover_devices_worker,
            args=(subnet_prefix,),
            daemon=True
        )
        self._scan_thread.start()

    def _discover_devices_worker(self, subnet_prefix: str):
        try:
            devices = []

            # Scan host range 1..254
            start_host = 1
            end_host = 254
            total = end_host - start_host + 1
            count = 0

            for host in range(start_host, end_host + 1):
                count += 1
                ip = f"{subnet_prefix}{host}"
                self._set_progress(f"{count}/{total}  (checking {ip})")

                dev = self._probe_ip_for_tinzr(ip)
                if dev is not None:
                    devices.append(dev)

            self.current_devices = devices
            self.after(0, lambda: self.update_devices_view(devices))

        except Exception as e:
            self._set_progress(f"Error during scan: {e}")
        finally:
            self.after(0, self._clear_progress)
            self.after(0, lambda: self.refresh_button.config(state="normal", text="Refresh"))

    # ------------------------------------------------------------------
    # Helpers: HTTP probe + progress label
    # ------------------------------------------------------------------
    def _probe_ip_for_tinzr(self, ip: str):
        """
        Try to GET http://ip/ and see if it looks like your TinZr firmware.

        - Uses standard library urllib.request (no external deps).
        - If HTML contains 'TinZr' and a 'Hostname:' line, returns a device dict.
        """
        url = f"http://{ip}/"
        req = request.Request(url, headers={"User-Agent": "TinZrScanner"})
        try:
            with request.urlopen(req, timeout=0.7) as resp:
                if resp.status != 200:
                    return None
                body = resp.read(1024).decode(errors="ignore")
        except (error.URLError, error.HTTPError, TimeoutError, OSError):
            return None

        # Quick signature check
        if "TinZr" not in body:
            return None

        hostname = extract_hostname_from_html(body)
        if hostname is None:
            hostname = f"TinZr@{ip}"

        return {
            "hostname": hostname,
            "ip": ip,
            "battery": 100,   # placeholder, later we can get real value
            "online": True,
        }

    def _set_progress(self, text):
        self.after(0, lambda: self.progress_label.config(text=text))

    def _clear_progress(self):
        self.after(0, lambda: self.progress_label.config(text=""))
