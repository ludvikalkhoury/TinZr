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


# ===== Make parent "examples" directory importable so we can see GUIsHelper.py =====
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)
    
# ===== Reusable GUI helper pieces (toggle, spinner, battery, theme) =====
from GUIsHelper import (
    ToggleSwitch,
    Spinner,
    BatteryWidget,
    apply_tinzr_theme,
)

# ================== BLE UUIDs & Device Filter ==================
TINZR_BLE_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
TINZR_BLE_RX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
TINZR_BLE_TX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"

DEVICE_PREFIX = "TinZr"

# Optional commands if your firmware supports them (adjust as needed)
CMD_START = b"S"
CMD_STOP  = b"E"
# Dedicated battery query command
CMD_BATT = b"BAT"

# ================== Frame format (must match C++) ==============
# struct __attribute__((packed)) WearFrame {
#   int16_t  ax, ay, az;   // accel * 1000
#   int16_t  gx, gy, gz;   // gyro  * 100
#   uint32_t red;          // raw PPG red
#   uint32_t ir;           // raw PPG ir
#   uint8_t  hr_bpm;       // HR
#   uint8_t  spo2_pct;     // SpO2
#   uint8_t  batt_pct;     // battery [%]
# };
#
# little-endian format:
#   6 * int16  -> hhhhhh
#   2 * uint32 -> II
#   3 * uint8  -> BBB
#
# => "<hhhhhhIIBBB"
FRAME_STRUCT = struct.Struct("<hhhhhhIIBBB")
FRAME_SIZE   = FRAME_STRUCT.size  # 23 bytes

# accel & gyro scales (inverse of firmware scaling)
ACC_SCALE = 1e-3         # milli-units -> m/s^2  (firmware sends accel * 1000)
GYR_SCALE = 1.0 / 100.0  # centi-units -> dps-ish or rad/s-ish

# ================== Viewer Config ==================
FS_RESAMP_HZ    = 240.0   # desired *fixed* output Fs (plot + save)
WINDOW_SEC      = 3.0     # seconds visible on screen
UPDATE_MS       = 10      # GUI update period in ms
MAX_RAW_SAMPLES = 20000   # safety cap on *plotting* raw buffer size


