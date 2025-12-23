import os
import sys
import time
import struct
import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from bleak import BleakScanner, BleakClient
from PyQt5 import QtCore, QtWidgets, QtGui
import pyqtgraph as pg

# =========================
# TinZr Multi-Device Viewer
# (Data logic matches TinZrWearable.py as closely as possible)
#
# Key idea:
# - BLE notify parses frames for HUD (HR/SpO2/Batt) ALWAYS
# - Plot/record buffers are ONLY updated when streaming == True
# - Recording is synchronized on ONE shared fixed-rate time grid (record_fs).
#
# NEW (save-as-you-go):
# - While recording is ON, we continuously write synchronized rows to disk.
# - On STOP, we write remaining rows and finalize header fields (Fs_orig, N_out).
# =========================

os.environ["BLEAK_BACKEND"] = "dotnet"  # important on Windows

# ===== Make parent "examples" directory importable so we can see GUIsHelper.py =====
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
	sys.path.insert(0, PARENT_DIR)

from GUIsHelper import (
	ToggleSwitch,
	Spinner,
	BatteryWidget,
	apply_tinzr_theme,
)

# ================== BLE UUIDs & Device Filter ==================
TINZR_BLE_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
TINZR_BLE_RX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # write (PC -> device)
TINZR_BLE_TX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"  # notify (device -> PC)
DEVICE_PREFIX = "TinZr"

CMD_START = b"S"
CMD_STOP  = b"E"

# ================== Frame format (must match C++) ==============
# WearFrame packed:
#   int16 ax,ay,az (accel * 1000)
#   int16 gx,gy,gz (gyro  * 100)
#   uint32 red, ir
#   uint8 hr, spo2, batt
FRAME_STRUCT = struct.Struct("<hhhhhhIIBBB")
FRAME_SIZE   = FRAME_STRUCT.size  # 23 bytes

ACC_SCALE = 1e-3
GYR_SCALE = 1.0 / 100.0

# ================== Viewer Config (mirrors Wearable defaults) ==================
FS_RESAMP_HZ    = 200.0    # fixed output Fs for saving (and plot resample)
WINDOW_SEC      = 3.0      # seconds visible on screen
UPDATE_MS       = 10       # GUI update period
MAX_RAW_SAMPLES = 20000    # plot buffer cap (per device)

# Rate estimate window (per device)
RX_RATE_WINDOW_SEC = 1.0
FW_FS_HINT_HZ      = 250.0

# NEW: file write cadence (save-as-you-go)
WRITE_EVERY_MS      = 200   # how often we attempt to write new synchronized rows
WRITE_FLUSH_EVERY_N = 1     # flush after each write batch (keep crash-safe)


@dataclass
class DeviceInfo:
	name: str
	address: str


def _safe_name(s: str) -> str:
	s = (s or "").strip()
	out = []
	for ch in s:
		if ch.isalnum() or ch in ("-", "_"):
			out.append(ch)
		else:
			out.append("_")
	return "".join(out) if out else "TinZr"


# =========================
# Plot Window (tabs per device)
# =========================
class MultiDevicePlotWindow(QtWidgets.QMainWindow):
	def __init__(self, parent_viewer):
		super().__init__()
		self.viewer = parent_viewer

		self.setWindowTitle("TinZr Wearable")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))
		self.resize(1100, 750)
		apply_tinzr_theme(self)

		screen = QtWidgets.QApplication.primaryScreen()
		dpi = screen.logicalDotsPerInch() if screen is not None else 96.0
		# 96 dpi = baseline scale 1.0
		self._dpi_scale = dpi / 96.0

		# axis label font sizes (in points)
		self._axis_label_pt = int(round(12 * self._dpi_scale))     # y-labels
		self._time_label_pt = int(round(12 * self._dpi_scale))    # "Time (s)"

		self.signal_order = [
			"red", "ir",
			"ax", "ay", "az",
			"gx", "gy", "gz",
		]
		self.colors = {
			"red": "#ff4444",
			"ir":  "#aaaaaa",
			"ax": "#448aff",
			"ay": "#448aff",
			"az": "#448aff",
			"gx": "#33ffff",
			"gy": "#33ffff",
			"gz": "#33ffff",
		}

		self.tabs = QtWidgets.QTabWidget()
		self.setCentralWidget(self.tabs)

		self.tab_widgets = {}
		self.tab_curves  = {}
		self.tab_axes    = {}

		self.timer = QtCore.QTimer(self)
		self.timer.setTimerType(QtCore.Qt.PreciseTimer)
		self.timer.timeout.connect(self._update_all)
		self.timer.start(UPDATE_MS)

		self._rebuild_tabs()

	def _rebuild_tabs(self):
		self.tabs.clear()
		self.tab_widgets.clear()
		self.tab_curves.clear()
		self.tab_axes.clear()

		with self.viewer.state_lock:
			devs = list(self.viewer.device_order)

		for did in devs:
			row = self.viewer.rows.get(did)
			label = row.alias() if row else did

			w = QtWidgets.QWidget()
			v = QtWidgets.QVBoxLayout(w)
			v.setContentsMargins(10, 10, 10, 10)

			glw = pg.GraphicsLayoutWidget()
			v.addWidget(glw)
			glw.setBackground("#020817")

			axes = {}
			curves = {}

			for r, key in enumerate(self.signal_order):
				p = glw.addPlot(row=r, col=0)
				p.setMenuEnabled(False)
				p.setClipToView(True)
				p.disableAutoRange(axis="x")
				p.enableAutoRange(axis="y", enable=True)
				p.showGrid(x=False, y=False)

				left_axis = p.getAxis("left")
				left_axis.setStyle(showValues=False, tickLength=0)
				left_axis.setLabel(
					text=key,
					units="",
					**{
						"color": "#E0E8FF",
						"size": f"{self._axis_label_pt}pt",
					}
				)

				if r < len(self.signal_order) - 1:
					p.showAxis("bottom", False)
				else:
					bottom = p.getAxis("bottom")
					bottom.setStyle(showValues=False, tickLength=0)
					bottom.setLabel(
						text="time (s)",
						units="",
						**{
							"color": "rgba(255,255,255,160)",
							"size": f"{self._axis_label_pt}pt",
						}
					)

				curve = p.plot([], [], pen=pg.mkPen(self.colors.get(key, "#E0E8FF"), width=1.4))
				axes[key] = p
				curves[key] = curve

			self.tabs.addTab(w, label)
			self.tab_widgets[did] = w
			self.tab_axes[did] = axes
			self.tab_curves[did] = curves

	def _update_all(self):
		# Pull plot snapshot (per device)
		with self.viewer.plot_lock:
			snap = {did: dict(buf) for did, buf in self.viewer.plot_buffers.items()}
		with self.viewer.fs_lock:
			fs_snap = dict(self.viewer.fs_est)

		# time axis for display: fixed WINDOW_SEC in seconds
		n_ds = int(max(2, round(WINDOW_SEC * FS_RESAMP_HZ)))
		t_ds = np.linspace(0.0, WINDOW_SEC, n_ds, endpoint=False)

		for did, buf in snap.items():
			if did not in self.tab_curves:
				continue

			idx = np.asarray(buf.get("idx", []), dtype=float)
			if len(idx) < 4:
				continue

			fs = float(fs_snap.get(did, FW_FS_HINT_HZ))
			if fs <= 0:
				fs = float(FW_FS_HINT_HZ)

			# Build time axis from sample index (Wearable style assumption: uniform Fs)
			# Make last sample appear at WINDOW_SEC.
			t_raw = (idx - idx[-1]) / fs + WINDOW_SEC

			# Only keep last WINDOW_SEC
			t0 = t_raw[-1] - WINDOW_SEC
			ix0 = int(np.searchsorted(t_raw, t0, side="left"))
			tw = t_raw[ix0:]
			if len(tw) < 2:
				continue

			for key in self.signal_order:
				y = np.asarray(buf.get(key, []), dtype=float)
				L = min(len(t_raw), len(y))
				if L < 4:
					continue
				yw = y[-L:][ix0:]

				if len(yw) < 2 or (tw[-1] <= tw[0]):
					continue

				# Resample for smooth plotting
				y_ds = np.interp(t_ds, tw, yw)
				self.tab_curves[did][key].setData(t_ds, y_ds)
				self.tab_axes[did][key].setXRange(0.0, WINDOW_SEC, padding=0.0)


