import os
import math
from PyQt5 import QtGui

os.environ["BLEAK_BACKEND"] = "dotnet"  # important on Windows

import asyncio
import sys
import time
import struct
import threading
import numpy as np

from bleak import BleakScanner, BleakClient
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

# ================== BLE UUIDs & Device Filter ==================
TINZR_BLE_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
TINZR_BLE_RX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
TINZR_BLE_TX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"

DEVICE_PREFIX = "TinZr"

# Optional commands if your firmware supports them (adjust as needed)
CMD_START = b"S"   # example
CMD_STOP  = b"E"   # example

# ================== Frame format (must match C++) ==============
# struct WearFrame {
#   int16_t ax, ay, az;
#   int16_t gx, gy, gz;
#   uint32_t red;
#   uint32_t ir;
# } __attribute__((packed));
#
# => little-endian: <hhhhhhII
FRAME_STRUCT = struct.Struct("<hhhhhhII")
FRAME_SIZE   = FRAME_STRUCT.size  # 20 bytes

# accel & gyro scales (inverse of firmware scaling)
ACC_SCALE = 1e-3   # milli-units -> m/s^2
GYR_SCALE = 1e-3   # milli-units -> rad/s (or dps-ish)

# ================== Viewer Config ==================
FS_RESAMP_HZ    = 240.0   # desired *fixed* rate
WINDOW_SEC      = 3.0     # seconds visible on screen
UPDATE_MS       = 10      # GUI update period in ms
MAX_RAW_SAMPLES = 20000   # safety cap on raw buffer size


# ================== Spinner Widget ==================
class Spinner(QtWidgets.QWidget):
    def __init__(self, radius=8, line_width=2, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._radius = radius
        self._line_width = line_width

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._on_timeout)

        size = radius * 4
        self.setFixedSize(size, size)
        self.setVisible(False)

    def start(self):
        if not self._timer.isActive():
            self._timer.start(1000 // 24)  # ~24 FPS
            self.setVisible(True)

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()
        self.setVisible(False)

    def _on_timeout(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        if not self.isVisible():
            return

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        center = self.rect().center()
        radius = self._radius

        pen = QtGui.QPen(self.palette().highlight().color(), self._line_width)
        p.setPen(pen)

        # 12 segments around the circle, fading tail
        for i in range(12):
            alpha = int(255 * (i + 1) / 12)
            color = pen.color()
            color.setAlpha(alpha)
            pen.setColor(color)
            p.setPen(pen)

            angle_deg = self._angle + i * 30
            angle = math.radians(angle_deg)

            x1 = center.x() + math.cos(angle) * (radius * 0.4)
            y1 = center.y() + math.sin(angle) * (radius * 0.4)
            x2 = center.x() + math.cos(angle) * radius
            y2 = center.y() + math.sin(angle) * radius

            p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))


