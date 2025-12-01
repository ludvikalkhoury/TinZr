# tab_ble_hub.py  (PyQt5 version of BleHubTab)

import os
import sys
os.environ["BLEAK_BACKEND"] = "dotnet"  # needed on Windows for Bleak

import asyncio
import threading
import queue
import time

from PyQt5 import QtCore, QtWidgets

from bleak import BleakScanner, BleakClient

try:
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    PARENT_DIR = os.path.dirname(CURRENT_DIR)
    if PARENT_DIR not in sys.path:
        sys.path.insert(0, PARENT_DIR)
        
    from GUIsHelper import apply_tinzr_theme
except ImportError:
    apply_tinzr_theme = None


# UUIDs must match your TinZr BLE firmware
TINZR_BLE_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
TINZR_BLE_RX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # node listens here (WRITE)
TINZR_BLE_TX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"  # node notifies here (NOTIFY)

DEVICE_NAME_PREFIX = "TinZr"   # adjust to whatever name you advertise via BLE


class BleHubTab(QtWidgets.QWidget):
    """
    BLE Hub that:
      - Scans for TinZr BLE devices
      - Connects to each as central
      - Subscribes to TX characteristic notifications
      - Sends commands by writing to RX characteristic

    Logic is identical to the original Tkinter version; GUI toolkit only is changed.
    """

    def __init__(self, app=None, parent=None):
        super().__init__(parent)
        self.app = app

        # BLE / hub state
        self.running = False
        self.ble_thread = None
        self.loop = None  # asyncio event loop (in BLE thread)

        # addr -> {"name": str, "client": BleakClient, "last_seen": float, "connected": bool}
        self.peers = {}
        self.peers_lock = threading.Lock()

        # Queues for thread-safe UI updates
        self.log_queue = queue.Queue()
        self.event_queue = queue.Queue()

        self._build_ui()

        # timer to poll queues (like Tk .after)
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.timeout.connect(self._poll_queues)
        self._poll_timer.start(100)

        if apply_tinzr_theme is not None:
            apply_tinzr_theme(self)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(20)
        main_layout.addWidget(top)

        # Left side: controls
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        top_layout.addWidget(left, stretch=1)

        title = QtWidgets.QLabel("TinZr BLE Hub")
        font = title.font()
        font.setPointSize(7)
        font.setBold(True)
        title.setFont(font)
        left_layout.addWidget(title)

        desc = QtWidgets.QLabel(
            "This PC will:\n"
            "• Scan for TinZr BLE devices\n"
            "• Connect and subscribe to notifications (TX char)\n"
            "• Track peers by BLE address\n"
            "• Send commands over BLE (write to RX char)"
        )
        desc.setWordWrap(True)
        font_desc = desc.font()
        font_desc.setPointSize(7)
        desc.setFont(font_desc)
        desc.setStyleSheet("""
            QLabel {
                color: gray;
                font-size: 10pt;
            }
        """)
        left_layout.addWidget(desc)

        btn_row = QtWidgets.QWidget()
        btn_layout = QtWidgets.QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)
        left_layout.addWidget(btn_row)

        self.start_button = QtWidgets.QPushButton("Start Hub")
        self.start_button.clicked.connect(self.on_start_hub)
        btn_layout.addWidget(self.start_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.on_stop_hub)
        btn_layout.addWidget(self.stop_button)

        btn_layout.addStretch(1)

        self.status_label = QtWidgets.QLabel("Hub stopped")
        self.status_label.setStyleSheet("color: gray;")
        left_layout.addWidget(self.status_label)

        left_layout.addStretch(1)

        # Right side: peer list
        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        top_layout.addWidget(right, stretch=1)

        right_layout.addWidget(QtWidgets.QLabel("Known TinZr Peers:"))
        self.peers_list = QtWidgets.QListWidget()
        self.peers_list.setMinimumWidth(260)
        right_layout.addWidget(self.peers_list)

        # Bottom: log + send area
        log_group = QtWidgets.QGroupBox("Hub Log:")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.text_log = QtWidgets.QPlainTextEdit()
        self.text_log.setReadOnly(True)
        log_layout.addWidget(self.text_log)
        main_layout.addWidget(log_group, stretch=1)

        # Send area
        send_frame = QtWidgets.QWidget()
        send_layout = QtWidgets.QHBoxLayout(send_frame)
        send_layout.setContentsMargins(0, 0, 0, 0)
        send_layout.setSpacing(6)

        label = QtWidgets.QLabel("Send message (e.g. 'LED 0 255 0 20', 'OFF', 'PING'):")
        main_layout.addWidget(label)

        self.send_entry = QtWidgets.QLineEdit()
        self.send_entry.returnPressed.connect(self.on_send_selected)
        send_layout.addWidget(self.send_entry, stretch=1)

        # NEW: Send to all
        btn_all = QtWidgets.QPushButton("Send to all")
        btn_all.clicked.connect(self.on_send_all)
        send_layout.addWidget(btn_all)

        btn_sel = QtWidgets.QPushButton("Send to selected")
        btn_sel.clicked.connect(self.on_send_selected)
        send_layout.addWidget(btn_sel)

        main_layout.addWidget(send_frame)

    # ------------------------------------------------------------------
    # Hub lifecycle
    # ------------------------------------------------------------------
    def on_start_hub(self):
        if self.running:
            return

        self.running = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_status("BLE hub running (scanning and connecting).", "green")
        self._log("[BLE] Starting BLE hub...")

        self.loop = asyncio.new_event_loop()
        self.ble_thread = threading.Thread(target=self._ble_thread_main, daemon=True)
        self.ble_thread.start()

    def on_stop_hub(self):
        if not self.running:
            return

        self._log("[BLE] Stopping BLE hub...")
        self.running = False

        if self.loop is not None:
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._set_status("BLE Hub stopped", "gray")

    # ------------------------------------------------------------------
    # BLE thread / loop
    # ------------------------------------------------------------------
    def _ble_thread_main(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._ble_loop())

    async def _ble_loop(self):
        """
        Main BLE loop: repeatedly scan, connect to new devices, and keep connections alive.
        """
        self._log("[BLE] BLE loop started.")

        while self.running:
            try:
                devices = await BleakScanner.discover(timeout=3.0)
            except Exception as e:
                self._log(f"[BLE] Scan error: {e}")
                await asyncio.sleep(2.0)
                continue

            for d in devices:
                if not self.running:
                    break
                if not d.name or not d.name.startswith(DEVICE_NAME_PREFIX):
                    continue

                addr = d.address
                name = d.name
                with self.peers_lock:
                    peer = self.peers.get(addr)
                if peer and peer.get("connected"):
                    # Already connected; just update last_seen
                    self._mark_peer_seen(addr)
                    continue

                # Need to connect
                self._log(f"[BLE] Found candidate {name} ({addr}), connecting...")
                try:
                    client = BleakClient(addr)
                    await client.connect()
                    if not client.is_connected:
                        self._log(f"[BLE] Failed to connect to {addr}")
                        continue

                    # Start notifications
                    await client.start_notify(
                        TINZR_BLE_TX_CHAR_UUID,
                        self._notification_handler
                    )

                    with self.peers_lock:
                        self.peers[addr] = {
                            "name": name,
                            "client": client,
                            "last_seen": time.time(),
                            "connected": True,
                        }
                    self._log(f"[BLE] Connected to {name} ({addr})")
                    self._update_peers_list()
                except Exception as e:
                    self._log(f"[BLE] Error connecting to {addr}: {e}")
                    # ensure we disconnect if partially connected
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

            # While running, periodically refresh last_seen / keep alive
            await asyncio.sleep(3.0)

        # Clean shutdown: disconnect all clients
        self._log("[BLE] Shutting down, disconnecting peers...")
        with self.peers_lock:
            peers_copy = list(self.peers.items())
        for addr, info in peers_copy:
            client = info.get("client")
            if client:
                try:
                    await client.stop_notify(TINZR_BLE_TX_CHAR_UUID)
                except Exception:
                    pass
                try:
                    await client.disconnect()
                except Exception:
                    pass
        self._log("[BLE] BLE loop exited.")

    # ------------------------------------------------------------------
    # Notification handler
    # ------------------------------------------------------------------
    def _notification_handler(self, handle, data: bytes):
        """
        Runs in BLE loop thread when TX notifications arrive.
        """
        text = data.decode("utf-8", errors="replace").strip()
        self._log(f"[BLE RX] {text!r}")
        # We don't have the addr directly here; many designs add a wrapper,
        # but for now we just log the text. You can extend this later to map handle→addr.

    # ------------------------------------------------------------------
    # Sending commands
    # ------------------------------------------------------------------
    def on_send_selected(self):
        msg = self.send_entry.text().strip()
        if not msg:
            return
        data = msg.encode("utf-8")

        selected_items = self.peers_list.selectedItems()
        if not selected_items:
            self._log("[BLE SEND] No peer selected.")
            return

        with self.peers_lock:
            peers_copy = dict(self.peers)

        async def _send_cmd():
            for item in selected_items:
                addr = item.data(QtCore.Qt.UserRole)
                if not addr:
                    continue
                info = peers_copy.get(addr)
                if not info:
                    continue
                client: BleakClient = info.get("client")
                if not client or not client.is_connected:
                    self._log(f"[BLE SEND] {addr} not connected.")
                    continue
                try:
                    await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, data)
                    self._log(f"[BLE SEND] -> {addr}: {msg!r}")
                except Exception as e:
                    self._log(f"[BLE SEND] Failed to send to {addr}: {e}")

        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(_send_cmd(), self.loop)
        else:
            self._log("[BLE SEND] Loop not running; cannot send.")

    def on_send_all(self):
        """
        Send the command in the entry to ALL known connected BLE peers.
        """
        msg = self.send_entry.text().strip()
        if not msg:
            return
        data = msg.encode("utf-8")

        with self.peers_lock:
            peers_copy = dict(self.peers)

        if not peers_copy:
            self._log("[BLE SEND] No peers to send to.")
            return

        async def _send_cmd_all():
            for addr, info in peers_copy.items():
                client: BleakClient = info.get("client")
                if not client or not client.is_connected:
                    self._log(f"[BLE SEND] {addr} not connected.")
                    continue
                try:
                    await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, data)
                    self._log(f"[BLE SEND] -> {addr}: {msg!r}")
                except Exception as e:
                    self._log(f"[BLE SEND] Failed to send to {addr}: {e}")

        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(_send_cmd_all(), self.loop)
        else:
            self._log("[BLE SEND] Loop not running; cannot send.")

    # ------------------------------------------------------------------
    # Peer tracking & UI
    # ------------------------------------------------------------------
    def _mark_peer_seen(self, addr: str):
        with self.peers_lock:
            info = self.peers.get(addr)
            if info:
                info["last_seen"] = time.time()
        self._update_peers_list()

    def _update_peers_list(self):
        self.event_queue.put(("peers_updated", None))

    def _poll_queues(self):
        # Logs
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.text_log.appendPlainText(msg)
            self.text_log.verticalScrollBar().setValue(
                self.text_log.verticalScrollBar().maximum()
            )

        # Events
        while True:
            try:
                ev, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if ev == "peers_updated":
                self._refresh_peers_list()

    def _refresh_peers_list(self):
        self.peers_list.clear()
        with self.peers_lock:
            for addr, info in sorted(self.peers.items()):
                name = info.get("name") or addr
                last_seen = info.get("last_seen", 0.0)
                age = time.time() - last_seen
                label = f"{name} [{addr}] ({age:.1f}s ago)"
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, addr)
                self.peers_list.addItem(item)

    def _log(self, msg: str):
        self.log_queue.put(msg)

    def _set_status(self, text: str, color: str = "gray"):
        self.status_label.setText(text)
        if color == "green":
            css = "color: #56d364;"
        elif color == "red":
            css = "color: #ff6b6b;"
        else:
            css = "color: gray;"
        self.status_label.setStyleSheet(css)