# =========================
# Device row widget
# =========================
class TinZrDeviceRow(QtWidgets.QFrame):
	connect_changed = QtCore.pyqtSignal(str, bool)
	request_remove  = QtCore.pyqtSignal(str)

	def __init__(self, device_id: str, info: DeviceInfo, alias: str = ""):
		super().__init__()
		self.device_id = device_id
		self.info = info
		self._block_toggle = False

		self.setFrameShape(QtWidgets.QFrame.StyledPanel)
		self.setStyleSheet("QFrame{border:1px solid rgba(255,255,255,40); border-radius:10px;}")

		lay = QtWidgets.QGridLayout(self)
		lay.setContentsMargins(10, 8, 10, 8)
		lay.setHorizontalSpacing(10)
		lay.setVerticalSpacing(4)

		self.ed_alias = QtWidgets.QLineEdit()
		self.ed_alias.setText(alias or "")
		self.ed_alias.setPlaceholderText("Alias (optional)")
		self.ed_alias.setMaximumWidth(220)

		self.lbl_addr = QtWidgets.QLabel(info.address)
		self.lbl_addr.setStyleSheet("color: rgba(255,255,255,150);")

		self.toggle_connect = ToggleSwitch()
		self.toggle_connect.setChecked(False)
		self.toggle_connect.toggled.connect(self._on_connect_toggled)

		self.batt = BatteryWidget()
		self.batt.setToolTip("Battery")

		self.lbl_vitals = QtWidgets.QLabel("HR: -- bpm   SpO₂: -- %")
		self.lbl_vitals.setStyleSheet("color: rgba(255,255,255,220);")

		self.lbl_rate = QtWidgets.QLabel("RX: -- Hz")
		self.lbl_rate.setStyleSheet("color: rgba(180,220,255,220); font-weight:600;")

		self.btn_remove = QtWidgets.QPushButton("✕")
		self.btn_remove.setFixedWidth(50)
		self.btn_remove.clicked.connect(lambda: self.request_remove.emit(self.device_id))
		self.btn_remove.setToolTip("Remove device from list")
		
		
		lay.addWidget(QtWidgets.QLabel("Name"), 0, 0, alignment=QtCore.Qt.AlignRight)
		lay.addWidget(self.ed_alias, 0, 1, 1, 2)

		lay.addWidget(QtWidgets.QLabel("Addr"), 1, 0, alignment=QtCore.Qt.AlignRight)
		lay.addWidget(self.lbl_addr, 1, 1, 1, 2)

		lay.addWidget(QtWidgets.QLabel("Connect"), 0, 3, alignment=QtCore.Qt.AlignRight)
		lay.addWidget(self.toggle_connect, 0, 4, alignment=QtCore.Qt.AlignLeft)

		lay.addWidget(self.batt, 0, 5, alignment=QtCore.Qt.AlignRight)

		vbox = QtWidgets.QVBoxLayout()
		vbox.setContentsMargins(0, 0, 0, 0)
		vbox.setSpacing(3)
		vbox.addWidget(self.lbl_vitals, alignment=QtCore.Qt.AlignRight)
		vbox.addWidget(self.lbl_rate, alignment=QtCore.Qt.AlignRight)
		w = QtWidgets.QWidget()
		w.setLayout(vbox)
		lay.addWidget(w, 1, 5, alignment=QtCore.Qt.AlignRight)

		lay.addWidget(self.btn_remove, 0, 6, 2, 1, alignment=QtCore.Qt.AlignVCenter)

	def alias(self) -> str:
		return self.ed_alias.text().strip() or self.info.name or self.info.address

	def set_vitals(self, hr: int, spo2: int, batt: int):
		hr_text = f"HR: {int(hr)} bpm" if (hr and hr > 0) else "HR: -- bpm"
		sp_text = f"SpO₂: {int(spo2)} %" if (spo2 and spo2 > 0) else "SpO₂: -- %"
		self.lbl_vitals.setText(f"{hr_text}   {sp_text}")

		if batt is not None and batt >= 0:
			self.batt.setLevel(int(batt))
		else:
			self.batt.setLevel(None)

	def set_rate(self, hz: float):
		if hz is None or hz <= 0:
			self.lbl_rate.setText("RX: -- Hz")
		else:
			self.lbl_rate.setText(f"RX: {hz:.1f} Hz")

	def set_toggle(self, checked: bool):
		self._block_toggle = True
		try:
			self.toggle_connect.setChecked(bool(checked))
		finally:
			self._block_toggle = False

	def _on_connect_toggled(self, checked: bool):
		if self._block_toggle:
			return
		self.connect_changed.emit(self.device_id, checked)