# ================== Main Viewer ==================
class WearableViewer(QtWidgets.QWidget):
    scan_finished = QtCore.pyqtSignal(object, object)  # (devices, error)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("TinZr Wearable")
        self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))

        # ---------- Fixed size window ----------
        self.setFixedSize(600, 700)
        self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)

        # ---------- Global style / theme (from helper) ----------
        apply_tinzr_theme(self)
        
        # ---------- DPI-aware font sizes for axis labels ----------
        screen = QtWidgets.QApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch() if screen is not None else 96.0
        # 96 dpi = baseline scale 1.0
        self._dpi_scale = dpi / 96.0

        # axis label font sizes (in points)
        self._axis_label_pt = int(round(12 * self._dpi_scale))     # y-labels
        self._time_label_pt = int(round(12 * self._dpi_scale))    # "Time (s)"

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

        # 🔋 Fancy battery widget at top-right of GUI
        self.batt_widget = BatteryWidget()
        self.batt_widget.clicked.connect(self.on_batt_clicked)
        header_layout.addWidget(self.batt_widget, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

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

        self.rec_keys = self.signal_order + ["hr", "spo2", "batt"]

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
            left_axis.setLabel(
                text=key,
                units="",
                **{
                    "color": "#E0E8FF",
                    "size": f"{self._axis_label_pt}pt",
                }
            )

            # BOTTOM AXIS: only on last row, and also no numbers
            if row < len(self.signal_order) - 1:
                p.showAxis("bottom", False)
            else:
                p.showAxis("bottom", True)
                bottom_axis = p.getAxis("bottom")
                bottom_axis.enableAutoSIPrefix(False)
                bottom_axis.setTicks([])
                bottom_axis.setStyle(showValues=False, tickLength=0)
                bottom_axis.setLabel(
                    text="Time (s)",
                    units="",
                    **{
                        "color": "#E0E8FF",
                        "size": f"{self._time_label_pt}pt",
                    }
                )

            curve = p.plot([], [], pen=pg.mkPen(colors[key], width=1.4))
            self.axes[key] = p
            self.curves[key] = curve

        # ================== HR / SpO2 / battery state ==================
        self.hr_bpm = None
        self.spo2_pct = None
        self.batt_pct = None

        # Small HUD overlay (top-right over the plots)
        self._init_hud()

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
        self.rec_data_raw = {k: [] for k in self.rec_keys}

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

    # ---------- HUD (HR / SpO2 / Battery) ----------
    def _init_hud(self):
        # small floating label overlay over the plots
        self.lbl_hr_spo2 = QtWidgets.QLabel(self.graphics)
        self.lbl_hr_spo2.setStyleSheet("""
            QLabel {
                color: #FFFFFF;
                font-size: 8pt;
                font-weight: 500;
                background-color: rgba(0, 0, 0, 120);
                padding: 2px 6px;
                border-radius: 6px;
            }
        """)
        self.lbl_hr_spo2.setText("HR: -- bpm   SpO₂: -- %")

        # transparent to mouse, doesn't block interaction
        self.lbl_hr_spo2.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        self.lbl_hr_spo2.adjustSize()
        self.lbl_hr_spo2.show()
        self.lbl_hr_spo2.raise_()

        # initial positioning (may be refined after showEvent)
        self._position_hr_hud()

    def _position_hr_hud(self):
        if not self.isVisible():
            return

        # Anchor to the top-right inside the graphics widget itself
        r = self.graphics.rect()  # coordinates in graphics' own space
        x = r.width() - self.lbl_hr_spo2.width() - 10
        y = 10
        self.lbl_hr_spo2.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_hr_hud()

    def showEvent(self, event):
        super().showEvent(event)
        # Reposition once the widget has its final geometry
        QtCore.QTimer.singleShot(0, self._position_hr_hud)

    def _update_hud(self):
        # ---- HR ----
        if self.hr_bpm is not None and self.hr_bpm > 0:
            hr_text = f"HR: {int(self.hr_bpm)} bpm"
        else:
            hr_text = "HR: -- bpm"

        # ---- SpO2 ----
        if self.spo2_pct is not None and self.spo2_pct > 0:
            spo2_text = f"SpO₂: {int(self.spo2_pct)} %"
        else:
            spo2_text = "SpO₂: -- %"

        self.lbl_hr_spo2.setText(f"{hr_text}   {spo2_text}")
        self.lbl_hr_spo2.adjustSize()
        self._position_hr_hud()

        # ---- Battery ----
        if hasattr(self, "batt_widget"):
            if self.batt_pct is not None and self.batt_pct >= 0:
                self.batt_widget.setLevel(self.batt_pct)
            else:
                self.batt_widget.setLevel(None)

    # ---------- Battery click handler ----------
    def on_batt_clicked(self):
        """
        Called when user clicks the battery widget.
        Sends CMD_BATT so firmware can push an updated battery value.
        """
        if not CMD_BATT:
            self.set_status("Battery command not configured")
            return

        if self.client and self.client.is_connected:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self.client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_BATT),
                    self.loop
                )
                fut.result()
                self.set_status("Requested battery refresh")
            except Exception as e:
                self.set_status(f"Battery refresh error: {e}")
        else:
            self.set_status("Connect to TinZr to query battery")

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

        self.byte_buf.clear()

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

        self.byte_buf.clear()

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

        # Reset HUD state
        self.hr_bpm = None
        self.spo2_pct = None
        self.batt_pct = None
        self._update_hud()

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
            "tinzr_recording_240Hz.csv",   # hint in name that this will be resampled
            "CSV Files (*.csv);;All Files (*)",
        )
        if not fname:
            self.recording = False
            return

        # Open file; we'll write everything (metadata + resampled data) on stop
        try:
            self.record_file = open(fname, "w", buffering=1)
            self.record_path = fname
        except Exception as e:
            self.set_status(f"File error: {e}")
            self.record_file = None
            self.record_path = None
            self.recording = False
            return

        # Clear recording buffers
        self.rec_idx_raw.clear()
        for k in self.rec_data_raw:
            self.rec_data_raw[k].clear()

        self.recording = True
        self.set_status(f"Recording (buffering, resample on stop) → {fname}")

    def on_stop_recording_clicked(self):
        if not self.recording:
            return

        self.recording = False

        # If no file / no data, just clean up
        if self.record_file is None or self.record_path is None:
            self.set_status("Recording stopped (no file)")
            return

        # Grab reference then clear handle so we don't double-close
        f = self.record_file
        self.record_file = None

        # If no samples recorded, just write minimal header
        n_raw = len(self.rec_idx_raw)
        if n_raw < 2:
            try:
                f.write("# TinZr Wearable Recording (no data)\n")
                f.write(f"# DateTime: {datetime.now().isoformat()}\n")
                f.write(f"# Fs_out_Hz: {self.record_fs:.6f}\n")
                f.write("time_s,red,ir,ax,ay,az,gx,gy,gz,hr_bpm,spo2_pct,batt_pct\n")
                f.close()
            except Exception:
                pass
            self.set_status("Recording stopped (no samples)")
            return

        # ===== Resample to 240 Hz and write =====
        try:
            # Use estimated original Fs if available, else fall back to 240 Hz
            fs_orig = self.fs_est if (self.fs_est is not None and self.fs_est > 0) else self.record_fs

            idx = np.asarray(self.rec_idx_raw, dtype=float)
            # Build raw time axis assuming uniform Fs_orig
            t_raw = (idx - idx[0]) / fs_orig  # start at 0
            t_end = t_raw[-1]

            # 240 Hz grid from 0 to t_end
            dt_out = 1.0 / self.record_fs
            n_out = int(t_end * self.record_fs)
            if n_out < 1:
                n_out = 1
            t_ds = np.linspace(0.0, (n_out - 1) * dt_out, n_out, endpoint=True)

            # Resample each channel onto t_ds
            # HR/SpO2/Batt as ZOH, others linear
            def zoh_resample(vals, t_raw, t_ds):
                vals = np.asarray(vals, dtype=float)
                if len(vals) == 0:
                    return np.zeros_like(t_ds)
                idx_axis = np.arange(len(vals), dtype=float)
                pos = np.interp(t_ds, t_raw, idx_axis)
                pos_idx = np.clip(np.floor(pos).astype(int), 0, len(vals) - 1)
                return vals[pos_idx]

            data_out = {}
            for key in self.rec_keys:
                vals = np.asarray(self.rec_data_raw[key], dtype=float)
                if key in ("hr", "spo2", "batt"):
                    data_out[key] = zoh_resample(vals, t_raw, t_ds)
                else:
                    data_out[key] = np.interp(t_ds, t_raw, vals)

            # ---- Write metadata ----
            f.write("# TinZr Wearable Recording (resampled to fixed 240 Hz)\n")
            f.write(f"# DateTime: {datetime.now().isoformat()}\n")
            f.write(f"# Fs_orig_Hz: {fs_orig:.6f}\n")
            f.write(f"# Fs_out_Hz: {self.record_fs:.6f}\n")
            f.write(f"# N_raw: {n_raw}\n")
            f.write(f"# N_out: {n_out}\n")
            f.write("# Columns: time_s, red, ir, ax, ay, az, gx, gy, gz, hr_bpm, spo2_pct, batt_pct\n")

            # ---- Header ----
            f.write("time_s,red,ir,ax,ay,az,gx,gy,gz,hr_bpm,spo2_pct,batt_pct\n")

            # ---- Data rows ----
            red_out  = data_out["red"]
            ir_out   = data_out["ir"]
            ax_out   = data_out["ax"]
            ay_out   = data_out["ay"]
            az_out   = data_out["az"]
            gx_out   = data_out["gx"]
            gy_out   = data_out["gy"]
            gz_out   = data_out["gz"]
            hr_out   = data_out["hr"]
            spo2_out = data_out["spo2"]
            batt_out = data_out["batt"]

            for i in range(n_out):
                f.write(
                    f"{t_ds[i]:.6f},"
                    f"{red_out[i]:.6f},{ir_out[i]:.6f},"
                    f"{ax_out[i]:.6f},{ay_out[i]:.6f},{az_out[i]:.6f},"
                    f"{gx_out[i]:.6f},{gy_out[i]:.6f},{gz_out[i]:.6f},"
                    f"{hr_out[i]:.2f},{spo2_out[i]:.2f},{batt_out[i]:.2f}\n"
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
        """
        Incoming BLE notifications (BINARY). Runs in BLE thread.

        We ALWAYS parse frames so battery/HR/SpO2 can update even
        if streaming is currently False. Plot/record buffers are only
        updated when self.streaming is True.
        """
        # Append new bytes to the rolling buffer
        self.byte_buf.extend(data)

        n_bytes = len(self.byte_buf)
        n_frames = n_bytes // FRAME_SIZE

        if n_frames == 0:
            return

        for i in range(n_frames):
            start = i * FRAME_SIZE
            chunk = self.byte_buf[start:start + FRAME_SIZE]

            (
                ax_i, ay_i, az_i,
                gx_i, gy_i, gz_i,
                red_i, ir_i,
                hr_i, spo2_i,
                batt_i
            ) = FRAME_STRUCT.unpack(chunk)

            # ---- Update HUD state (always, even if not streaming) ----
            self.hr_bpm   = hr_i
            self.spo2_pct = spo2_i
            self.batt_pct = batt_i

            # If we're not streaming, don't touch sample_count or buffers.
            # The GUI timer will still pick up the new batt/HR/SpO2.
            if not self.streaming:
                continue

            # Back to floats for streaming/recording
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
                self.rec_data_raw["hr"].append(float(hr_i))
                self.rec_data_raw["spo2"].append(float(spo2_i))
                self.rec_data_raw["batt"].append(float(batt_i))

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
        if self.streaming and self.fs_est is None:
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
        # HUD always updates from latest hr/spo2/batt
        self._update_hud()

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
    # Let Qt scale based on DPI so 600x700 stays a similar physical size
    if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
    if hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

    app = QtWidgets.QApplication(sys.argv)
    w = WearableViewer()
    w.show()
    sys.exit(app.exec_())
