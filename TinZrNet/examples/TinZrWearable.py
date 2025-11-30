import os
import math
from datetime import datetime
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
FS_RESAMP_HZ    = 240.0   # desired *fixed* output Fs (plot + save)
WINDOW_SEC      = 3.0     # seconds visible on screen
UPDATE_MS       = 10      # GUI update period in ms
MAX_RAW_SAMPLES = 20000   # safety cap on *plotting* raw buffer size


# ================== Toggle Switch Widget ==================
class ToggleSwitch(QtWidgets.QCheckBox):
    """
    Nice big oval sliding switch:
      - OFF: gray background
      - ON : green background
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        # Bigger, chunkier pill
        self._thumb_radius = 16
        self._track_radius = 20
        self._margin = 4

        self._width = 90
        self._height = 44

        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setChecked(False)
        self.setFixedSize(self._width, self._height)

        # Hide default checkbox indicator
        self.setStyleSheet("QCheckBox::indicator { width:0px; height:0px; }")

        # repaint whenever state changes
        self.stateChanged.connect(lambda _: self.update())

    def sizeHint(self):
        return QtCore.QSize(self._width, self._height)

    def hitButton(self, pos: QtCore.QPoint) -> bool:
        """
        Make the *entire* rect clickable, not just the (hidden) indicator.
        """
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        rect = self.rect()
        x = rect.x()
        y = rect.y()
        w = rect.width()
        h = rect.height()

        # Track rect
        track_rect = QtCore.QRectF(
            x + self._margin,
            y + self._margin,
            w - 2 * self._margin,
            h - 2 * self._margin
        )

        # Background color
        if self.isChecked():
            track_color = QtGui.QColor(76, 175, 80)   # ON: green
        else:
            track_color = QtGui.QColor(110, 110, 120) # OFF: gray

        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(track_color)
        p.drawRoundedRect(track_rect, self._track_radius, self._track_radius)

        # Thumb
        thumb_d = 2 * self._thumb_radius
        if self.isChecked():
            thumb_x = track_rect.right() - thumb_d
        else:
            thumb_x = track_rect.left()

        thumb_y = track_rect.center().y() - self._thumb_radius
        thumb_rect = QtCore.QRectF(thumb_x, thumb_y, thumb_d, thumb_d)

        thumb_color = QtGui.QColor(240, 240, 240)
        p.setBrush(thumb_color)
        p.drawEllipse(thumb_rect)

        p.end()


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

        self.setWindowTitle("TinZr Wearable")
        # Window icon (TinZr_logo.ico in same folder)
        self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))

        # ---------- Fixed size window ----------
        self.setFixedSize(1200, 1400)
        self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)

        # ---------- Global style / theme ----------
        self._apply_theme()

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(8)

        # ================== HEADER ==================
        header = QtWidgets.QWidget()
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 4)
        header_layout.setSpacing(10)

        title_label = QtWidgets.QLabel("TinZr Wearable Viewer")
        title_label.setStyleSheet("font-size: 16pt; font-weight: 600; color: #E3F2FD;")

        header_layout.addWidget(title_label, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        header_layout.addStretch(1)

        main_layout.addWidget(header)

        # ================== CONTROL PANEL ==================
        ctrl_widget = QtWidgets.QWidget()
        ctrl_layout = QtWidgets.QGridLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setHorizontalSpacing(16)
        ctrl_layout.setVerticalSpacing(8)

        row = 0

        # Find/scan devices
        self.btn_scan = QtWidgets.QPushButton("Find Devices")
        self.btn_scan.clicked.connect(self.on_scan_clicked)
        ctrl_layout.addWidget(self.btn_scan, row, 0)

        # Spinner OVERLAYED in the middle of the button
        self.spinner = Spinner(radius=8, line_width=2, parent=self.btn_scan)
        self.spinner.raise_()
        self.btn_scan.installEventFilter(self)
        self._center_spinner_on_button()

        # Device combo box
        self.combo_devices = QtWidgets.QComboBox()
        ctrl_layout.addWidget(self.combo_devices, row, 1, 1, 4)

        row += 1

        # Nice labels + toggle switches
        lbl_connect = QtWidgets.QLabel("Connect")
        lbl_stream  = QtWidgets.QLabel("Stream Data")
        lbl_record  = QtWidgets.QLabel("Record")

        self.toggle_connect = ToggleSwitch()
        self.toggle_stream  = ToggleSwitch()
        self.toggle_record  = ToggleSwitch()

        # initial states
        self.toggle_connect.setChecked(False)
        self.toggle_stream.setChecked(False)
        self.toggle_record.setChecked(False)

        # connect toggles to handlers
        self.toggle_connect.toggled.connect(self.on_connect_toggled)
        self.toggle_stream.toggled.connect(self.on_stream_toggled)
        self.toggle_record.toggled.connect(self.on_record_toggled)

        ctrl_layout.addWidget(lbl_connect, row, 0, alignment=QtCore.Qt.AlignRight)
        ctrl_layout.addWidget(self.toggle_connect, row, 1, alignment=QtCore.Qt.AlignLeft)

        ctrl_layout.addWidget(lbl_stream, row, 2, alignment=QtCore.Qt.AlignRight)
        ctrl_layout.addWidget(self.toggle_stream, row, 3, alignment=QtCore.Qt.AlignLeft)

        ctrl_layout.addWidget(lbl_record, row, 4, alignment=QtCore.Qt.AlignRight)
        ctrl_layout.addWidget(self.toggle_record, row, 5, alignment=QtCore.Qt.AlignLeft)

        row += 1

        # Status label
        self.label_status = QtWidgets.QLabel("Status: Idle")
        self.label_status.setObjectName("statusLabel")
        ctrl_layout.addWidget(self.label_status, row, 0, 1, 6)

        main_layout.addWidget(ctrl_widget)

        # Disable stream & record toggles until connected
        self.toggle_connect.setEnabled(True)
        self.toggle_stream.setEnabled(False)
        self.toggle_record.setEnabled(False)

        # ================== Graphics Layout ==================
        self.graphics = pg.GraphicsLayoutWidget()
        main_layout.addWidget(self.graphics)
        self.graphics.setBackground("#020817")  # deep navy

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

        # ================== Buffers (plotting) ==================
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
        self.record_path = None

        # ===== Recording buffers (for 240 Hz resampling on stop) =====
        self.rec_idx_raw = []
        self.rec_data_raw = {k: [] for k in self.signal_order}
        self.record_fs = FS_RESAMP_HZ  # forced output Fs (240 Hz)

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

    # ---------- Theme helper ----------
    def _apply_theme(self):
        # Dark blue TinZr-ish vibe
        palette = self.palette()
        bg = QtGui.QColor("#020817")       # main window background
        base = QtGui.QColor("#071529")     # controls background
        text = QtGui.QColor("#E0E8FF")     # light text
        accent = QtGui.QColor("#1E88E5")   # bright blue
        disable = QtGui.QColor("#555a70")

        palette.setColor(QtGui.QPalette.Window, bg)
        palette.setColor(QtGui.QPalette.Base, base)
        palette.setColor(QtGui.QPalette.AlternateBase, base.darker(120))
        palette.setColor(QtGui.QPalette.Text, text)
        palette.setColor(QtGui.QPalette.WindowText, text)
        palette.setColor(QtGui.QPalette.Button, base)
        palette.setColor(QtGui.QPalette.ButtonText, text)
        palette.setColor(QtGui.QPalette.Highlight, accent)
        palette.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, disable)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, disable)
        self.setPalette(palette)

        # Style sheet for controls
        self.setStyleSheet("""
        QWidget {
            background-color: #020817;
            color: #E0E8FF;
            font-family: "Segoe UI", "Roboto", sans-serif;
            font-size: 11pt;
        }
        QComboBox {
            background-color: #071529;
            border: 1px solid #1E88E5;
            border-radius: 8px;
            padding: 4px 8px;
        }
        QComboBox QAbstractItemView {
            background-color: #071529;
            selection-background-color: #1E88E5;
            selection-color: #ffffff;
        }
        QPushButton {
            background-color: #1565C0;
            border-radius: 16px;
            padding: 6px 18px;
            color: white;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #1E88E5;
        }
        QPushButton:pressed {
            background-color: #0D47A1;
        }
        QPushButton:disabled {
            background-color: #0b2340;
            color: #70758c;
        }
        QLabel#statusLabel {
            color: #90CAF9;
            font-size: 10pt;
        }
        """)

    # ---------- Spinner centering on button ----------
    def _center_spinner_on_button(self):
        if not self.spinner or not self.btn_scan:
            return
        brect = self.btn_scan.rect()
        srect = self.spinner.rect()
        x = (brect.width() - srect.width()) // 2
        y = (brect.height() - srect.height()) // 2
        self.spinner.move(x, y)

    def eventFilter(self, obj, event):
        if obj is self.btn_scan and event.type() == QtCore.QEvent.Resize:
            self._center_spinner_on_button()
        return super().eventFilter(obj, event)

    # ============ BLE loop thread ============
    def run_ble_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    # ============ UI helpers ============
    def set_status(self, text):
        self.label_status.setText(f"Status: {text}")

    def _block_and_set(self, toggle: ToggleSwitch, checked: bool):
        toggle.blockSignals(True)
        toggle.setChecked(checked)
        toggle.blockSignals(False)

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
        self._center_spinner_on_button()

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

    # ============ Connect / Disconnect via toggle ============
    def on_connect_toggled(self, checked: bool):
        if checked:
            # Show status immediately (before async work)
            self.set_status("Connecting...")
            QtWidgets.QApplication.processEvents()  # forces UI update

            ok = self._connect()

            if ok:
                self.set_status("Connected")
            else:
                self.set_status("Connect failed")
                self._block_and_set(self.toggle_connect, False)
        else:
            self._disconnect()

    def _connect(self) -> bool:
        """Connect to the selected device and subscribe to notifications."""
        if not self.loop.is_running():
            self.set_status("BLE loop not running")
            return False

        if self.client and self.client.is_connected:
            self.set_status("Already connected")
            return True

        addr = self.combo_devices.currentData()
        if not addr:
            self.set_status("No device selected")
            return False

        self.set_status(f"Connecting to {addr}...")

        async def _connect_coro():
            client = BleakClient(addr)
            await client.connect()
            if not client.is_connected:
                raise RuntimeError("Failed to connect")
            # Subscribe to notifications
            await client.start_notify(TINZR_BLE_TX_CHAR_UUID, self.on_rx)
            return client

        fut = asyncio.run_coroutine_threadsafe(_connect_coro(), self.loop)
        try:
            client = fut.result()
        except Exception as e:
            self.set_status(f"Connect error: {e}")
            return False

        self.client = client
        self.set_status("Connected")
        self.btn_scan.setEnabled(False)
        self.combo_devices.setEnabled(False)

        # Now you can stream
        self.toggle_stream.setEnabled(True)
        self.toggle_record.setEnabled(False)
        return True

    def _disconnect(self):
        """Disconnect and reset toggles."""
        if not self.loop.is_running():
            self.set_status("BLE loop not running")
        else:
            if self.client and self.client.is_connected:
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

        self.client = None

        # stop streaming / recording if needed
        if self.streaming:
            self.on_stop_data_clicked()
        if self.recording:
            self.on_stop_recording_clicked()

        # reset toggles / UI
        self.streaming = False
        self._block_and_set(self.toggle_stream, False)
        self.toggle_stream.setEnabled(False)

        self._block_and_set(self.toggle_record, False)
        self.toggle_record.setEnabled(False)

        self.btn_scan.setEnabled(True)
        self.combo_devices.setEnabled(True)
        self.set_status("Disconnected")

    # ============ Stream Data via toggle ============
    def on_stream_toggled(self, checked: bool):
        if checked:
            self.on_start_data_clicked()
            if not self.streaming:
                # if start failed, revert toggle
                self._block_and_set(self.toggle_stream, False)
        else:
            self.on_stop_data_clicked()

    def on_start_data_clicked(self):
        """Clear buffers and start using incoming data."""
        if not self.client or not self.client.is_connected:
            self.set_status("Not connected")
            self.streaming = False
            return

        # 👉 SEND START COMMAND TO WEARABLE
        if CMD_START:
            fut = asyncio.run_coroutine_threadsafe(
                self.client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_START),
                self.loop
            )
            try:
                fut.result()
            except Exception as e:
                self.set_status(f"Start cmd error: {e}")
                self.streaming = False
                return

        # reset buffers & Fs calibration (for plotting)
        self.sample_count = 0
        self.idx_raw.clear()
        for k in self.data_raw:
            self.data_raw[k].clear()

        self.fs_est = None
        self.calib_start_time = None
        self.calib_start_count = None

        self.streaming = True
        # now recording is allowed
        self.toggle_record.setEnabled(True)
        self.set_status("Starting streaming...")

        # also reset recording buffers if we start a new stream
        self.rec_idx_raw.clear()
        for k in self.rec_data_raw:
            self.rec_data_raw[k].clear()

    def on_stop_data_clicked(self):
        if not self.streaming:
            return

        # 👉 SEND STOP COMMAND TO WEARABLE
        if CMD_STOP and self.client and self.client.is_connected:
            fut = asyncio.run_coroutine_threadsafe(
                self.client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_STOP),
                self.loop
            )
            try:
                fut.result()
            except Exception as e:
                self.set_status(f"Stop cmd error: {e}")

        self.streaming = False
        self.toggle_record.setEnabled(False)

        # also stop recording if it was running
        if self.recording:
            self.on_stop_recording_clicked()
            self._block_and_set(self.toggle_record, False)

        self.set_status("Streaming stopped")

    # ============ Recording via toggle ============
    def on_record_toggled(self, checked: bool):
        if checked:
            self.on_start_recording_clicked()
            if not self.recording:
                # if recording failed, revert toggle
                self._block_and_set(self.toggle_record, False)
        else:
            self.on_stop_recording_clicked()

    def on_start_recording_clicked(self):
        if not self.streaming:
            self.set_status("Start data before recording")
            self.recording = False
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
            self.recording = False
            return

        # Open file, but we will write metadata+data on stop
        try:
            self.record_file = open(fname, "w", buffering=1)
            self.record_path = fname
        except Exception as e:
            self.set_status(f"File error: {e}")
            self.record_file = None
            self.record_path = None
            self.recording = False
            return

        # clear recording buffers
        self.rec_idx_raw.clear()
        for k in self.rec_data_raw:
            self.rec_data_raw[k].clear()

        self.recording = True
        self.set_status(f"Recording (buffering) to: {fname}")

    def on_stop_recording_clicked(self):
        if not self.recording:
            return

        self.recording = False

        # If no file / no data, just clean up
        if self.record_file is None or self.record_path is None:
            self.set_status("Recording stopped (no file)")
            return

        # Grab references then clear handle so we don't double-close
        f = self.record_file
        self.record_file = None

        # If no samples recorded, just write minimal header
        n_raw = len(self.rec_idx_raw)
        if n_raw < 2:
            try:
                f.write("# TinZr Wearable Recording (no data)\n")
                f.write(f"# DateTime: {datetime.now().isoformat()}\n")
                f.write(f"# Fs_out_Hz: {self.record_fs:.6f}\n")
                f.write("time_s,red,ir,ax,ay,az,gx,gy,gz\n")
                f.close()
            except Exception:
                pass
            self.set_status("Recording stopped (no samples)")
            return

        # ===== Resample to 240 Hz and write =====
        try:
            # Use estimated original Fs if available, else fall back
            fs_orig = self.fs_est if (self.fs_est is not None and self.fs_est > 0) else self.record_fs

            idx = np.asarray(self.rec_idx_raw, dtype=float)
            # Build raw time axis assuming uniform Fs_orig
            t_raw = (idx - idx[0]) / fs_orig  # start at 0
            t_end = t_raw[-1]

            # 240 Hz grid from 0 to t_end (exclusive)
            dt_out = 1.0 / self.record_fs
            n_out = int(t_end * self.record_fs)
            if n_out < 1:
                n_out = 1
            t_ds = np.linspace(0.0, (n_out - 1) * dt_out, n_out, endpoint=True)

            # Resample each channel onto t_ds
            data_out = {}
            for key in self.signal_order:
                vals = np.asarray(self.rec_data_raw[key], dtype=float)
                data_out[key] = np.interp(t_ds, t_raw, vals)

            # ---- Write metadata ----
            f.write("# TinZr Wearable Recording\n")
            f.write(f"# DateTime: {datetime.now().isoformat()}\n")
            f.write(f"# Fs_orig_Hz: {fs_orig:.6f}\n")
            f.write(f"# Fs_out_Hz: {self.record_fs:.6f}\n")
            f.write(f"# N_raw: {n_raw}\n")
            f.write(f"# N_out: {n_out}\n")
            f.write("# Columns: time_s, red, ir, ax, ay, az, gx, gy, gz\n")

            # ---- Header ----
            f.write("time_s,red,ir,ax,ay,az,gx,gy,gz\n")

            # ---- Data rows ----
            red_out = data_out["red"]
            ir_out  = data_out["ir"]
            ax_out  = data_out["ax"]
            ay_out  = data_out["ay"]
            az_out  = data_out["az"]
            gx_out  = data_out["gx"]
            gy_out  = data_out["gy"]
            gz_out  = data_out["gz"]

            for i in range(n_out):
                f.write(
                    f"{t_ds[i]:.6f},"
                    f"{red_out[i]:.6f},{ir_out[i]:.6f},"
                    f"{ax_out[i]:.6f},{ay_out[i]:.6f},{az_out[i]:.6f},"
                    f"{gx_out[i]:.6f},{gy_out[i]:.6f},{gz_out[i]:.6f}\n"
                )

            f.close()
            self.set_status(f"Recording saved (resampled to {self.record_fs:.1f} Hz)")
        except Exception as e:
            try:
                f.close()
            except Exception:
                pass
            self.set_status(f"Recording error: {e}")

        # Clear rec buffers
        self.rec_idx_raw.clear()
        for k in self.rec_data_raw:
            self.rec_data_raw[k].clear()

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

            # ---- Recording buffers (for resampling later) ----
            if self.recording:
                self.rec_idx_raw.append(self.sample_count)
                self.rec_data_raw["ax"].append(ax)
                self.rec_data_raw["ay"].append(ay)
                self.rec_data_raw["az"].append(az)
                self.rec_data_raw["gx"].append(gx)
                self.rec_data_raw["gy"].append(gy)
                self.rec_data_raw["gz"].append(gz)
                self.rec_data_raw["red"].append(red)
                self.rec_data_raw["ir"].append(ir)

        # Drop consumed bytes
        remaining = n_bytes - n_frames * FRAME_SIZE
        if remaining > 0:
            self.byte_buf = self.byte_buf[-remaining:]
        else:
            self.byte_buf.clear()

        # Hard cap on raw buffer size (for plotting only)
        if len(self.idx_raw) > MAX_RAW_SAMPLES:
            self.idx_raw = self.idx_raw[-MAX_RAW_SAMPLES:]
            for k in self.data_raw:
                self.data_raw[k] = self.data_raw[k][-MAX_RAW_SAMPLES:]

        # ===== Fs auto-calibration (for plotting + fs_orig) =====
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
                        self.set_status("Streaming")


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
    # optional: app.setStyle("Fusion")
    w = WearableViewer()
    w.show()
    sys.exit(app.exec_())