# =========================
# Main Multi Viewer
# =========================
class MultiTinZrViewer(QtWidgets.QWidget):
	scan_finished = QtCore.pyqtSignal(object, object)			# (devices, error)
	vitals_update = QtCore.pyqtSignal(str, int, int, int)		# did, hr, spo2, batt
	rate_update   = QtCore.pyqtSignal(str, float)				# did, hz
	status_update = QtCore.pyqtSignal(str)

	def __init__(self):
		super().__init__()

		self.setWindowTitle("TinZr (Multi) Wearable")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))
		self.setFixedSize(900, 700)
		self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)
		apply_tinzr_theme(self)

		# ===== BLE event loop thread (shared across all clients) =====
		self.loop = asyncio.new_event_loop()
		self.ble_thread = threading.Thread(target=self._run_ble_loop, daemon=True)
		self.ble_thread.start()

		# ===== State =====
		self.discovered: list[DeviceInfo] = []
		self.clients = {}			# did -> BleakClient
		self.byte_buf = {}			# did -> bytearray
		self.rows = {}				# did -> TinZrDeviceRow
		self.connected = set()		# did
		self.streaming = False
		self._batt_cache = {}  # did -> last battery %
		self.recording = False

		self.state_lock = threading.Lock()
		self.device_order = []		# list of did (UI order)

		# ===== Wearable-style per-device raw buffers =====
		self.data_lock = threading.Lock()
		self.sample_count = {}		# did -> int
		self.idx_raw = {}			# did -> list[int]
		self.data_raw = {}			# did -> dict[key] -> list[float]
		self.rec_idx_raw = {}		# did -> list[int]
		self.rec_data_raw = {}		# did -> dict[key] -> list[float]

		self.rec_keys = ["red","ir","ax","ay","az","gx","gy","gz","hr","spo2","batt"]

		# ===== Fs estimate (per device) =====
		self.fs_lock = threading.Lock()
		self.fs_est = {}			# did -> Hz (updated from RX estimate)

		# ===== Real RX sampling rate stats (per device) =====
		self.rate_lock = threading.Lock()
		self.rx_win_t0 = {}
		self.rx_win_cnt = {}
		self.rx_hz = {}
		self.rx_window_sec = float(RX_RATE_WINDOW_SEC)

		# ===== Recording output =====
		self.record_path = None
		self.record_file = None
		self.record_fs = float(FS_RESAMP_HZ)  # fixed output grid
		self.rec_devices_order = []
		self.rec_alias_map = {}

		# NEW: incremental (save-as-you-go) writer state
		self._write_cursor = 0
		self._csv_header_written = False
		self._flush_counter = 0

		# ===== Plot buffers (snapshot-friendly) =====
		self.plot_lock = threading.Lock()
		self.plot_buffers = {}		# did -> dict[str] -> list[float]
		self.plot_window = None

		# ===== Signals =====
		self.scan_finished.connect(self._handle_scan_result)
		self.vitals_update.connect(self._on_vitals_update)
		self.rate_update.connect(self._on_rate_update)
		self.status_update.connect(self._set_status)

		# ===== UI =====
		main_layout = QtWidgets.QVBoxLayout(self)
		main_layout.setContentsMargins(16, 12, 16, 12)
		main_layout.setSpacing(8)

		header = QtWidgets.QWidget()
		h_lay = QtWidgets.QHBoxLayout(header)
		h_lay.setContentsMargins(0, 0, 0, 4)
		h_lay.setSpacing(10)

		title = QtWidgets.QLabel("TinZr Multi-Device")
		title.setStyleSheet("font-size: 16pt; font-weight: 600; color: #E3F2FD;")
		h_lay.addWidget(title)
		h_lay.addStretch(1)
		main_layout.addWidget(header)

		ctrl = QtWidgets.QWidget()
		grid = QtWidgets.QGridLayout(ctrl)
		grid.setContentsMargins(0, 0, 0, 0)
		grid.setHorizontalSpacing(16)
		grid.setVerticalSpacing(8)

		r = 0
		self.btn_scan = QtWidgets.QPushButton("Find Devices")
		self.btn_scan.clicked.connect(self.on_scan_clicked)
		grid.addWidget(self.btn_scan, r, 0)

		self.spinner = Spinner(radius=8, line_width=2, parent=self.btn_scan)
		self.spinner.raise_()
		self.btn_scan.installEventFilter(self)
		self._center_spinner_on_button()

		self.combo_found = QtWidgets.QComboBox()
		self.combo_found.setMinimumWidth(280)
		grid.addWidget(self.combo_found, r, 1, 1, 3)

		self.ed_new_alias = QtWidgets.QLineEdit()
		self.ed_new_alias.setPlaceholderText("Alias for selected device (optional)")
		grid.addWidget(self.ed_new_alias, r, 4, 1, 2)

		self.btn_add = QtWidgets.QPushButton("Add")
		self.btn_add.clicked.connect(self.on_add_clicked)
		grid.addWidget(self.btn_add, r, 6)

		r += 1
		grid.addWidget(QtWidgets.QLabel("Stream"), r, 0, alignment=QtCore.Qt.AlignRight)
		self.toggle_stream = ToggleSwitch()
		self.toggle_stream.setChecked(False)
		self.toggle_stream.toggled.connect(self.on_stream_toggled)
		self.toggle_stream.setEnabled(False)
		grid.addWidget(self.toggle_stream, r, 1, alignment=QtCore.Qt.AlignLeft)

		grid.addWidget(QtWidgets.QLabel("Record (wide CSV)"), r, 2, alignment=QtCore.Qt.AlignRight)
		self.toggle_record = ToggleSwitch()
		self.toggle_record.setChecked(False)
		self.toggle_record.toggled.connect(self.on_record_toggled)
		self.toggle_record.setEnabled(False)
		grid.addWidget(self.toggle_record, r, 3, alignment=QtCore.Qt.AlignLeft)

		self.btn_plot = QtWidgets.QPushButton("Show Plots")
		self.btn_plot.clicked.connect(self.on_plot_clicked)
		self.btn_plot.setEnabled(True)
		grid.addWidget(self.btn_plot, r, 4)

		self.lbl_out = QtWidgets.QLabel(
			f"Fixed Fs: {self.record_fs:.1f} Hz"
		)
		self.lbl_out.setStyleSheet("color: rgba(200,240,255,200);")
		grid.addWidget(self.lbl_out, r, 5, 1, 2, alignment=QtCore.Qt.AlignRight)

		main_layout.addWidget(ctrl)

		self.scroll = QtWidgets.QScrollArea()
		self.scroll.setWidgetResizable(True)
		self.scroll.setStyleSheet("QScrollArea{border:none;}")

		self.devices_container = QtWidgets.QWidget()
		self.devices_layout = QtWidgets.QVBoxLayout(self.devices_container)
		self.devices_layout.setContentsMargins(0, 0, 0, 0)
		self.devices_layout.setSpacing(8)
		self.devices_layout.addStretch(1)

		self.scroll.setWidget(self.devices_container)
		main_layout.addWidget(self.scroll, stretch=1)

		self.label_status = QtWidgets.QLabel("Status: Idle")
		self.label_status.setObjectName("statusLabel")
		self.label_status.setStyleSheet("color: rgba(255,255,255,170);")
		main_layout.addWidget(self.label_status)

		# GUI timer only for plot refresh (plot window pulls snapshots)
		self.gui_timer = QtCore.QTimer(self)
		self.gui_timer.setTimerType(QtCore.Qt.PreciseTimer)
		self.gui_timer.timeout.connect(self._gui_tick)
		self.gui_timer.start(UPDATE_MS)

		# NEW: writer timer (save-as-you-go)
		self.writer_timer = QtCore.QTimer(self)
		self.writer_timer.setTimerType(QtCore.Qt.PreciseTimer)
		self.writer_timer.timeout.connect(self._writer_tick)
		self.writer_timer.start(WRITE_EVERY_MS)

	def eventFilter(self, obj, event):
		if obj is self.btn_scan and event.type() == QtCore.QEvent.Resize:
			self._center_spinner_on_button()
		return super().eventFilter(obj, event)

	def _center_spinner_on_button(self):
		if not self.spinner or not self.btn_scan:
			return
		brect = self.btn_scan.rect()
		srect = self.spinner.rect()
		x = (brect.width() - srect.width()) // 2
		y = (brect.height() - srect.height()) // 2
		self.spinner.move(x, y)

	# ============ BLE loop thread ============
	def _run_ble_loop(self):
		asyncio.set_event_loop(self.loop)
		self.loop.run_forever()

	# ============ Status ============
	def _set_status(self, text: str):
		self.label_status.setText(f"Status: {text}")

	# ============ Plot ============
	def on_plot_clicked(self):
		if self.plot_window is None:
			self.plot_window = MultiDevicePlotWindow(self)
		else:
			self.plot_window._rebuild_tabs()
		self.plot_window.show()
		self.plot_window.raise_()
		self.plot_window.activateWindow()

	def _gui_tick(self):
		# Keep plot_buffers in sync with latest raw buffers (lightweight snapshot for plot window)
		with self.data_lock:
			devs = list(self.device_order)
			for did in devs:
				if did not in self.idx_raw:
					continue
				idx = self.idx_raw[did]
				if len(idx) == 0:
					continue

				# cap plot raw buffers like Wearable
				if len(idx) > MAX_RAW_SAMPLES:
					trim = len(idx) - MAX_RAW_SAMPLES
					self.idx_raw[did] = idx[trim:]
					for k in self.data_raw[did]:
						self.data_raw[did][k] = self.data_raw[did][k][trim:]

				# snapshot into plot buffer
				with self.plot_lock:
					pb = self.plot_buffers.get(did)
					if pb is None:
						pb = {"idx": [], "ax": [], "ay": [], "az": [], "gx": [], "gy": [], "gz": [], "red": [], "ir": []}
						self.plot_buffers[did] = pb

					with self.fs_lock:
						fs = float(self.fs_est.get(did, FW_FS_HINT_HZ))
					keep = int(max(100, round((WINDOW_SEC * fs) + 200)))

					pb["idx"] = self.idx_raw[did][-keep:]
					for k in ("ax","ay","az","gx","gy","gz","red","ir"):
						pb[k] = self.data_raw[did][k][-keep:]

	# ============ Scan ============
	def on_scan_clicked(self):
		self.btn_scan.setEnabled(False)
		self.spinner.start()
		self.status_update.emit("Scanning BLE...")
		asyncio.run_coroutine_threadsafe(self._scan_ble(), self.loop)

	async def _scan_ble(self):
		try:
			devs = await BleakScanner.discover(timeout=5.0)
			found = []
			for d in devs:
				name = (d.name or "").strip()
				if not name:
					continue
				if not name.startswith(DEVICE_PREFIX):
					continue
				found.append(DeviceInfo(name=name, address=d.address))
			err = None
			self.scan_finished.emit(found, err)
		except Exception as e:
			self.scan_finished.emit([], e)

	def _handle_scan_result(self, devices, error):
		self.spinner.stop()
		self.btn_scan.setEnabled(True)

		self.discovered = devices or []
		self.combo_found.clear()
		for info in self.discovered:
			self.combo_found.addItem(f"{info.name}   [{info.address}]", userData=info)

		if error:
			self.status_update.emit(f"Scan error: {error}")
		else:
			self.status_update.emit(f"Found {len(self.discovered)} TinZr device(s).")

	# ============ Add device row ============
	def on_add_clicked(self):
		info = self.combo_found.currentData()
		if not info:
			return

		did = info.address  # stable unique id
		if did in self.rows:
			self.status_update.emit("Device already added.")
			return

		alias = self.ed_new_alias.text().strip()
		if not alias:
			alias = info.name

		row = TinZrDeviceRow(did, info, alias=alias)
		row.connect_changed.connect(self._on_row_connect_changed)
		row.request_remove.connect(self._on_row_remove)
		self.rows[did] = row

		with self.state_lock:
			self.device_order.append(did)

		self.devices_layout.insertWidget(self.devices_layout.count() - 1, row)

		self.byte_buf[did] = bytearray()

		with self.data_lock:
			self.sample_count[did] = 0
			self.idx_raw[did] = []
			self.data_raw[did] = {k: [] for k in ("ax","ay","az","gx","gy","gz","red","ir")}
			self.rec_idx_raw[did] = []
			self.rec_data_raw[did] = {k: [] for k in self.rec_keys}

		with self.fs_lock:
			self.fs_est[did] = float(FW_FS_HINT_HZ)

		with self.rate_lock:
			self.rx_win_t0[did] = time.perf_counter()
			self.rx_win_cnt[did] = 0
			self.rx_hz[did] = 0.0

		with self.plot_lock:
			self.plot_buffers[did] = {"idx": [], "ax": [], "ay": [], "az": [], "gx": [], "gy": [], "gz": [], "red": [], "ir": []}

		self.toggle_stream.setEnabled(True)
		self.toggle_record.setEnabled(True)

		if self.plot_window is not None:
			self.plot_window._rebuild_tabs()

		self.status_update.emit(f"Added: {row.alias()}")

	
	def _on_row_remove(self, did: str):
		# If connected, disconnect FIRST (must happen before we pop self.clients[did])
		if did in self.connected or did in self.clients:
			row = self.rows.get(did)
			if row:
				# prevent double-click / reconnect spam
				row.btn_remove.setEnabled(False)
				row.toggle_connect.setEnabled(False)

			# Fire STOP first (best effort), then disconnect and WAIT briefly
			try:
				fut_stop = asyncio.run_coroutine_threadsafe(self._send_cmd(did, CMD_STOP), self.loop)
				try:
					fut_stop.result(timeout=0.4)
				except Exception:
					pass
			except Exception:
				pass

			try:
				fut_disc = asyncio.run_coroutine_threadsafe(self._disconnect_device_async(did), self.loop)
				try:
					fut_disc.result(timeout=2.0)
				except Exception:
					# even if timeout, continue cleanup
					pass
			except Exception:
				pass

		# NOW it is safe to remove UI + cleanup local state
		row = self.rows.pop(did, None)
		if row:
			row.setParent(None)
			row.deleteLater()

		with self.state_lock:
			if did in self.device_order:
				self.device_order.remove(did)

		self._batt_cache.pop(did, None)

		# Only now: drop client and buffers
		self.byte_buf.pop(did, None)
		self.clients.pop(did, None)
		self.connected.discard(did)

		with self.data_lock:
			self.sample_count.pop(did, None)
			self.idx_raw.pop(did, None)
			self.data_raw.pop(did, None)
			self.rec_idx_raw.pop(did, None)
			self.rec_data_raw.pop(did, None)

		with self.fs_lock:
			self.fs_est.pop(did, None)

		with self.rate_lock:
			self.rx_win_t0.pop(did, None)
			self.rx_win_cnt.pop(did, None)
			self.rx_hz.pop(did, None)

		with self.plot_lock:
			self.plot_buffers.pop(did, None)

		if self.plot_window is not None:
			self.plot_window._rebuild_tabs()

		if len(self.rows) == 0:
			self.toggle_stream.setChecked(False)
			self.toggle_stream.setEnabled(False)
			self.toggle_record.setChecked(False)
			self.toggle_record.setEnabled(False)

		self.status_update.emit("Removed device (disconnected).")

		
	
	# ============ Connect / disconnect ============
	def _on_row_connect_changed(self, did: str, connect: bool):
		if connect:
			self.status_update.emit(f"Connecting: {self.rows[did].alias()} ...")
			asyncio.run_coroutine_threadsafe(self._connect_device_async(did), self.loop)
		else:
			self.status_update.emit(f"Disconnecting: {self.rows[did].alias()} ...")
			asyncio.run_coroutine_threadsafe(self._disconnect_device_async(did), self.loop)

	async def _connect_device_async(self, did: str):
		if did in self.connected:
			self.status_update.emit(f"Connected: {self.rows[did].alias()}")
			return

		info = self.rows[did].info
		client = BleakClient(info.address, timeout=10.0)

		try:
			await client.connect()
			await client.start_notify(TINZR_BLE_TX_CHAR_UUID, lambda sender, data: self._on_notify(did, data))

			self.clients[did] = client
			self.connected.add(did)

			self.rows[did].set_toggle(True)
			self.status_update.emit(f"Connected: {self.rows[did].alias()}")

		except Exception as e:
			try:
				await client.disconnect()
			except Exception:
				pass
			if did in self.rows:
				self.rows[did].set_toggle(False)
			self.status_update.emit(f"Connect failed: {e}")

	def _disconnect_device(self, did: str):
		asyncio.run_coroutine_threadsafe(self._disconnect_device_async(did), self.loop)

	async def _disconnect_device_async(self, did: str):
		client = self.clients.get(did)
		if not client:
			if did in self.rows:
				self.rows[did].set_toggle(False)
			self.connected.discard(did)
			return

		try:
			if self.streaming:
				try:
					await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_STOP, response=False)
				except Exception:
					pass

			try:
				await client.stop_notify(TINZR_BLE_TX_CHAR_UUID)
			except Exception:
				pass

			await client.disconnect()
		except Exception:
			pass

		self.clients.pop(did, None)
		self.connected.discard(did)

		if did in self.rows:
			self.rows[did].set_toggle(False)

		self.status_update.emit(f"Disconnected: {self.rows[did].alias() if did in self.rows else did}")

	# ============ Stream control ============
	def on_stream_toggled(self, checked: bool):
		if checked:
			self._start_stream_all()
		else:
			self._stop_stream_all()

	def _start_stream_all(self):
		if self.streaming:
			return
		if len(self.connected) == 0:
			self.toggle_stream.setChecked(False)
			self.status_update.emit("No connected devices.")
			return

		self.streaming = True
		self.status_update.emit("Streaming: starting...")

		with self.data_lock:
			for did in self.connected:
				self.sample_count[did] = 0
				self.idx_raw[did].clear()
				for k in self.data_raw[did]:
					self.data_raw[did][k].clear()

		for did in list(self.connected):
			asyncio.run_coroutine_threadsafe(self._send_cmd(did, CMD_START), self.loop)

		self.status_update.emit("Streaming: ON")

	def _stop_stream_all(self):
		if not self.streaming:
			return
		self.streaming = False
		self.status_update.emit("Streaming: stopping...")
		for did in list(self.connected):
			asyncio.run_coroutine_threadsafe(self._send_cmd(did, CMD_STOP), self.loop)
		self.status_update.emit("Streaming: OFF")

	async def _send_cmd(self, did: str, cmd: bytes):
		client = self.clients.get(did)
		if not client:
			return
		try:
			await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, cmd, response=False)
		except Exception:
			pass

	# ============ Recording ============
	def on_record_toggled(self, checked: bool):
		if checked:
			self._start_recording()
		else:
			self._stop_recording()

	def _start_recording(self):
		if self.recording:
			return
		if len(self.rows) == 0:
			self.toggle_record.setChecked(False)
			self.status_update.emit("Add devices first.")
			return

		ts = datetime.now().strftime("%Y%m%d_%H%M%S")
		default_name = f"TinZrMultiWearable_recording_{ts}.csv"

		start_dir = os.path.dirname(os.path.abspath(__file__))
		fpath, _ = QtWidgets.QFileDialog.getSaveFileName(
			self,
			"Save recording CSV",
			os.path.join(start_dir, default_name),
			"CSV Files (*.csv);;All Files (*)"
		)

		if not fpath:
			self.toggle_record.setChecked(False)
			self.status_update.emit("Recording cancelled.")
			return

		if not fpath.lower().endswith(".csv"):
			fpath += ".csv"

		try:
			self.record_file = open(fpath, "w", newline="")
			self.record_path = fpath
		except Exception as e:
			self.toggle_record.setChecked(False)
			self.status_update.emit(f"Record open failed: {e}")
			return

		with self.state_lock:
			self.rec_devices_order = list(self.device_order)
		self.rec_alias_map = {did: self.rows[did].alias() for did in self.rec_devices_order if did in self.rows}

		# clear rec buffers (Wearable style)
		with self.data_lock:
			for did in self.rec_devices_order:
				self.rec_idx_raw[did].clear()
				for k in self.rec_data_raw[did]:
					self.rec_data_raw[did][k].clear()

		# NEW: write header placeholders now (data will be appended immediately)
		self._write_cursor = 0
		self._csv_header_written = False
		self._flush_counter = 0
		self._write_header_placeholders()

		self.recording = True
		self.status_update.emit(f"Recording: ON (save-as-you-go + sync grid) → {fpath}")

	def _stop_recording(self):
		if not self.recording:
			return
		self.recording = False

		# Write any remaining rows before finalizing
		self._write_available_rows(final=True)

		f = self.record_file
		path = self.record_path
		self.record_file = None

		if f is None or path is None:
			self.status_update.emit("Recording stopped (no file).")
			return

		try:
			f.close()
		except Exception:
			pass

		# Finalize header (Fs_orig_Hz + N_out) without touching data rows
		try:
			self._finalize_csv_header(path)
			self.status_update.emit(f"Recording saved (synced) → {path}")
		except Exception as e:
			self.status_update.emit(f"Recording finalize error: {e}")

		# Clear rec buffers (Wearable style)
		with self.data_lock:
			for did in list(self.rec_devices_order):
				self.rec_idx_raw[did].clear()
				for k in self.rec_data_raw[did]:
					self.rec_data_raw[did][k].clear()

	# ============ Save-as-you-go writer ============
	def _writer_tick(self):
		# Keep file updated while recording (do NOT do heavy work in BLE notify thread)
		if not self.recording:
			return
		self._write_available_rows(final=False)

	def _write_header_placeholders(self):
		f = self.record_file
		if f is None:
			return

		devs = list(self.rec_devices_order)

		f.write("# TinZr Multi Wearable Recording (synchronized)\n")
		f.write(f"# DateTime: {datetime.now().isoformat()}\n")
		f.write("# __FS_ORIG_LINES__\n")          # placeholder (filled on stop)
		f.write(f"# Fs_out_Hz: {self.record_fs:.6f}\n")
		f.write("# N_out: __PLACEHOLDER__\n")     # placeholder (filled on stop)
		f.write("# Columns: time_s + per-device channels (red, ir, ax, ay, az, gx, gy, gz, hr_bpm, spo2_pct, batt_pct)\n")

		cols = ["time_s"]
		for did in devs:
			a = _safe_name(self.rec_alias_map.get(did, did))
			cols += [
				f"{a}_red", f"{a}_ir",
				f"{a}_ax", f"{a}_ay", f"{a}_az",
				f"{a}_gx", f"{a}_gy", f"{a}_gz",
				f"{a}_hr_bpm", f"{a}_spo2_pct", f"{a}_batt_pct",
			]
		f.write(",".join(cols) + "\n")
		f.write("# __DATA_START__\n")
		f.flush()

		self._csv_header_written = True

	def _write_available_rows(self, final: bool = False):
		"""
		Write synchronized rows that are currently available for ALL devices,
		on the shared fixed-rate grid (record_fs).
		We only write rows up to the current common overlap across devices.
		"""
		f = self.record_file
		if f is None or not self._csv_header_written:
			return

		with self.state_lock:
			devs = list(self.rec_devices_order)
		if not devs:
			return

		# Snapshot buffers + Fs under lock (short)
		with self.data_lock:
			snap = {}
			for did in devs:
				idx = list(self.rec_idx_raw.get(did, []))
				if len(idx) < 2:
					# not enough yet
					return
				snap[did] = {
					"idx": idx,
					"data": {k: list(self.rec_data_raw[did].get(k, [])) for k in self.rec_keys}
				}

		with self.fs_lock:
			fs_snap = dict(self.fs_est)

		# Determine common overlap end-time (best effort)
		t_ends = []
		fs_origs = {}
		for did in devs:
			idx = np.asarray(snap[did]["idx"], dtype=float)
			fs = float(fs_snap.get(did, FW_FS_HINT_HZ))
			if fs <= 0:
				fs = float(FW_FS_HINT_HZ)
			fs_origs[did] = fs
			t_raw = (idx - idx[0]) / fs
			t_ends.append(float(t_raw[-1]))

		if not t_ends:
			return

		t_end_common = min(t_ends)

		# How many output rows exist so far?
		n_out = int(t_end_common * self.record_fs)
		if n_out <= self._write_cursor:
			return

		dt = 1.0 / float(self.record_fs)

		# Precompute per-device time axes once per tick
		dev_axes = {}
		for did in devs:
			fs = fs_origs[did]
			idx = np.asarray(snap[did]["idx"], dtype=float)
			t_raw = (idx - idx[0]) / fs
			dev_axes[did] = t_raw

		def zoh_at(t, t_raw, vals):
			# last sample at or before t
			j = int(np.searchsorted(t_raw, t, side="right") - 1)
			if j < 0:
				j = 0
			if j >= len(vals):
				j = len(vals) - 1
			return float(vals[j])

		# Write new rows [cursor .. n_out)
		for i in range(self._write_cursor, n_out):
			t = i * dt
			row = [f"{t:.6f}"]

			for did in devs:
				t_raw = dev_axes[did]

				for k in self.rec_keys:
					vals = np.asarray(snap[did]["data"][k], dtype=float)
					L = min(len(vals), len(t_raw))
					if L <= 0:
						row.append("")
						continue

					vv = vals[:L]
					tt = t_raw[:L]

					if k in ("hr", "spo2", "batt"):
						v = zoh_at(t, tt, vv)
						row.append(f"{v:.2f}")
					else:
						v = float(np.interp(t, tt, vv))
						row.append(f"{v:.6f}")

			# IMPORTANT: match your column ordering in the original file:
			# Your original output order per device was:
			# red, ir, ax, ay, az, gx, gy, gz, hr, spo2, batt
			# But rec_keys is ["red","ir","ax","ay","az","gx","gy","gz","hr","spo2","batt"],
			# and we wrote in that order above, which matches the original.
			#
			# However: row currently includes ALL keys sequentially. We need to format per device
			# exactly like original. Above loop appended keys in rec_keys order, which is correct.

			f.write(",".join(row) + "\n")

		self._write_cursor = n_out

		# flush frequently (crash-safe)
		self._flush_counter += 1
		if self._flush_counter >= WRITE_FLUSH_EVERY_N or final:
			try:
				f.flush()
			except Exception:
				pass
			self._flush_counter = 0

	def _finalize_csv_header(self, path: str):
		"""
		Rewrite ONLY the header placeholders (Fs_orig lines, N_out),
		leaving the already-written data rows untouched.
		"""
		tmp_path = path + ".tmp"

		# Determine fs_origs and final n_out from buffers (same idea as stop logic)
		with self.state_lock:
			devs = list(self.rec_devices_order)
		with self.fs_lock:
			fs_snap = dict(self.fs_est)

		# compute final n_out based on overlap currently written (cursor)
		final_n_out = int(self._write_cursor)

		fs_origs = {}
		for did in devs:
			fs = float(fs_snap.get(did, FW_FS_HINT_HZ))
			if fs <= 0:
				fs = float(FW_FS_HINT_HZ)
			fs_origs[did] = fs

		# Rewrite file: replace placeholder lines before __DATA_START__
		with open(path, "r", newline="") as fin, open(tmp_path, "w", newline="") as fout:
			in_header = True
			for line in fin:
				if in_header:
					if line.strip() == "# __FS_ORIG_LINES__":
						# emit fs_orig lines (same style as original stop header)
						for did in devs:
							alias = _safe_name(self.rec_alias_map.get(did, did))
							fout.write(f"# {alias}_Fs_orig_Hz: {fs_origs[did]:.6f}\n")
						continue

					if line.startswith("# N_out:"):
						fout.write(f"# N_out: {final_n_out:d}\n")
						continue

					fout.write(line)

					if line.strip() == "# __DATA_START__":
						in_header = False
				else:
					fout.write(line)

		# Atomic-ish replace
		try:
			os.replace(tmp_path, path)
		except Exception:
			# fallback
			try:
				os.remove(path)
			except Exception:
				pass
			os.rename(tmp_path, path)

	# ============ Notify handler ============
	def _on_notify(self, did: str, data: bytes):
		# Called from Bleak notification context (in event loop thread)
		b = self.byte_buf.get(did)
		if b is None:
			self.byte_buf[did] = bytearray()
			b = self.byte_buf[did]

		b.extend(data)

		n_bytes = len(b)
		n_frames = n_bytes // FRAME_SIZE
		if n_frames == 0:
			return

		now = time.perf_counter()
		self._rx_rate_update(did, n_frames, now)

		for i in range(n_frames):
			start = i * FRAME_SIZE
			chunk = b[start:start + FRAME_SIZE]

			(
				ax_i, ay_i, az_i,
				gx_i, gy_i, gz_i,
				red_i, ir_i,
				hr_i, spo2_i,
				batt_i
			) = FRAME_STRUCT.unpack(chunk)

			# HUD always
			self.vitals_update.emit(did, int(hr_i), int(spo2_i), int(batt_i))

			if not self.streaming:
				continue

			ax = ax_i * ACC_SCALE
			ay = ay_i * ACC_SCALE
			az = az_i * ACC_SCALE
			gx = gx_i * GYR_SCALE
			gy = gy_i * GYR_SCALE
			gz = gz_i * GYR_SCALE
			red = float(red_i)
			ir  = float(ir_i)

			with self.data_lock:
				self.sample_count[did] = int(self.sample_count.get(did, 0)) + 1
				sc = self.sample_count[did]

				self.idx_raw[did].append(sc)
				self.data_raw[did]["ax"].append(ax)
				self.data_raw[did]["ay"].append(ay)
				self.data_raw[did]["az"].append(az)
				self.data_raw[did]["gx"].append(gx)
				self.data_raw[did]["gy"].append(gy)
				self.data_raw[did]["gz"].append(gz)
				self.data_raw[did]["red"].append(red)
				self.data_raw[did]["ir"].append(ir)

				# recording buffers
				if self.recording:
					self.rec_idx_raw[did].append(sc)
					self.rec_data_raw[did]["ax"].append(ax)
					self.rec_data_raw[did]["ay"].append(ay)
					self.rec_data_raw[did]["az"].append(az)
					self.rec_data_raw[did]["gx"].append(gx)
					self.rec_data_raw[did]["gy"].append(gy)
					self.rec_data_raw[did]["gz"].append(gz)
					self.rec_data_raw[did]["red"].append(red)
					self.rec_data_raw[did]["ir"].append(ir)
					self.rec_data_raw[did]["hr"].append(float(hr_i))
					self.rec_data_raw[did]["spo2"].append(float(spo2_i))
					self.rec_data_raw[did]["batt"].append(float(batt_i))

		remaining = n_bytes - n_frames * FRAME_SIZE
		if remaining > 0:
			self.byte_buf[did] = b[-remaining:]
		else:
			self.byte_buf[did].clear()

	def _rx_rate_update(self, did: str, frames: int, now: float):
		with self.rate_lock:
			t0 = self.rx_win_t0.get(did, now)
			cnt = self.rx_win_cnt.get(did, 0)

			cnt += int(frames)
			dt = now - t0

			if dt >= self.rx_window_sec:
				hz = float(cnt) / max(dt, 1e-6)
				self.rx_hz[did] = hz
				self.rx_win_t0[did] = now
				self.rx_win_cnt[did] = 0
				self.rate_update.emit(did, hz)

				target_fs = max(10.0, min(1000.0, hz))
				with self.fs_lock:
					prev = float(self.fs_est.get(did, float(FW_FS_HINT_HZ)))
					self.fs_est[did] = 0.90 * prev + 0.10 * target_fs
			else:
				self.rx_win_cnt[did] = cnt

	# ============ UI update slots ============
	def _on_vitals_update(self, did: str, hr: int, spo2: int, batt: int):
		try:
			self._batt_cache[did] = int(batt)
		except Exception:
			self._batt_cache[did] = -1

		row = self.rows.get(did)
		if row:
			row.set_vitals(hr, spo2, batt)

	def _on_rate_update(self, did: str, hz: float):
		row = self.rows.get(did)
		if row:
			row.set_rate(hz)

	def on_batt_clicked(self):
		lines = []
		for did in list(self.device_order):
			row = self.rows.get(did)
			if not row:
				continue
			name = row.alias()
			lines.append(f"• {name}")

		msg = "\n".join(lines) if lines else "No devices added."
		QtWidgets.QMessageBox.information(self, "Devices", msg)

	def closeEvent(self, event: QtGui.QCloseEvent):
		"""
		Gracefully disconnect all devices when the app window closes.
		"""
		try:
			self.status_update.emit("Closing: disconnecting devices...")

			# Stop timers to prevent further UI/writer work
			try:
				self.gui_timer.stop()
			except Exception:
				pass
			try:
				if hasattr(self, "writer_timer"):
					self.writer_timer.stop()
			except Exception:
				pass

			# Best-effort: stop streaming first
			try:
				if getattr(self, "streaming", False):
					self.streaming = False
					for did in list(self.connected):
						asyncio.run_coroutine_threadsafe(self._send_cmd(did, CMD_STOP), self.loop)
			except Exception:
				pass

			# Disconnect all clients (async, but we wait a short time)
			self._disconnect_all_on_close(timeout_sec=2.0)

			# Stop the BLE event loop thread cleanly
			try:
				if self.loop and self.loop.is_running():
					self.loop.call_soon_threadsafe(self.loop.stop)
			except Exception:
				pass

		except Exception:
			# Never block window close due to disconnect issues
			pass

		event.accept()


	def _disconnect_all_on_close(self, timeout_sec: float = 2.0):
		"""
		Schedule disconnect for all devices and wait briefly.
		"""
		try:
			dids = []
			with self.state_lock:
				dids = list(self.device_order)

			futures = []
			for did in dids:
				# Only disconnect those with a client
				if did in self.clients:
					futures.append(asyncio.run_coroutine_threadsafe(self._disconnect_device_async(did), self.loop))

			# Wait (briefly) so OS doesn't keep BLE connections alive
			t0 = time.time()
			for fut in futures:
				remaining = max(0.05, timeout_sec - (time.time() - t0))
				try:
					fut.result(timeout=remaining)
				except Exception:
					pass
		except Exception:
			pass


def main():
	if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
	if hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

	app = QtWidgets.QApplication(sys.argv)
	pg.setConfigOptions(antialias=True)

	w = MultiTinZrViewer()
	w.show()

	sys.exit(app.exec_())


if __name__ == "__main__":
	main()
