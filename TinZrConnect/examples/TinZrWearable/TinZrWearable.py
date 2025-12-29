import os
import math
import bisect
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
FS_RESAMP_HZ    = 100   # desired *fixed* output Fs (plot + save)
WINDOW_SEC      = 3.0     # seconds visible on screen
UPDATE_MS       = 10      # GUI update period in ms
MAX_RAW_SAMPLES = 20000   # safety cap on *plotting* raw buffer size

# NEW: file write cadence (save-as-you-go)
WRITE_EVERY_MS      = 200   # how often we attempt to write new fixed-grid rows while recording
WRITE_FLUSH_EVERY_N = 1     # flush after each write batch (keep crash-safe)

# NEW: ticking-clock recording behavior
AUTO_STOP_NO_DATA_SEC = 20.0   # if no frames received for this long while recording, stop recording
GAP_MAX_SEC           = 0.25   # if last sample is older than this, treat as dropout -> NaN
REC_PRUNE_KEEP_SEC    = 5.0    # keep last N seconds of raw samples for interpolation (prevents RecBuf growth)

# Incoming (firmware) Fs hint used when fs_est isn't calibrated yet
FW_FS_HINT_HZ      = 250.0


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

        # Second status row (debug telemetry)
        self.label_status2 = QtWidgets.QLabel("")
        self.label_status2.setStyleSheet("font-family: monospace; font-size: 9pt; color: #A8B3CF;")
        ctrl_layout.addWidget(self.label_status2, row + 1, 0, 1, 6)

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

        # ===== Incremental CSV recording (save-as-you-go) =====
        # If True, we write CSV rows continuously in small buffered chunks.
        # This avoids holding a full recording in RAM.
        self.record_incremental = True

        # ---- NEW: ticking-clock writer state (mirrors MultiWearable logic) ----
        self.rec_t_raw = []          # reconstructed per-sample time since record start (sec)
        self._t_rec0 = None          # perf_counter() origin when recording starts
        self._rec_last_t = None      # last reconstructed t_rel (sec)
        self._last_any_record_frame = None
        self._write_cursor = 0       # how many fixed-grid rows already written
        self._flush_counter = 0
        self._csv_header_written = False

        # ---- NEW: debug counters (RX/NaN telemetry) ----
        self._rx_packets = 0
        self._rx_frames = 0
        self._nan_rows = 0
        self._last_rx_time = None
        self._dbg_last_print = time.monotonic()

        self.writer_timer = QtCore.QTimer(self)
        self.writer_timer.setInterval(int(WRITE_EVERY_MS))
        self.writer_timer.timeout.connect(self._writer_tick)
        self.csv_flush_every = 250  # write to disk every N samples
        self._rec_lock = threading.Lock()
        self._csv_lines = []
        self._rec_start_sample = None
        self._rec_wall_t0 = None

        # ===== Recording buffers (for FS_RESAMP_HZ Hz resampling on stop) =====
        self.rec_idx_raw = []
        self.rec_data_raw = {k: [] for k in self.rec_keys}

        self.record_fs = FS_RESAMP_HZ  # forced output Fs (FS_RESAMP_HZ Hz)

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
        self.lbl_hr_spo2.setText(f"HR: -- bpm   SpO₂: -- %   Raw Fs: -- Hz   Fixed Fs: {FS_RESAMP_HZ:.1f} Hz")

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

        # ---- Raw Fs (incoming) ----
        if self.fs_est is not None and self.fs_est > 0:
            raw_fs_text = f"Raw Fs: {self.fs_est:.1f} Hz"
        else:
            raw_fs_text = "Raw Fs: -- Hz"

        # ---- Fixed Fs (display/save grid) ----
        fixed_fs = getattr(self, "record_fs", FS_RESAMP_HZ)
        fixed_fs_text = f"Fixed Fs: {float(fixed_fs):.1f} Hz"

        self.lbl_hr_spo2.setText(f"{hr_text}   {spo2_text}   {raw_fs_text}   {fixed_fs_text}")
        self.lbl_hr_spo2.adjustSize()
        self._position_hr_hud()

        # ---- Battery ----
        if hasattr(self, "batt_widget"):
            if self.batt_pct is not None and self.batt_pct >= 0:
                self.batt_widget.setLevel(self.batt_pct)
            else:
                self.batt_widget.setLevel(None)


    def _update_debug_row(self):
        """Update second status row with RX / NaN telemetry (cheap, once per second)."""
        if not hasattr(self, "label_status2"):
            return

        now = time.monotonic()
        dt = now - float(self._dbg_last_print)
        if dt < 1.0:
            return

        # Rates
        pkts_s = int(self._rx_packets / dt) if dt > 0 else 0
        frames_s = int(self._rx_frames / dt) if dt > 0 else 0
        nans_s = int(self._nan_rows / dt) if dt > 0 else 0

        # Age since last RX
        if self._last_rx_time is None:
            last_rx_ms = -1
        else:
            last_rx_ms = int((now - float(self._last_rx_time)) * 1000.0)

        rec_samples = len(self.rec_t_raw) if hasattr(self, "rec_t_raw") else 0
        written = int(self._write_cursor) if hasattr(self, "_write_cursor") else 0
        buf_bytes = len(self.byte_buf) if hasattr(self, "byte_buf") else 0

        self.label_status2.setText(
            f"RX: {pkts_s} pkts/s | {frames_s} frames/s | "
            f"Last RX: {last_rx_ms} ms | NaNs: {nans_s}/s | "
            f"RecBuf: {rec_samples} | Written: {written} | Buf: {buf_bytes}B"
        )

        # reset counters for next window
        self._rx_packets = 0
        self._rx_frames = 0
        self._nan_rows = 0
        self._dbg_last_print = now

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
            async def _write_batt():
                await asyncio.wait_for(
                    self.client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_BATT),
                    timeout=2.0
                )
            
            fut = asyncio.run_coroutine_threadsafe(_write_batt(), self.loop)
            try:
                fut.result(timeout=3.0)
                self.set_status("Requested battery refresh")
            except asyncio.TimeoutError:
                self.set_status("Battery refresh timeout")
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
            try:
                # Professional: Add connection timeout (10 seconds)
                await asyncio.wait_for(client.connect(), timeout=10.0)
                if not client.is_connected:
                    raise RuntimeError("Failed to connect")
                
                # Professional: Optional MTU negotiation for larger packets
                # (Uncomment if your firmware supports it and you want >23 byte MTU)
                # try:
                #     await client.set_mtu(247)  # Request larger MTU
                # except Exception:
                #     pass  # Fall back to default MTU if not supported
                
                # Professional: Set up disconnection callback
                def _disconnect_callback(client, future=None):
                    if hasattr(self, 'scan_finished'):
                        # Signal disconnection back to GUI thread
                        QtCore.QTimer.singleShot(0, lambda: self._handle_ble_disconnect())
                
                # Subscribe to notifications
                await client.start_notify(TINZR_BLE_TX_CHAR_UUID, self.on_rx)
                
                # Professional: Monitor connection state (Bleak handles this via callbacks)
                return client
            except asyncio.TimeoutError:
                raise RuntimeError("Connection timeout - device not responding")
            except Exception as e:
                # Clean up on any error
                try:
                    await client.disconnect()
                except Exception:
                    pass
                raise

        fut = asyncio.run_coroutine_threadsafe(_connect_coro(), self.loop)
        try:
            # Professional: Add timeout for the future result
            client = fut.result(timeout=12.0)  # Slightly longer than connection timeout
        except asyncio.TimeoutError:
            self.set_status("Connect timeout - operation took too long")
            return False
        except Exception as e:
            self.set_status(f"Connect error: {e}")
            return False

        self.client = client
        
        # Professional: Set up connection monitoring
        # Bleak will handle disconnection events, but we need to catch them
        # For now, we rely on periodic connection checks in _handleBLE()
        
        self.set_status("Connected")
        self.btn_scan.setEnabled(False)
        self.combo_devices.setEnabled(False)

        # Now you can stream
        self.toggle_stream.setEnabled(True)
        self.toggle_record.setEnabled(False)
        return True
    
    def _handle_ble_disconnect(self):
        """Professional: Handle unexpected BLE disconnection."""
        if self.client is None:
            return
        # This will be called if connection drops unexpectedly
        self.set_status("Device disconnected unexpectedly")
        self._disconnect()

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
            async def _write_start():
                await asyncio.wait_for(
                    self.client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_START),
                    timeout=2.0
                )
            
            fut = asyncio.run_coroutine_threadsafe(_write_start(), self.loop)
            try:
                fut.result(timeout=3.0)
            except asyncio.TimeoutError:
                self.set_status("Start command timeout - device not responding")
                self.streaming = False
                return
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
        self.rec_t_raw.clear()
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
            async def _write_stop():
                await asyncio.wait_for(
                    self.client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_STOP),
                    timeout=2.0
                )
            
            fut = asyncio.run_coroutine_threadsafe(_write_stop(), self.loop)
            try:
                fut.result(timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:
                # Non-critical if stop fails
                if isinstance(e, asyncio.TimeoutError):
                    self.set_status("Stop command timeout (non-critical)")
                else:
                    self.set_status(f"Stop cmd error: {e} (non-critical)")

        self.streaming = False
        self.toggle_record.setEnabled(False)

        # also stop recording if it was running
        if self.recording:
            self.on_stop_recording_clicked()
            self._block_and_set(self.toggle_record, False)

        self.set_status("Streaming stopped")

    
    # ============ NEW: ticking-clock incremental writer ============
    def _writer_tick(self):
        # Keep file updated while recording (do NOT do heavy work in BLE notify thread)
        if not self.recording:
            return

        # Auto-stop if nothing has been received for too long while recording
        now = time.perf_counter()
        if self._last_any_record_frame is not None:
            if (now - float(self._last_any_record_frame)) >= float(AUTO_STOP_NO_DATA_SEC):
                self.set_status(f"No data for {AUTO_STOP_NO_DATA_SEC:.0f}s → auto-stopping recording.")
                try:
                    self.toggle_record.blockSignals(True)
                    self.toggle_record.setChecked(False)
                finally:
                    self.toggle_record.blockSignals(False)
                self.on_stop_recording_clicked()
                return

        self._write_available_rows(final=False)

    def _write_header_placeholders(self):
        f = self.record_file
        if f is None:
            return

        f.write("# TinZr Wearable Recording (ticking-clock)\n")
        f.write(f"# DateTime: {datetime.now().isoformat()}\n")
        f.write("# __FS_ORIG_LINES__\n")          # placeholder (filled on stop)
        f.write(f"# Fs_out_Hz: {float(self.record_fs):.6f}\n")
        f.write("# N_out: __PLACEHOLDER__\n")     # placeholder (filled on stop)
        f.write("# Columns: time_s, red, ir, ax, ay, az, gx, gy, gz, hr_bpm, spo2_pct, batt_pct\n")
        f.write("time_s,red,ir,ax,ay,az,gx,gy,gz,hr_bpm,spo2_pct,batt_pct\n")
        f.write("# __DATA_START__\n")
        f.flush()
        self._csv_header_written = True

    def _write_available_rows(self, final: bool = False):
        '''
        Ticking-clock writer:
        - time_s is based on perf_counter() since recording started (self._t_rec0)
        - we ALWAYS advance time (fixed record_fs grid)
        - if no data for a given time, we write NaN for that field
        '''
        f = self.record_file
        if f is None or not self._csv_header_written:
            return

        # Ensure _t_rec0 is initialized (should already be set, but fail-safe)
        if self._t_rec0 is None:
            # This should be rare - defensive initialization
            with self._rec_lock:
                if self._t_rec0 is None:  # Double-check under lock
                    self._t_rec0 = time.perf_counter()
                    self._last_any_record_frame = self._t_rec0

        now = time.perf_counter()
        t_end = float(now - float(self._t_rec0))
        if t_end < 0:
            t_end = 0.0

        # How many output rows should exist up to NOW?
        n_out = int(t_end * float(self.record_fs))
        if n_out <= int(self._write_cursor):
            return

        dt = 1.0 / float(self.record_fs)

        # Snapshot buffers under lock (short)
        with self._rec_lock:
            tt = list(self.rec_t_raw)
            snap = {k: list(self.rec_data_raw.get(k, [])) for k in self.rec_keys}

        def _fmt(v, is_intish: bool = False):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return "nan"
            if is_intish:
                return f"{float(v):.2f}"
            return f"{float(v):.6f}"

        def _interp_or_nan(t, tt, vv):
            if tt is None or vv is None:
                return float("nan")
            L = min(len(tt), len(vv))
            if L < 2:
                return float("nan")
            tta = np.asarray(tt[:L], dtype=float)
            vva = np.asarray(vv[:L], dtype=float)

            if t < tta[0] or t > tta[-1]:
                return float("nan")

            j = int(np.searchsorted(tta, t, side="right") - 1)
            if j < 0 or j >= (len(vva) - 1):
                return float("nan")

            # avoid bridging long gaps
            if (t - tta[j]) > float(GAP_MAX_SEC):
                return float("nan")
            if (tta[j + 1] - tta[j]) > float(GAP_MAX_SEC):
                return float("nan")

            t0 = tta[j]
            t1 = tta[j + 1]
            if t1 <= t0:
                return float("nan")
            a = (t - t0) / (t1 - t0)
            return float(vva[j] * (1.0 - a) + vva[j + 1] * a)

        def _zoh_or_nan(t, tt, vv):
            if tt is None or vv is None:
                return float("nan")
            L = min(len(tt), len(vv))
            if L < 1:
                return float("nan")
            tta = np.asarray(tt[:L], dtype=float)
            vva = np.asarray(vv[:L], dtype=float)

            if t < tta[0] or t > tta[-1]:
                return float("nan")

            j = int(np.searchsorted(tta, t, side="right") - 1)
            if j < 0 or j >= len(vva):
                return float("nan")

            if (t - tta[j]) > float(GAP_MAX_SEC):
                return float("nan")

            return float(vva[j])

        # Write new rows [cursor . n_out)
        for i in range(int(self._write_cursor), int(n_out)):
            t = i * dt
            row = [f"{t:.6f}"]
            all_nan = True

            for k in self.rec_keys:
                vals = snap.get(k, [])
                if k in ("hr", "spo2", "batt"):
                    v = _zoh_or_nan(t, tt, vals)
                    row.append(_fmt(v, is_intish=True))
                    if not (isinstance(v, float) and np.isnan(v)):
                        all_nan = False
                else:
                    v = _interp_or_nan(t, tt, vals)
                    row.append(_fmt(v, is_intish=False))
                    if not (isinstance(v, float) and np.isnan(v)):
                        all_nan = False

            if all_nan:
                self._nan_rows += 1
            f.write(",".join(row) + "\n")

        self._write_cursor = int(n_out)

        # ---- NEW: prune raw record buffers so RecBuf doesn't grow without bound ----
        # We only need a small tail of raw samples to bracket upcoming fixed-grid ticks.
        # Keep last REC_PRUNE_KEEP_SEC seconds plus a 2-sample safety margin.
        keep_from_t = max(0.0, (int(self._write_cursor) - 2) * dt - float(REC_PRUNE_KEEP_SEC))
        try:
            j = bisect.bisect_left(self.rec_t_raw, keep_from_t) if self.rec_t_raw else 0
        except Exception:
            j = 0

        if j > 0:
            with self._rec_lock:
                # Recompute under lock in case buffers changed slightly
                try:
                    j2 = bisect.bisect_left(self.rec_t_raw, keep_from_t) if self.rec_t_raw else 0
                except Exception:
                    j2 = j
                if j2 > 0:
                    self.rec_t_raw = self.rec_t_raw[j2:]
                    try:
                        self.rec_idx_raw = self.rec_idx_raw[j2:]
                    except Exception:
                        pass
                    for _k in self.rec_keys:
                        _vv = self.rec_data_raw.get(_k, None)
                        if _vv is not None:
                            self.rec_data_raw[_k] = _vv[j2:]

        # flush frequently (crash-safe)
        self._flush_counter += 1
        if self._flush_counter >= int(WRITE_FLUSH_EVERY_N) or final:
            try:
                f.flush()
            except Exception:
                pass
            self._flush_counter = 0

    def _finalize_csv_header(self, path: str):
        '''Rewrite ONLY the header placeholders (Fs_orig, N_out), leaving data rows untouched.'''
        tmp_path = path + ".tmp"

        fs_orig = float(self.fs_est) if (self.fs_est is not None and self.fs_est > 0) else float(FW_FS_HINT_HZ)
        final_n_out = int(self._write_cursor)

        with open(path, "r", newline="") as fin, open(tmp_path, "w", newline="") as fout:
            in_header = True
            for line in fin:
                if in_header:
                    if line.strip() == "# __FS_ORIG_LINES__":
                        fout.write(f"# Fs_orig_Hz: {fs_orig:.6f}\n")
                        continue
                    if line.startswith("# N_out:"):
                        fout.write(f"# N_out: {final_n_out:d}\n")
                        continue

                    fout.write(line)
                    if line.strip() == "# __DATA_START__":
                        in_header = False
                else:
                    fout.write(line)

        try:
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.remove(path)
            except Exception:
                pass
            os.rename(tmp_path, path)

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
        
        ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

        fname, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Recording",
            "TinZrWearable_recording_"+str(ts)+".csv",   # hint in name that this will be resampled
            "CSV Files (*.csv);;All Files (*)",
        )
        if not fname:
            self.recording = False
            return

        # Open file
        try:
            # Use a larger buffer for fewer disk writes; we flush manually.
            self.record_file = open(fname, "w", buffering=64 * 1024)
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

        # Initialize incremental writer state
        self._rec_start_sample = self.sample_count
        self._rec_wall_t0 = time.perf_counter()
        self._csv_lines.clear()

        # Reset ticking-clock writer state (single-device version of MultiWearable logic)
        # Initialize ALL recording state BEFORE setting recording=True to avoid race conditions
        with self._rec_lock:
            self.rec_t_raw.clear()
            for k in self.rec_data_raw:
                self.rec_data_raw[k].clear()
            # Set timestamp origin BEFORE recording flag (critical for thread safety)
            self._t_rec0 = time.perf_counter()
            self._rec_last_t = None
            self._last_any_record_frame = self._t_rec0
            self._write_cursor = 0
            self._flush_counter = 0

        # Set this outside lock to avoid deadlock with writer
        self._csv_header_written = False

        # If saving-as-you-go, write header immediately (placeholders finalized on stop)
        if self.record_incremental and self.record_file is not None:
            try:
                self._write_header_placeholders()
            except Exception as e:
                self.set_status(f"Recording header error: {e}")

        # Start writer timer (handles NaN gaps + auto-stop)
        try:
            self.writer_timer.start()
        except Exception:
            pass

        # Set recording flag LAST, after all state is initialized
        # This ensures BLE thread will see complete initialization
        self.recording = True
        self.set_status(f"Recording → {fname} ({'incremental' if self.record_incremental else 'buffer+resample on stop'})")

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

        # If we were writing incrementally (ticking-clock), write remaining rows, close, and finalize header.
        if self.record_incremental:
            try:
                try:
                    self.writer_timer.stop()
                except Exception:
                    pass

                # write any remaining fixed-grid rows
                self._write_available_rows(final=True)

                # close file first
                with self._rec_lock:
                    try:
                        f.flush()
                    except Exception:
                        pass
                f.close()

                # finalize header placeholders (Fs_orig, N_out)
                try:
                    self._finalize_csv_header(self.record_path)
                except Exception:
                    pass

                self.set_status(f"Recording saved (ticking-clock) → {self.record_path}")
            except Exception as e:
                try:
                    f.close()
                except Exception:
                    pass
                self.set_status(f"Recording stop error: {e}")
            finally:
                # reset writer state for next time
                self._csv_header_written = False
                self._t_rec0 = None
                self._rec_last_t = None
                self._last_any_record_frame = None
                self._write_cursor = 0
                self._flush_counter = 0
            return


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

        # ===== Resample to FS_RESAMP_HZ Hz and write =====
        try:
            # Use estimated original Fs if available, else fall back to FS_RESAMP_HZ Hz
            fs_orig = self.fs_est if (self.fs_est is not None and self.fs_est > 0) else self.record_fs

            idx = np.asarray(self.rec_idx_raw, dtype=float)
            # Build raw time axis assuming uniform Fs_orig
            t_raw = (idx - idx[0]) / fs_orig  # start at 0
            t_end = t_raw[-1]

            # FS_RESAMP_HZ Hz grid from 0 to t_end
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
            f.write(f"# TinZr Wearable Recording (resampled to fixed {FS_RESAMP_HZ} Hz) \n")
            f.write(f"# DateTime: {datetime.now().isoformat()} \n")
            f.write(f"# Fs_orig_Hz: {fs_orig:.6f} \n")
            f.write(f"# Fs_out_Hz: {self.record_fs:.6f} \n")
            f.write(f"# N_raw: {n_raw} \n")
            f.write(f"# N_out: {n_out} \n")
            f.write(f"# Columns: time_s, red, ir, ax, ay, az, gx, gy, gz, hr_bpm, spo2_pct, batt_pct \n")

            # ---- Header ----
            f.write("time_s,red,ir,ax,ay,az,gx,gy,gz,hr_bpm,spo2_pct,batt_pct \n")

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

        # Debug telemetry: count BLE notifications
        self._rx_packets += 1
        self._last_rx_time = time.monotonic()

        n_bytes = len(self.byte_buf)
        n_frames = n_bytes // FRAME_SIZE

        if n_frames == 0:
            return

        # Debug telemetry: count decoded frames
        self._rx_frames += int(n_frames)

        now = time.perf_counter()

        # If recording, reconstruct per-sample timestamps within this BLE packet (BLE sends bursts)
        t_rel_first = None
        dt_samp = None
        if self.recording:
            # Defensive check: _t_rec0 should be set by on_start_recording_clicked(),
            # but if recording flag was set without proper init, handle it here
            if self._t_rec0 is None:
                # This should be rare - indicates a race condition or bug elsewhere
                with self._rec_lock:
                    if self._t_rec0 is None:  # Double-check under lock
                        self._t_rec0 = now
                        self._last_any_record_frame = now
                        self._rec_last_t = None

            fs_now = float(self.fs_est) if (self.fs_est is not None and self.fs_est > 0) else float(FW_FS_HINT_HZ)
            if fs_now <= 0:
                fs_now = float(FW_FS_HINT_HZ)
            dt_samp = 1.0 / fs_now

            last_t = self._rec_last_t
            if last_t is None:
                # Back-date the burst so the LAST frame aligns closest to 'now'
                t_rel_first = float(now - float(self._t_rec0)) - (n_frames - 1) * dt_samp
                if t_rel_first < 0:
                    t_rel_first = 0.0
            else:
                t_rel_first = float(last_t) + dt_samp

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
                # Pre-compute values outside lock to minimize lock hold time
                # reconstructed record-time for this sample (sec since record start)
                if t_rel_first is not None and dt_samp is not None:
                    t_rel = float(t_rel_first) + float(i) * float(dt_samp)
                else:
                    t_rel = float("nan")
                
                # Prepare all data first
                new_data = {
                    "ax": ax,
                    "ay": ay,
                    "az": az,
                    "gx": gx,
                    "gy": gy,
                    "gz": gz,
                    "red": red,
                    "ir": ir,
                    "hr": float(hr_i),
                    "spo2": float(spo2_i),
                    "batt": float(batt_i)
                }
                
                # Single atomic update under lock
                with self._rec_lock:
                    self.rec_idx_raw.append(self.sample_count)
                    self.rec_t_raw.append(t_rel)
                    self._rec_last_t = t_rel
                    self._last_any_record_frame = now
                    # Append all channel data
                    for key, value in new_data.items():
                        self.rec_data_raw[key].append(value)

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

        # Debug telemetry row (once per second)
        #self._update_debug_row()

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
