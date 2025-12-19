import os
import sys
import time
import struct
import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime
from collections import deque

import numpy as np
from bleak import BleakScanner, BleakClient
from PyQt5 import QtCore, QtWidgets, QtGui
import pyqtgraph as pg

# =========================
# TinZr Multi-Device Viewer
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
TINZR_BLE_RX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
TINZR_BLE_TX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"

DEVICE_PREFIX = "TinZr"

# Firmware commands (adjust if your firmware differs)
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

# accel & gyro scales (inverse of firmware scaling)
ACC_SCALE = 1e-3
GYR_SCALE = 1.0 / 100.0

# ================== FIXED OUTPUT CONFIG ==================
FS_OUT_HZ            = 150.0	# <- your fixed output rate (saved + plotted)
SYNC_TIMER_MS        = int(round(1000.0 / FS_OUT_HZ))
FIFO_MAX_SECONDS     = 6.0		# raw fifo span stored per device
FIFO_MAXLEN          = int(FIFO_MAX_SECONDS * 300.0)  # safe upper bound

# Real RX-rate window
RX_RATE_WINDOW_SEC   = 1.0

# If firmware sampling is around this, it helps timestamp reconstruction
FW_FS_HINT_HZ        = 250.0

# Plot window
PLOT_WINDOW_SEC      = 4.0
PLOT_UPDATE_MS       = 20


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
		self.setWindowTitle("TinZr Live Plots (Fixed-Rate)")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))
		self.resize(1100, 750)

		apply_tinzr_theme(self)

		self.tabs = QtWidgets.QTabWidget()
		self.setCentralWidget(self.tabs)

		self.tab_widgets = {}		# device_id -> QWidget
		self.tab_curves  = {}		# device_id -> dict[str]->curve
		self.tab_plots   = {}		# device_id -> dict[str]->plot

		self.timer = QtCore.QTimer(self)
		self.timer.setTimerType(QtCore.Qt.PreciseTimer)
		self.timer.timeout.connect(self._update_all)
		self.timer.start(PLOT_UPDATE_MS)

		self._rebuild_tabs()

	def _rebuild_tabs(self):
		self.tabs.clear()
		self.tab_widgets.clear()
		self.tab_curves.clear()
		self.tab_plots.clear()

		# build in stable device order (same as recording order if set)
		with self.viewer.state_lock:
			devs = list(self.viewer.device_order)

		for device_id in devs:
			row = self.viewer.rows.get(device_id)
			label = row.alias() if row else device_id

			w = QtWidgets.QWidget()
			v = QtWidgets.QVBoxLayout(w)
			v.setContentsMargins(10, 10, 10, 10)

			glw = pg.GraphicsLayoutWidget()
			glw.setBackground(None)
			v.addWidget(glw)

			# Create plots:
			# 1) Accel (ax,ay,az)
			p_acc = glw.addPlot(row=0, col=0, title="Accel (ax, ay, az)")
			p_acc.showGrid(x=True, y=True, alpha=0.2)
			p_acc.addLegend(offset=(10, 10))
			c_ax = p_acc.plot(name="ax")
			c_ay = p_acc.plot(name="ay")
			c_az = p_acc.plot(name="az")

			# 2) Gyro (gx,gy,gz)
			glw.nextRow()
			p_gyr = glw.addPlot(row=1, col=0, title="Gyro (gx, gy, gz)")
			p_gyr.showGrid(x=True, y=True, alpha=0.2)
			p_gyr.addLegend(offset=(10, 10))
			c_gx = p_gyr.plot(name="gx")
			c_gy = p_gyr.plot(name="gy")
			c_gz = p_gyr.plot(name="gz")

			# 3) RED
			glw.nextRow()
			p_red = glw.addPlot(row=2, col=0, title="PPG RED")
			p_red.showGrid(x=True, y=True, alpha=0.2)
			c_red = p_red.plot(name="red")

			# 4) IR
			glw.nextRow()
			p_ir = glw.addPlot(row=3, col=0, title="PPG IR")
			p_ir.showGrid(x=True, y=True, alpha=0.2)
			c_ir = p_ir.plot(name="ir")

			self.tabs.addTab(w, label)

			self.tab_widgets[device_id] = w
			self.tab_plots[device_id] = {
				"acc": p_acc,
				"gyr": p_gyr,
				"red": p_red,
				"ir":  p_ir,
			}
			self.tab_curves[device_id] = {
				"ax": c_ax, "ay": c_ay, "az": c_az,
				"gx": c_gx, "gy": c_gy, "gz": c_gz,
				"red": c_red, "ir": c_ir,
			}

	def _update_all(self):
		# Pull fixed-rate buffers from viewer (already synchronized)
		with self.viewer.plot_lock:
			plot_snap = dict(self.viewer.plot_buffers)  # device_id -> dict arrays

		for device_id, buf in plot_snap.items():
			if device_id not in self.tab_curves:
				continue

			t = buf.get("t", None)
			if t is None or len(t) < 2:
				continue

			# show last window
			t0 = t[-1] - PLOT_WINDOW_SEC
			ix0 = np.searchsorted(t, t0, side="left")
			tt = t[ix0:] - t[ix0]

			for k in ("ax","ay","az","gx","gy","gz","red","ir"):
				y = buf.get(k, None)
				if y is None or len(y) != len(t):
					continue
				self.tab_curves[device_id][k].setData(tt, y[ix0:])

			# nice range
			self.tab_plots[device_id]["acc"].setXRange(0, PLOT_WINDOW_SEC, padding=0)
			self.tab_plots[device_id]["gyr"].setXRange(0, PLOT_WINDOW_SEC, padding=0)
			self.tab_plots[device_id]["red"].setXRange(0, PLOT_WINDOW_SEC, padding=0)
			self.tab_plots[device_id]["ir"].setXRange(0, PLOT_WINDOW_SEC, padding=0)


