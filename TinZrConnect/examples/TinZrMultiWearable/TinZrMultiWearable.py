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

# ================== FIXED OUTPUT CONFIG ==================
FS_OUT_HZ            = 150.0
SYNC_TIMER_MS        = int(round(1000.0 / FS_OUT_HZ))

# Plot resample (your plot window interpolates to this)
FS_RESAMP_HZ         = 240.0

FIFO_MAX_SECONDS     = 6.0
FIFO_MAXLEN          = int(FIFO_MAX_SECONDS * 300.0)  # upper bound

RX_RATE_WINDOW_SEC   = 1.0
FW_FS_HINT_HZ        = 250.0

PLOT_WINDOW_SEC      = 5.0
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

		self.setWindowTitle("TinZr Live Plots")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))
		self.resize(1100, 750)
		apply_tinzr_theme(self)

		screen = QtWidgets.QApplication.primaryScreen()
		dpi = float(screen.logicalDotsPerInch()) if screen else 96.0
		self._dpi_scale = dpi / 96.0
		self._axis_label_pt = int(round(12 * self._dpi_scale))

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
		self.timer.start(PLOT_UPDATE_MS)

		self._rebuild_tabs()

	def _rebuild_tabs(self):
		self.tabs.clear()
		self.tab_widgets.clear()
		self.tab_curves.clear()
		self.tab_axes.clear()

		with self.viewer.state_lock:
			devs = list(self.viewer.device_order)

		for device_id in devs:
			row = self.viewer.rows.get(device_id)
			label = row.alias() if row else device_id

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
			self.tab_widgets[device_id] = w
			self.tab_axes[device_id] = axes
			self.tab_curves[device_id] = curves

	def _update_all(self):
		with self.viewer.plot_lock:
			plot_snap = dict(self.viewer.plot_buffers)

		n_ds = int(PLOT_WINDOW_SEC * FS_RESAMP_HZ)
		if n_ds < 2:
			return
		t_ds = np.linspace(0.0, PLOT_WINDOW_SEC, n_ds, endpoint=False)

		for device_id, buf in plot_snap.items():
			if device_id not in self.tab_curves:
				continue

			t = np.asarray(buf.get("t", []), dtype=float)
			if len(t) < 4:
				continue

			def _aligned(ykey):
				y = np.asarray(buf.get(ykey, []), dtype=float)
				L = min(len(t), len(y))
				if L < 4:
					return None, None
				return t[-L:], y[-L:]

			for key in self.signal_order:
				t_k, y = _aligned(key)
				if t_k is None:
					continue

				t_last_k = t_k[-1]
				t0_k = t_last_k - PLOT_WINDOW_SEC
				ix0_k = int(np.searchsorted(t_k, t0_k, side="left"))
				tw = t_k[ix0_k:] - t_k[ix0_k]
				yw = y[ix0_k:]

				if len(tw) < 2 or len(yw) < 2:
					continue
				if tw[-1] <= tw[0]:
					continue

				y_ds = np.interp(t_ds, tw, yw)
				self.tab_curves[device_id][key].setData(t_ds, y_ds)
				self.tab_axes[device_id][key].setXRange(0.0, PLOT_WINDOW_SEC, padding=0.0)


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
		self.clients = {}			# did -> BleakClient
		self.byte_buf = {}			# did -> bytearray
		self.rows = {}				# did -> TinZrDeviceRow
		self.connected = set()		# did
		self.streaming = False
		self.recording = False

		self.state_lock = threading.Lock()
		self.device_order = []		# list of did

		# ===== Raw sample FIFOs =====
		# each entry: (t, vec_dict)
		self.q_lock = threading.Lock()
		self.q = {}					# did -> deque
		self.q_max = int(FIFO_MAXLEN)

		# ===== Timestamp reconstruction =====
		self.fs_lock = threading.Lock()
		self.fs_est = {}			# did -> Hz
		self.dt_est = {}			# did -> seconds/sample
		self.last_lock = threading.Lock()
		self.last_raw = {}			# did -> (t, vec_dict)

		# ===== Real RX sampling rate stats =====
		self.rate_lock = threading.Lock()
		self.rx_win_t0 = {}
		self.rx_win_cnt = {}
		self.rx_hz = {}
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

		# ===== Plot buffers for plotting (per device) =====
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

		self.lbl_out = QtWidgets.QLabel(
			f"Fixed output: {FS_OUT_HZ:.1f} Hz (timer {SYNC_TIMER_MS} ms) | Plot resample: {FS_RESAMP_HZ:.1f} Hz"
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

	# ============ Status ============
	def _set_status(self, text: str):
		self.label_status.setText(text)

	# ============ Plot ============
	def on_plot_clicked(self):
		if self.plot_window is None:
			self.plot_window = MultiDevicePlotWindow(self)
		else:
			self.plot_window._rebuild_tabs()
		self.plot_window.show()
		self.plot_window.raise_()
		self.plot_window.activateWindow()

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
			alias = info.name  # default alias = BLE device name (e.g., "TinZrRev4_01")
		row = TinZrDeviceRow(did, info, alias=alias)

		
		
		row.connect_changed.connect(self._on_row_connect_changed)
		row.request_remove.connect(self._on_row_remove)
		self.rows[did] = row

		with self.state_lock:
			self.device_order.append(did)

		# insert before stretch
		self.devices_layout.insertWidget(self.devices_layout.count() - 1, row)

		# init per-device buffers
		with self.q_lock:
			self.q[did] = deque(maxlen=self.q_max)
		with self.fs_lock:
			self.fs_est[did] = float(FW_FS_HINT_HZ)
			self.dt_est[did] = 1.0 / float(FW_FS_HINT_HZ)
		with self.last_lock:
			self.last_raw[did] = None
		with self.rate_lock:
			self.rx_win_t0[did] = time.perf_counter()
			self.rx_win_cnt[did] = 0
			self.rx_hz[did] = 0.0
		with self.plot_lock:
			self.plot_buffers[did] = {k: [] for k in ("t","ax","ay","az","gx","gy","gz","red","ir")}

		self.toggle_stream.setEnabled(True)
		self.toggle_record.setEnabled(True)

		if self.plot_window is not None:
			self.plot_window._rebuild_tabs()

		self.status_update.emit(f"Added: {row.alias()}")

	def _on_row_remove(self, did: str):
		if did in self.connected:
			self._disconnect_device(did)

		row = self.rows.pop(did, None)
		if row:
			row.setParent(None)
			row.deleteLater()

		with self.state_lock:
			if did in self.device_order:
				self.device_order.remove(did)

		with self.q_lock:
			self.q.pop(did, None)
		with self.fs_lock:
			self.fs_est.pop(did, None)
			self.dt_est.pop(did, None)
		with self.last_lock:
			self.last_raw.pop(did, None)
		with self.plot_lock:
			self.plot_buffers.pop(did, None)

		if self.plot_window is not None:
			self.plot_window._rebuild_tabs()

		if len(self.rows) == 0:
			self.toggle_stream.setChecked(False)
			self.toggle_stream.setEnabled(False)
			self.toggle_record.setChecked(False)
			self.toggle_record.setEnabled(False)

		self.status_update.emit("Removed device.")

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
			self.byte_buf[did] = bytearray()
			self.connected.add(did)

			self.rows[did].set_toggle(True)
			self.status_update.emit(f"Connected: {self.rows[did].alias()}")

		except Exception as e:
			try:
				await client.disconnect()
			except Exception:
				pass
			self.rows[did].set_toggle(False)
			self.status_update.emit(f"Connect failed: {e}")

	def _disconnect_device(self, did: str):
		asyncio.run_coroutine_threadsafe(self._disconnect_device_async(did), self.loop)

	async def _disconnect_device_async(self, did: str):
		if did not in self.clients:
			if did in self.rows:
				self.rows[did].set_toggle(False)
			self.connected.discard(did)
			return

		client = self.clients.get(did)
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
		self.byte_buf.pop(did, None)
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
		for did in list(self.connected):
			asyncio.run_coroutine_threadsafe(self._send_cmd(did, CMD_START), self.loop)

		# start fixed-rate writer (sync)
		self.rec_tick_idx = 0
		if not self.sync_timer.isActive():
			self.sync_timer.start(SYNC_TIMER_MS)

		self.status_update.emit("Streaming: ON")

	def _stop_stream_all(self):
		if not self.streaming:
			return
		self.streaming = False
		self.status_update.emit("Streaming: stopping...")
		for did in list(self.connected):
			asyncio.run_coroutine_threadsafe(self._send_cmd(did, CMD_STOP), self.loop)

		# stop timer (but if recording is ON, we keep it running)
		if not self.recording and self.sync_timer.isActive():
			self.sync_timer.stop()

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
		default_name = f"TinZrMulti_{ts}.csv"

		start_dir = os.path.dirname(os.path.abspath(__file__))
		fpath, _ = QtWidgets.QFileDialog.getSaveFileName(
			self,
			"Save recording CSV",
			os.path.join(start_dir, default_name),
			"CSV Files (*.csv);;All Files (*)"
		)

		# user cancelled
		if not fpath:
			self.toggle_record.setChecked(False)
			self.status_update.emit("Recording cancelled.")
			return

		# enforce .csv
		if not fpath.lower().endswith(".csv"):
			fpath += ".csv"


		try:
			self.rec_file = open(fpath, "w", newline="")
		except Exception as e:
			self.toggle_record.setChecked(False)
			self.status_update.emit(f"Record open failed: {e}")
			return

		with self.state_lock:
			self.rec_devices_order = list(self.device_order)
		self.rec_alias_map = {did: self.rows[did].alias() for did in self.rec_devices_order if did in self.rows}

		# header
		cols = ["t_sec"]
		for did in self.rec_devices_order:
			a = _safe_name(self.rec_alias_map.get(did, did))
			cols += [
				f"{a}_ax", f"{a}_ay", f"{a}_az",
				f"{a}_gx", f"{a}_gy", f"{a}_gz",
				f"{a}_red", f"{a}_ir",
				f"{a}_hr", f"{a}_spo2", f"{a}_batt"
			]
		self.rec_file.write(",".join(cols) + "\n")
		self.rec_file.flush()

		self.recording = True
		self.rec_t0 = time.perf_counter()
		self.rec_tick_idx = 0

		# recording requires fixed-rate writer
		if not self.sync_timer.isActive():
			self.sync_timer.start(SYNC_TIMER_MS)

		self.status_update.emit(f"Recording: ON ({fpath})")

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
		self.rec_t0 = None

		# if not streaming, we can stop the fixed-rate writer
		if not self.streaming and self.sync_timer.isActive():
			self.sync_timer.stop()

		self.status_update.emit("Recording: OFF")

	# ============ Notify handler ============
	def _on_notify(self, did: str, data: bytes):
		# Called from Bleak notification context (in event loop thread)
		if did not in self.byte_buf:
			self.byte_buf[did] = bytearray()

		b = self.byte_buf[did]
		b.extend(data)

		# Parse as many frames as possible
		n = len(b) // FRAME_SIZE
		if n <= 0:
			return

		# Keep remainder
		chunk = b[: n * FRAME_SIZE]
		rem = b[n * FRAME_SIZE :]
		self.byte_buf[did] = bytearray(rem)

		now = time.perf_counter()

		# update rx rate stats using frame count
		self._rx_rate_update(did, n, now)

		# reconstruct timestamps (best-effort without firmware sample_idx)
		with self.fs_lock:
			dt = float(self.dt_est.get(did, 1.0 / FW_FS_HINT_HZ))

		with self.last_lock:
			last = self.last_raw.get(did)

		# anchor: if first time, set last_t near "now - (n-1)*dt" so the burst spans dt steps
		if last is None:
			t0 = now - (n - 1) * dt
		else:
			t0 = last[0] + dt

		# decode frames
		for i in range(n):
			off = i * FRAME_SIZE
			ax, ay, az, gx, gy, gz, red, ir, hr, spo2, batt = FRAME_STRUCT.unpack_from(chunk, off)

			vec = {
				"ax": float(ax) * ACC_SCALE,
				"ay": float(ay) * ACC_SCALE,
				"az": float(az) * ACC_SCALE,
				"gx": float(gx) * GYR_SCALE,
				"gy": float(gy) * GYR_SCALE,
				"gz": float(gz) * GYR_SCALE,
				"red": float(red),
				"ir":  float(ir),
				"hr": int(hr),
				"spo2": int(spo2),
				"batt": int(batt),
			}

			t_sample = t0 + i * dt

			with self.q_lock:
				q = self.q.get(did)
				if q is not None:
					q.append((t_sample, vec))

			with self.last_lock:
				self.last_raw[did] = (t_sample, vec)

			# update vitals on UI (latest frame)
			if i == n - 1:
				self.vitals_update.emit(did, int(hr), int(spo2), int(batt))

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

				# adapt dt_est slowly (low-pass)
				target_fs = max(10.0, min(1000.0, hz))
				target_dt = 1.0 / target_fs
				with self.fs_lock:
					prev_dt = float(self.dt_est.get(did, 1.0 / FW_FS_HINT_HZ))
					new_dt = 0.90 * prev_dt + 0.10 * target_dt
					self.dt_est[did] = new_dt
					self.fs_est[did] = 1.0 / max(new_dt, 1e-9)
			else:
				self.rx_win_cnt[did] = cnt

	# ============ UI update slots ============
	def _on_vitals_update(self, did: str, hr: int, spo2: int, batt: int):
		row = self.rows.get(did)
		if row:
			row.set_vitals(hr, spo2, batt)

	def _on_rate_update(self, did: str, hz: float):
		row = self.rows.get(did)
		if row:
			row.set_rate(hz)

	# ============ Fixed-rate writer (SYNC) ============
	def _sync_tick_write(self):
		# This is the heart: produce synchronized rows at FS_OUT_HZ
		if (not self.streaming) and (not self.recording):
			return

		if self.rec_t0 is None:
			self.rec_t0 = time.perf_counter()

		t_out = self.rec_t0 + (self.rec_tick_idx / FS_OUT_HZ)

		with self.state_lock:
			devs = list(self.device_order)

		row_vals = [f"{(t_out - self.rec_t0):.6f}"]

		for did in devs:
			# pull enough raw data to interpolate at t_out
			x = self._interp_device(did, t_out)

			if x is None:
				# 11 columns blank-ish
				row_vals += [""] * 11
				continue

			# x has floats and ints
			row_vals += [
				f"{x['ax']:.6f}", f"{x['ay']:.6f}", f"{x['az']:.6f}",
				f"{x['gx']:.6f}", f"{x['gy']:.6f}", f"{x['gz']:.6f}",
				f"{x['red']:.1f}", f"{x['ir']:.1f}",
				str(int(x['hr'])), str(int(x['spo2'])), str(int(x['batt']))
			]

			# push to plot buffers (continuous)  ✅ USE x, NOT vec
			with self.plot_lock:
				pb = self.plot_buffers.get(did)
				if pb is not None:
					pb["t"].append(float(t_out))
					for k in ("ax","ay","az","gx","gy","gz","red","ir"):
						pb[k].append(float(x[k]))

					max_keep = int(FIFO_MAX_SECONDS * FS_OUT_HZ) + 200
					for k in pb.keys():
						if len(pb[k]) > max_keep:
							pb[k] = pb[k][-max_keep:]

		# write CSV
		if self.recording and self.rec_file:
			try:
				self.rec_file.write(",".join(row_vals) + "\n")
				# avoid flushing every line (slows down); flush periodically
				if (self.rec_tick_idx % 50) == 0:
					self.rec_file.flush()
			except Exception:
				pass

		self.rec_tick_idx += 1

	def _interp_device(self, did: str, t_out: float):
		# Returns dict with values at time t_out using linear interpolation
		# If we don't have a future sample yet, we hold last sample (ZOH)
		with self.q_lock:
			q = self.q.get(did)
			if not q or len(q) < 1:
				return None

			# Drop too-old points
			t_min = t_out - FIFO_MAX_SECONDS
			while len(q) > 2 and q[1][0] < t_min:
				q.popleft()

			# If still no data
			if len(q) < 1:
				return None

			# If we only have one sample, hold it
			if len(q) == 1:
				return q[0][1]

			# If t_out is earlier than first sample, return first
			if t_out <= q[0][0]:
				return q[0][1]

			# Advance until q[0].t <= t_out <= q[1].t, if possible
			while len(q) >= 2 and q[1][0] < t_out:
				q.popleft()

			# If we ran out of a future point, hold last
			if len(q) < 2:
				return q[-1][1]

			t0, v0 = q[0]
			t1, v1 = q[1]

		# Guard degenerate
		if t1 <= t0:
			return v1

		# Interpolate
		if t_out <= t0:
			alpha = 0.0
		elif t_out >= t1:
			alpha = 1.0
		else:
			alpha = (t_out - t0) / (t1 - t0)

		def lerp(a, b):
			return (1.0 - alpha) * float(a) + alpha * float(b)

		out = {
			"ax": lerp(v0["ax"], v1["ax"]),
			"ay": lerp(v0["ay"], v1["ay"]),
			"az": lerp(v0["az"], v1["az"]),
			"gx": lerp(v0["gx"], v1["gx"]),
			"gy": lerp(v0["gy"], v1["gy"]),
			"gz": lerp(v0["gz"], v1["gz"]),
			"red": lerp(v0["red"], v1["red"]),
			"ir":  lerp(v0["ir"],  v1["ir"]),
			# vitals: hold-last (no interpolation)
			"hr": v1["hr"],
			"spo2": v1["spo2"],
			"batt": v1["batt"],
		}
		return out

	# ============ Cleanup on close ============
	def closeEvent(self, event):
		# stop record/stream first so firmware stops sending
		if self.recording:
			self._stop_recording()
		if self.streaming:
			self._stop_stream_all()

		# disconnect everything
		for did in list(self.connected):
			try:
				self._disconnect_device(did)
			except Exception:
				pass

		t0 = time.perf_counter()
		while len(self.connected) > 0 and (time.perf_counter() - t0) < 1.2:
			QtWidgets.QApplication.processEvents()
			time.sleep(0.01)

		if self.loop.is_running():
			self.loop.call_soon_threadsafe(self.loop.stop)

		if self.ble_thread.is_alive():
			self.ble_thread.join(timeout=2)

		super().closeEvent(event)


if __name__ == "__main__":
	if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
	if hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

	app = QtWidgets.QApplication(sys.argv)
	w = MultiTinZrViewer()
	w.show()
	sys.exit(app.exec_())