# ================== Main Viewer ==================
class WearableViewer(QtWidgets.QWidget):
    scan_finished = QtCore.pyqtSignal(object, object)  # (devices, error)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("TinZrWearable – resampled to fixed Fs")
        main_layout = QtWidgets.QVBoxLayout(self)

        # ================== CONTROL PANEL ==================
        ctrl_widget = QtWidgets.QWidget()
        ctrl_layout = QtWidgets.QGridLayout(ctrl_widget)

        row = 0

        # Find/scan devices
        self.btn_scan = QtWidgets.QPushButton("Find Devices")
        self.btn_scan.clicked.connect(self.on_scan_clicked)
        ctrl_layout.addWidget(self.btn_scan, row, 0)

        # Spinner next to the button
        self.spinner = Spinner(radius=8, line_width=2)
        ctrl_layout.addWidget(
            self.spinner,
            row,
            0,
            alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
        )

        # Device combo box
        self.combo_devices = QtWidgets.QComboBox()
        ctrl_layout.addWidget(self.combo_devices, row, 1, 1, 3)

        row += 1

        # Connect / Disconnect
        self.btn_connect = QtWidgets.QPushButton("Connect")
        self.btn_connect.clicked.connect(self.on_connect_clicked)
        ctrl_layout.addWidget(self.btn_connect, row, 0)

        self.btn_disconnect = QtWidgets.QPushButton("Disconnect")
        self.btn_disconnect.clicked.connect(self.on_disconnect_clicked)
        self.btn_disconnect.setEnabled(False)
        ctrl_layout.addWidget(self.btn_disconnect, row, 1)

        # Start / Stop data streaming (from our point of view)
        self.btn_start = QtWidgets.QPushButton("Start Data")
        self.btn_start.clicked.connect(self.on_start_data_clicked)
        self.btn_start.setEnabled(False)
        ctrl_layout.addWidget(self.btn_start, row, 2)

        self.btn_stop = QtWidgets.QPushButton("Stop Data")
        self.btn_stop.clicked.connect(self.on_stop_data_clicked)
        self.btn_stop.setEnabled(False)
        ctrl_layout.addWidget(self.btn_stop, row, 3)

        row += 1

        # Recording buttons
        self.btn_rec_start = QtWidgets.QPushButton("Start Recording")
        self.btn_rec_start.clicked.connect(self.on_start_recording_clicked)
        self.btn_rec_start.setEnabled(False)
        ctrl_layout.addWidget(self.btn_rec_start, row, 0)

        self.btn_rec_stop = QtWidgets.QPushButton("Stop Recording")
        self.btn_rec_stop.clicked.connect(self.on_stop_recording_clicked)
        self.btn_rec_stop.setEnabled(False)
        ctrl_layout.addWidget(self.btn_rec_stop, row, 1)

        # Status label
        self.label_status = QtWidgets.QLabel("Status: Idle")
        ctrl_layout.addWidget(self.label_status, row, 2, 1, 2)

        main_layout.addWidget(ctrl_widget)

        # ================== Graphics Layout ==================
        self.graphics = pg.GraphicsLayoutWidget()
        main_layout.addWidget(self.graphics)
        self.graphics.setBackground('k')

        # Only signals we actually use
        self.signal_order = [
            "red", "ir",
            "ax", "ay", "az",
            "gx", "gy", "gz",
        ]

        colors = {
            "red": "#ff4444",
            "ir":  "#aaaaaa",

            "ax": "#448aff",
            "ay": "#448aff",
            "az": "#448aff",

            "gx": "#33ffff",
            "gy": "#33ffff",
            "gz": "#33ffff",
        }

        self.axes   = {}
        self.curves = {}

        for row, key in enumerate(self.signal_order):
            p = self.graphics.addPlot(row=row, col=0)

            # No grid
            p.showGrid(x=False, y=False)

            # LEFT AXIS: only the label text (key), no numbers, no units
            left_axis = p.getAxis("left")
            left_axis.enableAutoSIPrefix(False)
            left_axis.setTicks([])
            left_axis.setStyle(showValues=False, tickLength=0)
            left_axis.setLabel(text=key, units="", unitPrefix="")

            # BOTTOM AXIS: only on last row, and also no numbers
            if row < len(self.signal_order) - 1:
                p.showAxis("bottom", False)
            else:
                p.showAxis("bottom", True)
                bottom_axis = p.getAxis("bottom")
                bottom_axis.enableAutoSIPrefix(False)
                bottom_axis.setTicks([])
                bottom_axis.setStyle(showValues=False, tickLength=0)
                bottom_axis.setLabel(text="Time (s)", units="", unitPrefix="")

            curve = p.plot([], [], pen=pg.mkPen(colors[key], width=1.4))
            self.axes[key] = p
            self.curves[key] = curve

        # ================== Buffers ==================
        self.sample_count = 0            # raw sample index from firmware (1,2,3,...)
        self.idx_raw = []                # indices
        self.data_raw = {k: [] for k in self.signal_order}

        # For resampled data (at FS_RESAMP_HZ) if you ever want to use it later
        self.t_resamp = None
        self.data_resamp = {k: None for k in self.signal_order}

        # Raw byte buffer for assembling frames (in case BLE fragments)
        self.byte_buf = bytearray()

        # ===== Fs auto-calibration =====
        self.fs_est = None
        self.calib_start_time = None
        self.calib_start_count = None
        self.CALIB_SAMPLES = 1000   # ~4 s at ~250 Hz

        # Streaming and recording flags
        self.streaming = False
        self.recording = False
        self.record_file = None

        # BLE stuff
        self.loop = asyncio.new_event_loop()
        self.client = None
        self.devices = []   # list of (name, address)

        # signal from BLE thread back to GUI
        self.scan_finished.connect(self._handle_scan_result)

        # Start BLE event loop in a background thread
        self.ble_thread = threading.Thread(target=self.run_ble_loop, daemon=True)
        self.ble_thread.start()

        # GUI timer for plotting
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(UPDATE_MS)

    # ============ BLE loop thread ============
    def run_ble_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    # ============ UI helpers ============
    def set_status(self, text):
        self.label_status.setText(f"Status: {text}")

    # ============ Scan logic ============
    def on_scan_clicked(self):
        """Scan for devices whose name starts with DEVICE_PREFIX."""
        if not self.loop.is_running():
            self.set_status("BLE loop not running")
            return

        self.set_status("Scanning...")
        self.combo_devices.clear()
        self.devices = []
        self.btn_scan.setEnabled(False)
        self.spinner.start()

        async def _scan():
            devs = await BleakScanner.discover(timeout=4.0)
            out = []
            for d in devs:
                if d.name and d.name.startswith(DEVICE_PREFIX):
                    out.append((d.name, d.address))
            return out

        fut = asyncio.run_coroutine_threadsafe(_scan(), self.loop)

        def _done_callback(f):
            try:
                devs = f.result()
                err = None
            except Exception as e:
                devs = None
                err = e
            # signal back to GUI thread
            self.scan_finished.emit(devs, err)

        fut.add_done_callback(_done_callback)

    def _handle_scan_result(self, devs, err):
        # called in GUI thread thanks to pyqtSignal
        self.spinner.stop()
        self.btn_scan.setEnabled(True)

        if err is not None:
            self.set_status(f"Scan error: {err}")
            return

        if not devs:
            self.set_status("No TinZr devices found")
            return

        self.devices = devs
        self.combo_devices.clear()
        for name, addr in devs:
            self.combo_devices.addItem(f"{name} ({addr})", addr)

        self.set_status(f"Found {len(devs)} device(s)")

    # ============ Connect / Disconnect ============
    def on_connect_clicked(self):
        """Connect to the selected device and subscribe to notifications."""
        if not self.loop.is_running():
            self.set_status("BLE loop not running")
            return

        if self.client and self.client.is_connected:
            self.set_status("Already connected")
            return

        addr = self.combo_devices.currentData()
        if not addr:
            self.set_status("No device selected")
            return

        self.set_status(f"Connecting to {addr}...")

        async def _connect():
            client = BleakClient(addr)
            await client.connect()
            if not client.is_connected:
                raise RuntimeError("Failed to connect")
            # Subscribe to notifications
            await client.start_notify(TINZR_BLE_TX_CHAR_UUID, self.on_rx)
            return client

        fut = asyncio.run_coroutine_threadsafe(_connect(), self.loop)
        try:
            client = fut.result()
        except Exception as e:
            self.set_status(f"Connect error: {e}")
            return

        self.client = client
        self.set_status("Connected")
        self.btn_disconnect.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_scan.setEnabled(False)

    def on_disconnect_clicked(self):
        if not self.loop.is_running():
            self.set_status("BLE loop not running")
            return

        if not self.client or not self.client.is_connected:
            self.set_status("No active connection")
            return

        self.set_status("Disconnecting...")

        async def _disc():
            try:
                await self.client.stop_notify(TINZR_BLE_TX_CHAR_UUID)
            except Exception:
                pass
            await self.client.disconnect()

        fut = asyncio.run_coroutine_threadsafe(_disc(), self.loop)
        try:
            fut.result()
        except Exception as e:
            self.set_status(f"Disconnect error: {e}")
            return

        self.client = None
        self.streaming = False
        self.btn_disconnect.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_rec_start.setEnabled(False)
        self.btn_rec_stop.setEnabled(False)
        self.btn_scan.setEnabled(True)
        self.set_status("Disconnected")

    # ============ Start / Stop Data ============
    def on_start_data_clicked(self):
        """Clear buffers and start using incoming data."""
        if not self.client or not self.client.is_connected:
            self.set_status("Not connected")
            return

        # If your firmware needs an explicit start command, uncomment:
        # if CMD_START:
        #     fut = asyncio.run_coroutine_threadsafe(
        #         self.client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_START),
        #         self.loop
        #     )
        #     try:
        #         fut.result()
        #     except Exception as e:
        #         self.set_status(f"Start cmd error: {e}")
        #         return

        # reset buffers & Fs calibration
        self.sample_count = 0
        self.idx_raw.clear()
        for k in self.data_raw:
            self.data_raw[k].clear()

        self.fs_est = None
        self.calib_start_time = None
        self.calib_start_count = None

        self.streaming = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_rec_start.setEnabled(True)
        self.set_status("Streaming (waiting for Fs calibration)")

    def on_stop_data_clicked(self):
        if not self.streaming:
            return

        # If your firmware needs an explicit stop command, uncomment:
        # if CMD_STOP and self.client and self.client.is_connected:
        #     fut = asyncio.run_coroutine_threadsafe(
        #         self.client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_STOP),
        #         self.loop
        #     )
        #     try:
        #         fut.result()
        #     except Exception as e:
        #         self.set_status(f"Stop cmd error: {e}")

        self.streaming = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_rec_start.setEnabled(False)
        # also stop recording if it was running
        if self.recording:
            self.on_stop_recording_clicked()
        self.set_status("Streaming stopped")

    # ============ Recording ============
    def on_start_recording_clicked(self):
        if not self.streaming:
            self.set_status("Start data before recording")
            return

        if self.recording:
            self.set_status("Already recording")
            return

        fname, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Recording",
            "tinzr_recording.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not fname:
            return

        try:
            self.record_file = open(fname, "w", buffering=1)  # line buffered
        except Exception as e:
            self.set_status(f"File error: {e}")
            self.record_file = None
            return

        # header
        self.record_file.write("sample,ax,ay,az,gx,gy,gz,red,ir\n")
        self.recording = True
        self.btn_rec_start.setEnabled(False)
        self.btn_rec_stop.setEnabled(True)
        self.set_status(f"Recording to: {fname}")

    def on_stop_recording_clicked(self):
        if not self.recording:
            return

        self.recording = False
        if self.record_file is not None:
            try:
                self.record_file.close()
            except Exception:
                pass
            self.record_file = None

        self.btn_rec_start.setEnabled(self.streaming)
        self.btn_rec_stop.setEnabled(False)
        self.set_status("Recording stopped")

    # ============ BLE Notification Handler ============
    def on_rx(self, handle, data: bytes):
        """Incoming BLE notifications (BINARY). Runs in BLE thread."""
        if not self.streaming:
            return

        self.byte_buf.extend(data)

        n_bytes = len(self.byte_buf)
        n_frames = n_bytes // FRAME_SIZE
        if n_frames == 0:
            return

        for i in range(n_frames):
            start = i * FRAME_SIZE
            chunk = self.byte_buf[start:start + FRAME_SIZE]
            ax_i, ay_i, az_i, gx_i, gy_i, gz_i, red_i, ir_i = FRAME_STRUCT.unpack(chunk)

            # Back to floats
            ax = ax_i * ACC_SCALE
            ay = ay_i * ACC_SCALE
            az = az_i * ACC_SCALE
            gx = gx_i * GYR_SCALE
            gy = gy_i * GYR_SCALE
            gz = gz_i * GYR_SCALE
            red = float(red_i)
            ir  = float(ir_i)

            self.sample_count += 1
            self.idx_raw.append(self.sample_count)

            self.data_raw["ax"].append(ax)
            self.data_raw["ay"].append(ay)
            self.data_raw["az"].append(az)
            self.data_raw["gx"].append(gx)
            self.data_raw["gy"].append(gy)
            self.data_raw["gz"].append(gz)
            self.data_raw["red"].append(red)
            self.data_raw["ir"].append(ir)

            # live recording
            if self.recording and self.record_file is not None:
                try:
                    self.record_file.write(
                        f"{self.sample_count},{ax},{ay},{az},"
                        f"{gx},{gy},{gz},{red},{ir}\n"
                    )
                except Exception:
                    pass

        # Drop consumed bytes
        remaining = n_bytes - n_frames * FRAME_SIZE
        if remaining > 0:
            self.byte_buf = self.byte_buf[-remaining:]
        else:
            self.byte_buf.clear()

        # Hard cap on raw buffer size
        if len(self.idx_raw) > MAX_RAW_SAMPLES:
            self.idx_raw = self.idx_raw[-MAX_RAW_SAMPLES:]
            for k in self.data_raw:
                self.data_raw[k] = self.data_raw[k][-MAX_RAW_SAMPLES:]

        # ===== Fs auto-calibration =====
        if self.fs_est is None:
            if self.calib_start_time is None:
                self.calib_start_time = time.perf_counter()
                self.calib_start_count = self.sample_count
            else:
                n = self.sample_count - self.calib_start_count
                if n >= self.CALIB_SAMPLES:
                    dt = time.perf_counter() - self.calib_start_time
                    if dt > 0:
                        self.fs_est = n / dt
                        print(f"[FS CALIBRATED] ≈ {self.fs_est:.2f} Hz")

    # ============ Plot Update ============
    def update_plot(self):
        # Wait until we know the real device Fs
        if self.fs_est is None:
            return

        # Snapshot lengths to avoid race with BLE callback
        lengths = [len(self.idx_raw)] + [len(self.data_raw[k]) for k in self.signal_order]
        n = min(lengths)
        if n < 2:
            return

        # Take only the last n samples from all channels
        idx = np.asarray(self.idx_raw[-n:], dtype=float)
        fs_raw = self.fs_est

        # Time (in seconds) relative to last sample: last sample at t=0
        t_raw = (idx - idx[-1]) / fs_raw  # e.g. [..., -0.02, -0.01, 0.0]

        # Only keep the last WINDOW_SEC seconds
        t_min = -WINDOW_SEC
        mask = t_raw >= t_min
        if mask.sum() < 2:
            return

        t_window = t_raw[mask]
        # shift so window is [0, ~WINDOW_SEC]
        t_window_shifted = t_window - t_window[0]

        # Target time grid at FS_RESAMP_HZ within [0, WINDOW_SEC]
        n_ds = int(WINDOW_SEC * FS_RESAMP_HZ)
        if n_ds < 2:
            return

        t_ds = np.linspace(0.0, WINDOW_SEC, n_ds, endpoint=False)
        self.t_resamp = t_ds

        for key in self.signal_order:
            vals_full = np.asarray(self.data_raw[key][-n:], dtype=float)
            vals_window = vals_full[mask]

            if len(vals_window) != len(t_window_shifted):
                continue

            vals_ds = np.interp(t_ds, t_window_shifted, vals_window)
            self.data_resamp[key] = vals_ds

            self.curves[key].setData(t_ds, vals_ds)
            self.axes[key].setXRange(0.0, WINDOW_SEC, padding=0)

    # ============ Cleanup on Close ============
    def closeEvent(self, event):
        # Stop recording if active
        if self.recording:
            self.on_stop_recording_clicked()

        # Disconnect BLE client if needed
        if self.client and self.client.is_connected:
            async def _disc():
                try:
                    await self.client.stop_notify(TINZR_BLE_TX_CHAR_UUID)
                except Exception:
                    pass
                await self.client.disconnect()

            fut = asyncio.run_coroutine_threadsafe(_disc(), self.loop)
            try:
                fut.result(timeout=2)
            except Exception:
                pass

        # Stop the loop
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        if self.ble_thread.is_alive():
            self.ble_thread.join(timeout=2)

        super().closeEvent(event)


# ================== Main ==================
if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = WearableViewer()
    w.resize(1500, 2000)
    w.show()
    sys.exit(app.exec_())