# =========================
# Device row widget
# =========================
class TinZrDeviceRow(QtWidgets.QFrame):
	connect_changed = QtCore.pyqtSignal(str, bool)	# device_id, connect?
	request_remove  = QtCore.pyqtSignal(str)

	def __init__(self, device_id: str, info: DeviceInfo, alias: str = ""):
		super().__init__()
		self.device_id = device_id
		self.info = info

		self.setFrameShape(QtWidgets.QFrame.StyledPanel)
		self.setStyleSheet("QFrame{border:1px solid rgba(255,255,255,40); border-radius:10px;}")

		lay = QtWidgets.QGridLayout(self)
		lay.setContentsMargins(10, 8, 10, 8)
		lay.setHorizontalSpacing(10)
		lay.setVerticalSpacing(4)

		self.ed_alias = QtWidgets.QLineEdit()
		self.ed_alias.setText(alias or "")
		self.ed_alias.setPlaceholderText("Alias")
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

		# Row layout
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

	def set_connected_ui(self, connected: bool):
		# could disable/enable items if you want
		pass

	def set_vitals(self, hr: int, spo2: int, batt: int):
		if hr and hr > 0:
			hr_text = f"HR: {int(hr)} bpm"
		else:
			hr_text = "HR: -- bpm"

		if spo2 and spo2 > 0:
			sp_text = f"SpO₂: {int(spo2)} %"
		else:
			sp_text = "SpO₂: -- %"

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

	def _on_connect_toggled(self, checked: bool):
		self.connect_changed.emit(self.device_id, checked)


