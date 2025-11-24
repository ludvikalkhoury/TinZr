import os
os.environ["BLEAK_BACKEND"] = "dotnet"  # needed on Windows for Bleak

import asyncio
import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox

from bleak import BleakScanner, BleakClient

# UUIDs must match your TinZr BLE firmware
TINZR_BLE_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
TINZR_BLE_RX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # node listens here (WRITE)
TINZR_BLE_TX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"  # node notifies here (NOTIFY)

DEVICE_NAME_PREFIX = "TinZr"   # adjust to whatever name you advertise via BLE


class BleHubTab(ttk.Frame):
    """
    BLE Hub that:
      - Scans for TinZr BLE devices
      - Connects to each as central
      - Subscribes to TX characteristic notifications
      - Sends commands by writing to RX characteristic
    """
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        # BLE / hub state
        self.running       = False
        self.ble_thread    = None
        self.loop          = None  # asyncio event loop (in BLE thread)

        # addr -> {"name": str, "client": BleakClient, "last_seen": float, "connected": bool}
        self.peers         = {}
        self.peers_lock    = threading.Lock()

        # Queues for thread-safe UI updates
        self.log_queue     = queue.Queue()
        self.event_queue   = queue.Queue()

        self.build_ui()
        self.after(100, self._poll_queues)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def build_ui(self):
        top = tk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)

        # Left side: controls
        left = tk.Frame(top)
        left.pack(side="left", fill="x", expand=True)

        tk.Label(left, text="TinZr BLE Hub (PC as Central)", font=("Segoe UI", 11, "bold")).pack(anchor="w")

        tk.Label(
            left,
            text=(
                "This PC will:\n"
                "• Scan for TinZr BLE devices\n"
                "• Connect and subscribe to notifications (TX char)\n"
                "• Track peers by BLE address\n"
                "• Send commands over BLE (write to RX char)"
            ),
            justify="left", fg="gray"
        ).pack(anchor="w", pady=(2, 5))

        btn_row = tk.Frame(left)
        btn_row.pack(anchor="w", pady=(8, 0))

        self.start_button = tk.Button(btn_row, text="Start BLE Hub", command=self.on_start_hub)
        self.start_button.pack(side="left", padx=(0, 5))

        self.stop_button = tk.Button(btn_row, text="Stop", command=self.on_stop_hub, state="disabled")
        self.stop_button.pack(side="left")

        self.status_label = tk.Label(left, text="BLE Hub stopped", fg="gray")
        self.status_label.pack(anchor="w", pady=(4, 0))

        # Right side: peer list
        right = tk.Frame(top)
        right.pack(side="right", fill="y", padx=(20, 0))

        tk.Label(right, text="Known BLE TinZr Peers:").pack(anchor="w")
        self.peers_list = tk.Listbox(right, height=10, width=40)
        self.peers_list.pack(fill="y", expand=False)

        # Bottom: log + send area
        bottom = tk.Frame(self)
        bottom.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Log
        log_frame = tk.Frame(bottom)
        log_frame.pack(fill="both", expand=True)

        tk.Label(log_frame, text="BLE Hub Log:").pack(anchor="w")
        self.text_log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.text_log.pack(fill="both", expand=True)

        # Send area
        send_frame = tk.Frame(self)
        send_frame.pack(fill="x", padx=10, pady=(0, 10))

        tk.Label(send_frame, text="Send message (e.g. 'LED 0 255 0 20', 'OFF', 'PING'):").pack(anchor="w")
        self.send_entry = tk.Entry(send_frame)
        self.send_entry.pack(side="left", fill="x", expand=True)
        self.send_entry.bind("<Return>", lambda e: self.on_send_all())

        tk.Button(send_frame, text="Send to all", command=self.on_send_all).pack(side="left", padx=4)
        tk.Button(send_frame, text="Send to selected", command=self.on_send_selected).pack(side="left", padx=4)

    # ------------------------------------------------------------------
    # Hub lifecycle
    # ------------------------------------------------------------------
    def on_start_hub(self):
        if self.running:
            return

        self.running = True
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self._set_status("BLE Hub running (scanning + connecting)...", "green")
        self._log("[BLE HUB] Starting BLE hub thread...")

        self.ble_thread = threading.Thread(target=self._ble_thread_main, daemon=True)
        self.ble_thread.start()

    def on_stop_hub(self):
        if not self.running:
            return

        self._log("[BLE HUB] Stopping BLE hub...")
        self.running = False

        # Ask the event loop to close all clients and then stop
        if self.loop is not None:
            async def _shutdown():
                self._log("[BLE HUB] Shutting down BLE clients...")
                with self.peers_lock:
                    peers_copy = dict(self.peers)
                for addr, info in peers_copy.items():
                    client = info.get("client")
                    if client is not None and client.is_connected:
                        try:
                            await client.disconnect()
                            self._log(f"[BLE HUB] Disconnected {addr}")
                        except Exception as e:
                            self._log(f"[BLE HUB] Error disconnecting {addr}: {e}")
                # allow loop to stop
            try:
                asyncio.run_coroutine_threadsafe(_shutdown(), self.loop)
            except RuntimeError:
                pass  # loop already closing/closed

        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self._set_status("BLE Hub stopped", "gray")

    def _ble_thread_main(self):
        """
        BLE thread entry: create and run an asyncio loop for Bleak.
        """
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._ble_main_loop())
        except Exception as e:
            self._log(f"[BLE HUB] Exception in BLE loop: {e}")
        finally:
            try:
                self.loop.stop()
            except Exception:
                pass

    async def _ble_main_loop(self):
        """
        Main BLE loop: periodically scan and connect to any TinZr devices.
        """
        self._log("[BLE HUB] BLE main loop started.")
        while self.running:
            try:
                self._log("[BLE HUB] Scanning for TinZr devices...")
                devices = await BleakScanner.discover(timeout=4.0)

                for dev in devices:
                    name = dev.name or ""
                    if not name:
                        continue

                    # Filter by device name prefix or service, adjust as needed
                    if not name.startswith(DEVICE_NAME_PREFIX):
                        continue

                    addr = dev.address
                    with self.peers_lock:
                        info = self.peers.get(addr)

                    if info and info.get("connected"):
                        # already connected, just update last seen
                        self._learn_peer(addr, name=name)
                        continue

                    # New or disconnected device → try to connect
                    self._log(f"[BLE HUB] Found {name} [{addr}], attempting to connect...")
                    client = BleakClient(addr)

                    try:
                        await client.connect(timeout=10.0)
                    except Exception as e:
                        self._log(f"[BLE HUB] Failed to connect to {addr}: {e}")
                        continue

                    if not client.is_connected:
                        self._log(f"[BLE HUB] Connection to {addr} not established.")
                        continue

                    self._log(f"[BLE HUB] Connected to {name} [{addr}]")

                    # Subscribe to TX notifications
                    async def _start_notify():
                        try:
                            await client.start_notify(TINZR_BLE_TX_CHAR_UUID,
                                                      lambda sender, data, a=addr: self._handle_notification(a, data))
                            self._log(f"[BLE HUB] Subscribed to TX notifications for {addr}")
                        except Exception as e:
                            self._log(f"[BLE HUB] Failed to subscribe TX for {addr}: {e}")

                    await _start_notify()

                    with self.peers_lock:
                        self.peers[addr] = {
                            "name": name,
                            "client": client,
                            "last_seen": time.time(),
                            "connected": True,
                        }
                    self._update_peers_list()

                # Short idle before next scan
                await asyncio.sleep(5.0)

            except Exception as e:
                self._log(f"[BLE HUB] Error in main BLE loop: {e}")
                await asyncio.sleep(3.0)

        self._log("[BLE HUB] BLE main loop exiting.")

    # ------------------------------------------------------------------
    # Notifications / peers
    # ------------------------------------------------------------------
    def _handle_notification(self, addr: str, data: bytes):
        """
        Called in BLE thread when a TinZr device sends a notification on TX char.
        """
        ts = time.strftime("%H:%M:%S")
        try:
            text = data.decode("utf-8", errors="replace").strip()
        except Exception:
            text = repr(data)
        self._log(f"[BLE RX {addr} @ {ts}] {text}")
        self._learn_peer(addr)

    def _learn_peer(self, addr: str, name: str | None = None):
        with self.peers_lock:
            entry = self.peers.get(addr, {"last_seen": time.time(), "name": None, "client": None, "connected": False})
            entry["last_seen"] = time.time()
            if name is not None:
                entry["name"] = name
            self.peers[addr] = entry
        self._update_peers_list()

    # ------------------------------------------------------------------
    # Sending commands over BLE
    # ------------------------------------------------------------------
    def _ble_write(self, addr: str, data: bytes):
        """
        Schedule a BLE write to RX characteristic on the BLE loop.
        """
        with self.peers_lock:
            info = self.peers.get(addr)
        if not info:
            self._log(f"[BLE SEND] No peer known with addr {addr}")
            return

        client = info.get("client")
        if client is None or not client.is_connected:
            self._log(f"[BLE SEND] Client for {addr} not connected.")
            return

        if self.loop is None:
            self._log("[BLE SEND] BLE loop not running.")
            return

        async def _do_write():
            try:
                await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, data)
            except Exception as e:
                self._log(f"[BLE SEND] Error writing to {addr}: {e}")

        try:
            asyncio.run_coroutine_threadsafe(_do_write(), self.loop)
        except RuntimeError as e:
            self._log(f"[BLE SEND] Loop not accepting tasks: {e}")

    def on_send_all(self):
        text = self.send_entry.get().strip()
        if not text:
            return
        data = (text + "\n").encode("utf-8")

        with self.peers_lock:
            addrs = list(self.peers.keys())

        for addr in addrs:
            self._ble_write(addr, data)

        self._log(f"[PC→ALL/BLE] {data!r}")

    def on_send_selected(self):
        sel = self.peers_list.curselection()
        if not sel:
            return
        idx = sel[0]
        with self.peers_lock:
            addrs = list(self.peers.keys())
        if idx < 0 or idx >= len(addrs):
            return
        addr = addrs[idx]

        text = self.send_entry.get().strip()
        if not text:
            return
        data = (text + "\n").encode("utf-8")

        self._ble_write(addr, data)
        self._log(f"[PC→{addr}/BLE] {data!r}")

    # ------------------------------------------------------------------
    # UI update helpers (thread-safe via queues)
    # ------------------------------------------------------------------
    def _log(self, msg: str):
        self.log_queue.put(str(msg))

    def _set_status(self, text: str, color: str):
        self.event_queue.put(("status", text, color))

    def _update_peers_list(self):
        self.event_queue.put(("peers", None, None))

    def _poll_queues(self):
        # Logs
        while not self.log_queue.empty():
            msg = self.log_queue.get()
            self.text_log.config(state="normal")
            self.text_log.insert("end", msg + "\n")
            self.text_log.see("end")
            self.text_log.config(state="disabled")

        # Events
        while not self.event_queue.empty():
            ev = self.event_queue.get()
            kind = ev[0]
            if kind == "status":
                text, color = ev[1], ev[2]
                self.status_label.config(text=text, fg=color)
            elif kind == "peers":
                self.peers_list.delete(0, "end")
                now = time.time()
                with self.peers_lock:
                    for addr, info in self.peers.items():
                        age = now - info["last_seen"]
                        name = info.get("name") or "TinZr"
                        label = f"{name} [{addr}]  (seen {age:0.1f}s ago)"
                        self.peers_list.insert("end", label)

        self.after(100, self._poll_queues)