# =========================
# Main Multi Viewer
# =========================
class MultiTinZrViewer(QtWidgets.QWidget):
	scan_finished = QtCore.pyqtSignal(object, object)	# (devices, error)
	vitals_update = QtCore.pyqtSignal(str, int, int, int)	# device_id, hr, spo2, batt
	rate_update   = QtCore.pyqtSignal(str, float)	# device_id, rx_hz
	status_update = QtCore.pyqtSignal(str)

	def __init__(self):
		super().__init__()

		self.setWindowTitle("TinZr Wearable (Multi) - Synced Fixed-Rate")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))
		self.setFixedSize(900, 740)
		self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)
		apply_tinzr_theme(self)

		# ===== BLE event loop thread (shared across all clients) =====
		self.loop = asyncio.new_event_loop()
		self.ble_thread = threading.Thread(target=self._run_ble_loop, daemon=True)
		self.ble_thread.start()

		# ===== State =====
		self.discovered: list[DeviceInfo] = []
		self.clients = {}		# device_id -> BleakClient
		self.byte_buf = {}		# device_id -> bytearray
		self.rows = {}			# device_id -> TinZrDeviceRow
		self.connected = set()	# device_id
		self.streaming = False
		self.recording = False

		# stable device order for plotting/recording
		self.state_lock = threading.Lock()
		self.device_order = []	# list[device_id]

		# ===== FIFO queues (timestamped) =====
		# Each entry: (t_sample, vec11)
		self.q_lock = threading.Lock()
		self.q = {}				# device_id -> deque[(t, vec)]
		self.q_max = int(FIFO_MAXLEN)

		# per-device sampling period estimate (for timestamp reconstruction)
		self.fs_lock = threading.Lock()
		self.fs_est = {}			# device_id -> float Hz
		self.dt_est = {}			# device_id -> float seconds

		# last raw sample for interpolation continuity
		self.last_lock = threading.Lock()
		self.last_raw = {}			# device_id -> (t, vec) most recent appended

		# ===== Real RX sampling rate stats =====
		self.rate_lock = threading.Lock()
		self.rx_win_t0 = {}			# device_id -> window start time
		self.rx_win_cnt = {}		# device_id -> frames counted in current window
		self.rx_hz = {}				# device_id -> latest computed Hz
		self.rx_window_sec = float(RX_RATE_WINDOW_SEC)

		# ===== Fixed-rate writer timer =====
		self.sync_timer = QtCore.QTimer(self)
		self.sync_timer.setTimerType(QtCore.Qt.PreciseTimer)
		self.sync_timer.timeout.connect(self._sync_tick_write)

		# ===== Recording (single wide CSV) =====
		self.rec_file = None
		self.rec_t0 = None
		self.rec_tick_idx = 0
		self.rec_devices_order = []
		self.rec_alias_map = {}

		# ===== Fixed-rate buffers for plotting =====
		self.plot_lock = threading.Lock()
		self.plot_buffers = {}	# device_id -> dict arrays

		# plot window
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

		title = QtWidgets.QLabel("TinZr Multi-Device (Synchronized Fixed-Rate)")
		title.setStyleSheet("font-size: 16pt; font-weight: 600; color: #E3F2FD;")
		h_lay.addWidget(title)
		h_lay.addStretch(1)

		main_layout.addWidget(header)

		ctrl = QtWidgets.QWidget()
		grid = QtWidgets.QGridLayout(ctrl)
		grid.setContentsMargins(0, 0, 0, 0)
		grid.setHorizontalSpacing(12)
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

		self.lbl_out = QtWidgets.QLabel(f"Fixed output: {FS_OUT_HZ:.1f} Hz  (timer {SYNC_TIMER_MS} ms)")
		self.lbl_out.setStyleSheet("color: rgba(200,240,255,200);")
		grid.addWidget(self.lbl_out, r, 5, 1, 2, alignment=QtCore.Qt.AlignRight)

		main_layout.addWidget(ctrl)

		# Devices list (scrollable)
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

		# Status
		self.label_status = QtWidgets.QLabel("Ready.")
		self.label_status.setStyleSheet("color: rgba(255,255,255,170);")
		main_layout.addWidget(self.label_status)

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

	# ============ UI handlers ============
	def _set_status(self, text: str):
		self.label_status.setText(text)

	def on_plot_clicked(self):
		if self.plot_window is None:
			self.plot_window = MultiDevicePlotWindow(self)
		else:
			# rebuild tabs in case devices changed
			self.plot_window._rebuild_tabs()
		self.plot_window.show()
		self.plot_window.raise_()
		self.plot_window.activateWindow()

	def on_scan_clicked(self):
		self.combo_found.clear()
		self.discovered = []
		self.spinner.start()
		self.status_update.emit("Scanning for TinZr devices...")

		async def _scan():
			try:
				devs = await BleakScanner.discover(timeout=3.0)
				found = []
				for d in devs:
					name = d.name or ""
					if name.startswith(DEVICE_PREFIX):
						found.append(DeviceInfo(name=name, address=d.address))
				return found, None
			except Exception as e:
				return [], e

		def _done(fut):
			devs, err = fut.result()
			self.scan_finished.emit(devs, err)

		fut = asyncio.run_coroutine_threadsafe(_scan(), self.loop)
		fut.add_done_callback(_done)

	def _handle_scan_result(self, devs, err):
		self.spinner.stop()
		if err is not None:
			self.status_update.emit(f"Scan error: {err}")
			return

		self.discovered = devs
		self.combo_found.clear()
		for info in devs:
			self.combo_found.addItem(f"{info.name}   ({info.address})", info)

		if len(devs) == 0:
			self.status_update.emit("No TinZr devices found.")
		else:
			self.status_update.emit(f"Found {len(devs)} TinZr device(s).")

	def on_add_clicked(self):
		info = self.combo_found.currentData()
		if info is None:
			return

		device_id = info.address
		if device_id in self.rows:
			self.status_update.emit("Device already added.")
			return

		alias = self.ed_new_alias.text().strip()

		row = TinZrDeviceRow(device_id, info, alias=alias)
		row.connect_changed.connect(self._on_connect_changed)
		row.request_remove.connect(self._on_remove_device)

		# insert before stretch
		self.devices_layout.insertWidget(self.devices_layout.count() - 1, row)

		self.rows[device_id] = row
		self.byte_buf[device_id] = bytearray()

		with self.state_lock:
			self.device_order.append(device_id)

		# init fifo + stats
		with self.q_lock:
			self.q[device_id] = deque(maxlen=self.q_max)

		with self.fs_lock:
			self.fs_est[device_id] = float(FW_FS_HINT_HZ)
			self.dt_est[device_id] = 1.0 / float(FW_FS_HINT_HZ)

		with self.rate_lock:
			self.rx_win_t0[device_id] = time.perf_counter()
			self.rx_win_cnt[device_id] = 0
			self.rx_hz[device_id] = 0.0

		# enable global toggles if any devices exist
		self.toggle_stream.setEnabled(True)
		self.toggle_record.setEnabled(True)

		self.status_update.emit(f"Added {info.name}.")

	def _on_remove_device(self, device_id: str):
		if device_id in self.connected:
			try:
				self._disconnect_device(device_id)
			except Exception:
				pass

		row = self.rows.get(device_id)
		if row:
			row.setParent(None)
			row.deleteLater()

		self.rows.pop(device_id, None)
		self.byte_buf.pop(device_id, None)
		self.clients.pop(device_id, None)

		with self.q_lock:
			self.q.pop(device_id, None)

		with self.fs_lock:
			self.fs_est.pop(device_id, None)
			self.dt_est.pop(device_id, None)

		with self.rate_lock:
			self.rx_win_t0.pop(device_id, None)
			self.rx_win_cnt.pop(device_id, None)
			self.rx_hz.pop(device_id, None)

		with self.state_lock:
			if device_id in self.device_order:
				self.device_order.remove(device_id)

		self.status_update.emit("Removed device.")

	def _on_connect_changed(self, device_id: str, want_connect: bool):
		if want_connect:
			self._connect_device(device_id)
		else:
			self._disconnect_device(device_id)

	def _connect_device(self, device_id: str):
		if device_id in self.connected:
			return

		row = self.rows.get(device_id)
		if row is None:
			return

		info = row.info

		async def _do_connect():
			client = BleakClient(info.address)
			await client.connect()
			await client.start_notify(TINZR_BLE_TX_CHAR_UUID, lambda sender, data: self._on_notify(device_id, data))
			return client

		def _done(fut):
			try:
				client = fut.result()
				self.clients[device_id] = client
				self.connected.add(device_id)
				self.status_update.emit(f"Connected: {info.name}")
				row.set_connected_ui(True)
			except Exception as e:
				self.status_update.emit(f"Connect error ({info.name}): {e}")
				row.toggle_connect.blockSignals(True)
				row.toggle_connect.setChecked(False)
				row.toggle_connect.blockSignals(False)

		fut = asyncio.run_coroutine_threadsafe(_do_connect(), self.loop)
		fut.add_done_callback(lambda f: QtCore.QTimer.singleShot(0, lambda: _done(f)))

	def _disconnect_device(self, device_id: str):
		row = self.rows.get(device_id)
		info = row.info if row else None
		client = self.clients.get(device_id)
		if client is None:
			if row:
				row.set_connected_ui(False)
			self.connected.discard(device_id)
			return

		async def _do_disc():
			try:
				await client.stop_notify(TINZR_BLE_TX_CHAR_UUID)
			except Exception:
				pass
			try:
				await client.disconnect()
			except Exception:
				pass

		def _done(_fut):
			self.clients.pop(device_id, None)
			self.connected.discard(device_id)
			if row:
				row.set_connected_ui(False)
			if info:
				self.status_update.emit(f"Disconnected: {info.name}")

		fut = asyncio.run_coroutine_threadsafe(_do_disc(), self.loop)
		fut.add_done_callback(lambda f: QtCore.QTimer.singleShot(0, lambda: _done(f)))

	def on_stream_toggled(self, checked: bool):
		if checked:
			self._start_stream_all()
		else:
			self._stop_stream_all()

	def _start_stream_all(self):
		if self.streaming:
			return
		self.streaming = True
		self.status_update.emit("Streaming started.")

		# start fixed-rate tick always when streaming (even if not recording)
		if not self.sync_timer.isActive():
			self.sync_timer.start(SYNC_TIMER_MS)

		async def _send_all(cmd):
			for did in list(self.connected):
				c = self.clients.get(did)
				if c and c.is_connected:
					try:
						await c.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, cmd)
					except Exception:
						pass

		asyncio.run_coroutine_threadsafe(_send_all(CMD_START), self.loop)

	def _stop_stream_all(self):
		if not self.streaming:
			return
		self.streaming = False
		self.status_update.emit("Streaming stopped.")

		async def _send_all(cmd):
			for did in list(self.connected):
				c = self.clients.get(did)
				if c and c.is_connected:
					try:
						await c.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, cmd)
					except Exception:
						pass

		asyncio.run_coroutine_threadsafe(_send_all(CMD_STOP), self.loop)

		# if not recording, can stop timer
		if not self.recording:
			if self.sync_timer.isActive():
				self.sync_timer.stop()

	def on_record_toggled(self, checked: bool):
		if checked:
			self._start_recording()
		else:
			self._stop_recording()

	def _start_recording(self):
		if self.recording:
			return

		# Build output file (single wide CSV)
		ts = datetime.now().strftime("%Y%m%d_%H%M%S")
		out_name = f"TinZr_SyncWide_{ts}.csv"
		out_path = os.path.join(CURRENT_DIR, out_name)

		try:
			f = open(out_path, "w", encoding="utf-8")
		except Exception as e:
			self.status_update.emit(f"Cannot open file: {e}")
			self.toggle_record.blockSignals(True)
			self.toggle_record.setChecked(False)
			self.toggle_record.blockSignals(False)
			return

		# freeze device order at start of recording (only connected ones)
		with self.state_lock:
			devs = [d for d in self.device_order if d in self.rows]

		self.rec_devices_order = devs
		self.rec_alias_map = {}
		for did in devs:
			row = self.rows.get(did)
			self.rec_alias_map[did] = _safe_name(row.alias() if row else did)

		# header: time + blocks per device
		cols = ["t_s"]
		for did in self.rec_devices_order:
			nm = self.rec_alias_map[did]
			cols += [
				f"{nm}_ax", f"{nm}_ay", f"{nm}_az",
				f"{nm}_gx", f"{nm}_gy", f"{nm}_gz",
				f"{nm}_red", f"{nm}_ir",
				f"{nm}_hr", f"{nm}_spo2", f"{nm}_batt",
			]
		f.write(",".join(cols) + "\n")
		f.flush()

		self.rec_file = f
		self.rec_t0 = time.perf_counter()
		self.rec_tick_idx = 0
		self.recording = True

		# ensure tick timer running
		if not self.sync_timer.isActive():
			self.sync_timer.start(SYNC_TIMER_MS)

		self.status_update.emit(f"Recording started: {out_name}")

	def _stop_recording(self):
		if not self.recording:
			return

		self.recording = False
		try:
			if self.rec_file:
				self.rec_file.flush()
				self.rec_file.close()
		except Exception:
			pass

		self.rec_file = None
		self.status_update.emit("Recording stopped.")

		# If not streaming, stop timer
		if not self.streaming:
			if self.sync_timer.isActive():
				self.sync_timer.stop()

	# ==========================================
	# Core: BLE notify -> timestamped FIFO append
	# ==========================================
	def _on_notify(self, device_id: str, data: bytes):
		# runs in BLE thread context
		if device_id not in self.byte_buf:
			self.byte_buf[device_id] = bytearray()

		buf = self.byte_buf[device_id]
		buf.extend(data)

		n_bytes = len(buf)
		if n_bytes < FRAME_SIZE:
			return

		n_frames = n_bytes // FRAME_SIZE
		if n_frames <= 0:
			return

		# ---- RX-rate measurement (frames/sec), windowed ----
		now = time.perf_counter()
		with self.rate_lock:
			if device_id not in self.rx_win_t0:
				self.rx_win_t0[device_id] = now
				self.rx_win_cnt[device_id] = 0
				self.rx_hz[device_id] = 0.0

			self.rx_win_cnt[device_id] += n_frames
			dt = now - self.rx_win_t0[device_id]
			if dt >= self.rx_window_sec:
				hz = float(self.rx_win_cnt[device_id]) / max(dt, 1e-6)
				self.rx_hz[device_id] = hz
				self.rx_win_t0[device_id] = now
				self.rx_win_cnt[device_id] = 0

				# update period estimate used for timestamp reconstruction
				with self.fs_lock:
					# smooth a bit
					old = self.fs_est.get(device_id, float(FW_FS_HINT_HZ))
					new = 0.85 * old + 0.15 * hz
					new = float(np.clip(new, 20.0, 1000.0))
					self.fs_est[device_id] = new
					self.dt_est[device_id] = 1.0 / new

				self.rate_update.emit(device_id, hz)

		# ---- Parse frames and append to FIFO with reconstructed timestamps ----
		# We assume these frames were sampled at ~constant dt (device dt_est),
		# and that "now" corresponds approximately to the last frame time.
		with self.fs_lock:
			dt_s = self.dt_est.get(device_id, 1.0 / float(FW_FS_HINT_HZ))

		# timestamps for frames in this notification:
		# last frame at ~now, earlier frames spaced by dt_s
		# frame i (0-based) => t_i = now - (n_frames-1-i)*dt_s
		base_t = now - float(n_frames - 1) * dt_s

		last_hr, last_spo2, last_batt = 0, 0, 0

		with self.q_lock:
			dq = self.q.get(device_id)
			if dq is None:
				dq = deque(maxlen=self.q_max)
				self.q[device_id] = dq

			for i in range(n_frames):
				frame_bytes = buf[i * FRAME_SIZE:(i + 1) * FRAME_SIZE]
				ax_i, ay_i, az_i, gx_i, gy_i, gz_i, red_i, ir_i, hr_i, spo2_i, batt_i = FRAME_STRUCT.unpack(frame_bytes)

				ax = ax_i * ACC_SCALE
				ay = ay_i * ACC_SCALE
				az = az_i * ACC_SCALE
				gx = gx_i * GYR_SCALE
				gy = gy_i * GYR_SCALE
				gz = gz_i * GYR_SCALE
				red = float(red_i)
				ir  = float(ir_i)

				last_hr, last_spo2, last_batt = int(hr_i), int(spo2_i), int(batt_i)

				vec = [
					float(ax), float(ay), float(az),
					float(gx), float(gy), float(gz),
					float(red), float(ir),
					float(last_hr), float(last_spo2), float(last_batt),
				]

				t_i = base_t + float(i) * dt_s
				dq.append((t_i, vec))

				with self.last_lock:
					self.last_raw[device_id] = (t_i, vec)

		self.vitals_update.emit(device_id, last_hr, last_spo2, last_batt)

		# keep remaining bytes
		remaining = n_bytes - n_frames * FRAME_SIZE
		if remaining > 0:
			self.byte_buf[device_id] = buf[-remaining:]
		else:
			self.byte_buf[device_id].clear()

	def _on_vitals_update(self, device_id: str, hr: int, spo2: int, batt: int):
		row = self.rows.get(device_id)
		if row is None:
			return
		row.set_vitals(hr, spo2, batt)

	def _on_rate_update(self, device_id: str, hz: float):
		row = self.rows.get(device_id)
		if row is None:
			return
		row.set_rate(hz)

	# ==========================================
	# Fixed-rate tick: interpolate at tick time
	# ==========================================
	def _interp_at(self, dq: deque, t_q: float, last_fallback):
		"""
		dq: deque of (t, vec)
		return: vec at time t_q via linear interpolation
		"""
		if dq is None or len(dq) == 0:
			return last_fallback

		# Ensure dq has increasing times (it should)
		# Pop left while the second element is still <= t_q
		while len(dq) >= 2 and dq[1][0] <= t_q:
			dq.popleft()

		# Now either:
		# - dq[0].t <= t_q < dq[1].t  => interpolate
		# - t_q < dq[0].t             => not enough history -> hold first
		# - len(dq)==1                => hold last
		if len(dq) == 1:
			return dq[0][1]

		t0, v0 = dq[0]
		t1, v1 = dq[1]

		if t1 <= t0:
			return v1

		if t_q <= t0:
			return v0

		if t_q >= t1:
			# should not happen due to pop loop, but safe
			return v1

		a = (t_q - t0) / (t1 - t0)
		v0a = np.asarray(v0, dtype=float)
		v1a = np.asarray(v1, dtype=float)
		v = (1.0 - a) * v0a + a * v1a
		return v.tolist()

	def _sync_tick_write(self):
		# This runs in Qt main thread at fixed rate.
		if (not self.streaming) and (not self.recording):
			return

		# define tick time based on rec_t0 or "start" when not recording
		if self.rec_t0 is None:
			# use a local reference for plotting
			self.rec_t0 = time.perf_counter()
			self.rec_tick_idx = 0

		t_q = self.rec_t0 + (self.rec_tick_idx / FS_OUT_HZ)
		t_s = t_q - self.rec_t0

		# For each device in stable order, compute interpolated sample at t_q
		with self.q_lock:
			# snapshot device list
			with self.state_lock:
				devs = list(self.device_order)

			row_vals = {}
			for did in devs:
				dq = self.q.get(did, None)
				with self.last_lock:
					last_fb = self.last_raw.get(did, None)
				last_vec = last_fb[1] if last_fb else [0.0] * 11
				v = self._interp_at(dq, t_q, last_vec)
				row_vals[did] = v

		# ===== Save wide CSV if recording =====
		if self.recording and self.rec_file is not None:
			out = [f"{t_s:.6f}"]
			for did in self.rec_devices_order:
				v = row_vals.get(did, [0.0] * 11)
				# ax..ir floats, hr/spo2/batt may be float but represent int
				out += [
					f"{v[0]:.6f}", f"{v[1]:.6f}", f"{v[2]:.6f}",
					f"{v[3]:.6f}", f"{v[4]:.6f}", f"{v[5]:.6f}",
					f"{v[6]:.6f}", f"{v[7]:.6f}",
					f"{v[8]:.2f}", f"{v[9]:.2f}", f"{v[10]:.2f}",
				]
			try:
				self.rec_file.write(",".join(out) + "\n")
			except Exception:
				pass

		# ===== Push into plot buffers (fixed-rate) =====
		with self.plot_lock:
			for did, v in row_vals.items():
				buf = self.plot_buffers.get(did, None)
				if buf is None:
					buf = {
						"t": np.array([], dtype=float),
						"ax": np.array([], dtype=float), "ay": np.array([], dtype=float), "az": np.array([], dtype=float),
						"gx": np.array([], dtype=float), "gy": np.array([], dtype=float), "gz": np.array([], dtype=float),
						"red": np.array([], dtype=float), "ir": np.array([], dtype=float),
					}
					self.plot_buffers[did] = buf

				# append with bounded window
				def _append(arr, x):
					arr = np.append(arr, x)
					max_n = int(PLOT_WINDOW_SEC * FS_OUT_HZ * 2.0)  # keep some margin
					if len(arr) > max_n:
						arr = arr[-max_n:]
					return arr

				buf["t"]   = _append(buf["t"], t_s)
				buf["ax"]  = _append(buf["ax"], v[0])
				buf["ay"]  = _append(buf["ay"], v[1])
				buf["az"]  = _append(buf["az"], v[2])
				buf["gx"]  = _append(buf["gx"], v[3])
				buf["gy"]  = _append(buf["gy"], v[4])
				buf["gz"]  = _append(buf["gz"], v[5])
				buf["red"] = _append(buf["red"], v[6])
				buf["ir"]  = _append(buf["ir"], v[7])

		self.rec_tick_idx += 1

	def closeEvent(self, event):
		# Stop recording/streaming gracefully
		if self.recording:
			self._stop_recording()
		if self.streaming:
			self._stop_stream_all()

		# Disconnect all clients
		for did in list(self.connected):
			try:
				self._disconnect_device(did)
			except Exception:
				pass

		# Stop loop
		if self.loop.is_running():
			self.loop.call_soon_threadsafe(self.loop.stop)

		if self.ble_thread.is_alive():
			self.ble_thread.join(timeout=2)

		super().closeEvent(event)


if __name__ == "__main__":
	# DPI scaling like your original
	if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
	if hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

	app = QtWidgets.QApplication(sys.argv)
	w = MultiTinZrViewer()
	w.show()
	sys.exit(app.exec_())
