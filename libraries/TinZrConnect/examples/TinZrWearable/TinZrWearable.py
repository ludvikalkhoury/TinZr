import os
import sys
import time
import struct
import csv
import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime

from PyQt5 import QtCore, QtWidgets, QtGui

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

pg = None
np = None
BleakScanner = None
BleakClient = None


def _resource_path(name: str) -> str:
	base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
	return os.path.join(base_dir, name)


ICON_PATH = _resource_path("TinZr_small_logo.ico")


def _get_pyqtgraph():
	global pg
	if pg is None:
		import pyqtgraph as _pg
		_pg.setConfigOptions(antialias=True)
		pg = _pg
	return pg


def _get_numpy():
	global np
	if np is None:
		import numpy as _np
		np = _np
	return np


def _get_bleak():
	global BleakScanner, BleakClient
	if BleakScanner is None or BleakClient is None:
		from bleak import BleakScanner as _BleakScanner, BleakClient as _BleakClient
		BleakScanner = _BleakScanner
		BleakClient = _BleakClient
	return BleakScanner, BleakClient

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
CMD_BATT  = b"BAT"

# ================== Frame format (must match C++) ==============
FRAME_SYNC_WORD = 0xA55A
FRAME_SYNC_BYTES = FRAME_SYNC_WORD.to_bytes(2, "little")
FRAME_STRUCT = struct.Struct("<HIhhhhhhffBBB")
FRAME_SIZE   = FRAME_STRUCT.size
BLE_PACKET_MAGIC = b"\xA5\x5A"
BLE_PACKET_TYPE_WEARABLE = 0x01
BLE_PACKET_VERSION = 0x01
BLE_HEADER_BYTES = 13
BLE_CRC_BYTES = 2

ACC_SCALE = 1e-3
GYR_SCALE = 1.0 / 100.0

# ================== Viewer Config (mirrors Wearable defaults) ==================
FS_RESAMP_HZ    = 250.0    # fixed output Fs for saving (and plot resample)
WINDOW_SEC      = 3.0      # seconds visible on screen
UPDATE_MS       = 10       # GUI update period
MAX_RAW_SAMPLES = 20000    # plot buffer cap (per device)

# Rate estimate window (per device)
RX_RATE_WINDOW_SEC = 1.0
FW_FS_HINT_HZ      = 250.0

# NEW: file write cadence (save-as-you-go)
WRITE_EVERY_MS      = 200   # how often we attempt to write new synchronized rows
WRITE_FLUSH_EVERY_N = 1     # flush after each write batch (keep crash-safe)

# NEW: ticking-clock recording behavior
AUTO_STOP_NO_DATA_SEC = 20.0   # if no frames from ANY device for this long while recording, stop recording
GAP_MAX_SEC           = 0.25   # if a gap between samples around t exceeds this, write NaN (avoid bridging long dropouts)
APP_VERSION           = "v1.0.0"


def _crc16_ccitt(data: bytes | bytearray | memoryview, seed: int = 0xFFFF) -> int:
	crc = seed & 0xFFFF
	for byte in data:
		crc ^= int(byte) << 8
		for _ in range(8):
			if crc & 0x8000:
				crc = ((crc << 1) ^ 0x1021) & 0xFFFF
			else:
				crc = (crc << 1) & 0xFFFF
	return crc


def _decode_wear_frame(chunk: bytes | bytearray | memoryview, last_idx):
	if len(chunk) != FRAME_SIZE:
		return None, last_idx
	(
		sync_word,
		sample_idx,
		ax_i, ay_i, az_i,
		gx_i, gy_i, gz_i,
		red_i, ir_i,
		hr_i, spo2_i, batt_i
	) = FRAME_STRUCT.unpack(chunk)

	valid = (
		sync_word == FRAME_SYNC_WORD and
		0 <= batt_i <= 100 and
		0 <= spo2_i <= 100 and
		0 <= hr_i <= 255
	)
	if valid and last_idx is not None:
		valid = (sample_idx > last_idx and sample_idx - last_idx <= 64) or sample_idx == 1
	if not valid:
		return None, last_idx

	return (
		int(sample_idx),
		ax_i * ACC_SCALE,
		ay_i * ACC_SCALE,
		az_i * ACC_SCALE,
		gx_i * GYR_SCALE,
		gy_i * GYR_SCALE,
		gz_i * GYR_SCALE,
		float(red_i),
		float(ir_i),
		int(hr_i),
		int(spo2_i),
		int(batt_i),
	), int(sample_idx)


def _parse_wearable_notify_buffer(buf: bytearray, last_idx):
	accepted = []
	search_pos = 0
	keep_from = len(buf)

	while search_pos < len(buf):
		packet_pos = buf.find(BLE_PACKET_MAGIC, search_pos)
		frame_pos = buf.find(FRAME_SYNC_BYTES, search_pos)
		candidates = [p for p in (packet_pos, frame_pos) if p >= 0]
		if not candidates:
			break

		pos = min(candidates)
		if packet_pos == pos:
			if len(buf) - pos < BLE_HEADER_BYTES:
				keep_from = pos
				break

			frame_count = buf[pos + 12]
			expected_len = BLE_HEADER_BYTES + (frame_count * FRAME_SIZE) + BLE_CRC_BYTES
			packet_valid = (
				buf[pos + 2] == BLE_PACKET_TYPE_WEARABLE and
				buf[pos + 3] == BLE_PACKET_VERSION and
				0 < frame_count <= 7
			)
			if packet_valid and len(buf) - pos < expected_len:
				keep_from = pos
				break

			if packet_valid:
				packet = bytes(buf[pos:pos + expected_len])
				expected_crc = (packet[-2] << 8) | packet[-1]
				if expected_crc == _crc16_ccitt(packet[:-2]):
					offset = BLE_HEADER_BYTES
					for _ in range(frame_count):
						frame, last_idx = _decode_wear_frame(packet[offset:offset + FRAME_SIZE], last_idx)
						if frame is not None:
							accepted.append(frame)
						offset += FRAME_SIZE
					search_pos = pos + expected_len
					continue

			search_pos = pos + 1
			continue

		if len(buf) - pos < FRAME_SIZE:
			keep_from = pos
			break

		frame, last_idx = _decode_wear_frame(buf[pos:pos + FRAME_SIZE], last_idx)
		if frame is not None:
			accepted.append(frame)
			search_pos = pos + FRAME_SIZE
		else:
			search_pos = pos + 1

	if search_pos < len(buf):
		remaining = bytes(buf[keep_from if keep_from < len(buf) else search_pos:])
		max_keep = max((BLE_HEADER_BYTES + 7 * FRAME_SIZE + BLE_CRC_BYTES) - 1, FRAME_SIZE - 1)
		return accepted, last_idx, bytearray(remaining[-max_keep:])
	return accepted, last_idx, bytearray()


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
		self.pg = _get_pyqtgraph()
		self.viewer = parent_viewer

		self.setWindowTitle("TinZr Wearable")
		self.setWindowIcon(QtGui.QIcon(ICON_PATH))
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

			glw = self.pg.GraphicsLayoutWidget()
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

				curve = p.plot([], [], pen=self.pg.mkPen(self.colors.get(key, "#E0E8FF"), width=1.4))
				axes[key] = p
				curves[key] = curve

			self.tabs.addTab(w, label)
			self.tab_widgets[did] = w
			self.tab_axes[did] = axes
			self.tab_curves[did] = curves

	def _update_all(self):
		np_mod = _get_numpy()
		# Pull plot snapshot (per device)
		with self.viewer.plot_lock:
			snap = {did: dict(buf) for did, buf in self.viewer.plot_buffers.items()}
		with self.viewer.fs_lock:
			fs_snap = dict(self.viewer.fs_est)

		# time axis for display: fixed WINDOW_SEC in seconds
		n_ds = int(max(2, round(WINDOW_SEC * FS_RESAMP_HZ)))
		t_ds = np_mod.linspace(0.0, WINDOW_SEC, n_ds, endpoint=False)

		for did, buf in snap.items():
			if did not in self.tab_curves:
				continue

			idx = np_mod.asarray(buf.get("idx", []), dtype=float)
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
			ix0 = int(np_mod.searchsorted(t_raw, t0, side="left"))
			tw = t_raw[ix0:]
			if len(tw) < 2:
				continue

			for key in self.signal_order:
				y = np_mod.asarray(buf.get(key, []), dtype=float)
				L = min(len(t_raw), len(y))
				if L < 4:
					continue
				yw = y[-L:][ix0:]

				if len(yw) < 2 or (tw[-1] <= tw[0]):
					continue

				# Resample for smooth plotting
				y_ds = np_mod.interp(t_ds, tw, yw)
				self.tab_curves[did][key].setData(t_ds, y_ds)
				self.tab_axes[did][key].setXRange(0.0, WINDOW_SEC, padding=0.0)


# =========================
# RSSI Bars widget (signal indicator)
# =========================
class RSSIBars(QtWidgets.QWidget):
	def __init__(self, parent=None):
		super().__init__(parent)
		self._rssi = None
		self.setFixedSize(60, 18)

	def setRssi(self, rssi_dbm):
		# rssi_dbm: int or None
		self._rssi = rssi_dbm if rssi_dbm is None else int(rssi_dbm)
		self.update()

	def _bars_from_rssi(self, rssi):
		# thresholds: tweak as desired
		if rssi is None:
			return 0
		if rssi >= -55:
			return 4
		if rssi >= -65:
			return 3
		if rssi >= -75:
			return 2
		if rssi >= -85:
			return 1
		return 0

	def paintEvent(self, event):
		p = QtGui.QPainter(self)
		p.setRenderHint(QtGui.QPainter.Antialiasing, True)

		w = self.width()
		h = self.height()

		n = 4
		gap = 3
		bar_w = int((w - gap * (n - 1)) / n) if n > 0 else w

		bars_on = self._bars_from_rssi(self._rssi)

		for i in range(n):
			# bar heights grow to the right
			bar_h = int(h * (0.35 + 0.18 * i))
			x = i * (bar_w + gap)
			y = h - bar_h

			if i < bars_on:
				brush = QtGui.QBrush(QtGui.QColor(180, 220, 255, 230))
			else:
				brush = QtGui.QBrush(QtGui.QColor(180, 220, 255, 60))

			p.setPen(QtCore.Qt.NoPen)
			p.setBrush(brush)
			p.drawRoundedRect(QtCore.QRectF(x, y, bar_w, bar_h), 2, 2)

		p.end()


# =========================
# Device row widget
# =========================
class TinZrDeviceRow(QtWidgets.QFrame):
	connect_changed = QtCore.pyqtSignal(str, bool)
	request_remove  = QtCore.pyqtSignal(str)
	request_battery = QtCore.pyqtSignal(str)

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
		if hasattr(self.batt, "clicked"):
			self.batt.clicked.connect(lambda: self.request_battery.emit(self.device_id))

		self.lbl_vitals = QtWidgets.QLabel("HR: -- bpm   SpO₂: -- %")
		self.lbl_vitals.setStyleSheet("color: rgba(255,255,255,220);")

		self.lbl_rate = QtWidgets.QLabel("RX: -- Hz")
		self.lbl_rate.setStyleSheet("color: rgba(180,220,255,220); font-weight:600;")

		# RSSI indicator
		self.rssi_bars = RSSIBars()
		self.rssi_bars.setToolTip("Signal strength (RSSI)")
		self.lbl_rssi = QtWidgets.QLabel("RSSI: -- dBm")
		self.lbl_rssi.setStyleSheet("color: rgba(180,220,255,200);")

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

		# RX + RSSI row
		hrow = QtWidgets.QHBoxLayout()
		hrow.setContentsMargins(0, 0, 0, 0)
		hrow.setSpacing(8)
		hrow.addWidget(self.lbl_rate, 0, QtCore.Qt.AlignRight)
		hrow.addWidget(self.rssi_bars, 0, QtCore.Qt.AlignVCenter)
		hrow.addWidget(self.lbl_rssi, 0, QtCore.Qt.AlignRight)
		wr = QtWidgets.QWidget()
		wr.setLayout(hrow)
		vbox.addWidget(wr, alignment=QtCore.Qt.AlignRight)

		w = QtWidgets.QWidget()
		w.setLayout(vbox)
		lay.addWidget(w, 1, 5, alignment=QtCore.Qt.AlignRight)

		lay.addWidget(self.btn_remove, 0, 6, 2, 1, alignment=QtCore.Qt.AlignVCenter)

		self.lbl_rate.hide()
		self.rssi_bars.hide()
		self.lbl_rssi.hide()

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

	def set_rssi(self, rssi_dbm):
		if rssi_dbm is None:
			self.lbl_rssi.setText("RSSI: -- dBm")
			self.rssi_bars.setRssi(None)
			return
		try:
			r = int(rssi_dbm)
		except Exception:
			r = None
		if r is None:
			self.lbl_rssi.setText("RSSI: -- dBm")
			self.rssi_bars.setRssi(None)
		else:
			self.lbl_rssi.setText(f"RSSI: {r:d} dBm")
			self.rssi_bars.setRssi(r)

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
	status_update = QtCore.pyqtSignal(str)

	def __init__(self):
		super().__init__()

		self.setWindowTitle("TinZr (Multi) Wearable")
		self.setWindowIcon(QtGui.QIcon(ICON_PATH))
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
		self.client_session = {}	# did -> monotonically increasing connection token
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
		self.last_sample_idx = {}	# did -> last valid firmware sample index
		self.sample_count = {}		# did -> int
		self.idx_raw = {}			# did -> list[int]
		self.data_raw = {}			# did -> dict[key] -> list[float]
		self.rec_idx_raw = {}		# did -> list[int]
		self.rec_data_raw = {}		# did -> dict[key] -> list[float]
		self.rec_t_raw = {}		# did -> list[float] (seconds since record start, perf_counter-based)

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

		# RSSI (per device)
		self.rssi_dbm = {}
		# Advertisement RSSI cache (addr -> (rssi_dbm, t_perf))
		self.adv_rssi = {}
		self._adv_scanner = None
		self._rssi_fail_last = {}  # did -> perf_counter timestamp (throttle errors)

		# ===== Recording output =====
		self.record_path = None
		self.record_file = None
		self.record_writer = None
		self.record_fs = float(FS_RESAMP_HZ)  # fixed output grid
		self.rec_devices_order = []
		self.rec_alias_map = {}
		self.record_pending = {}
		self.record_next_idx = 1
		self.record_next_sample_idx = None
		self.record_skip_count = 0

		# NEW: incremental (save-as-you-go) writer state
		self._write_cursor = 0
		self._csv_header_written = False
		self._flush_counter = 0

		# NEW: ticking-clock time base for recording
		self._t_rec0 = None
		self._last_any_record_frame = None

		# NEW: per-device reconstructed sample time (seconds since _t_rec0)
		self._rec_last_t = {}		# did -> last reconstructed t_rel

		# ===== Plot buffers (snapshot-friendly) =====
		self.plot_lock = threading.Lock()
		self.plot_buffers = {}		# did -> dict[str] -> list[float]
		self.plot_window = None

		# ===== Signals =====
		self.scan_finished.connect(self._handle_scan_result)
		self.vitals_update.connect(self._on_vitals_update)
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

		grid.addWidget(QtWidgets.QLabel("Record"), r, 2, alignment=QtCore.Qt.AlignRight)
		self.toggle_record = ToggleSwitch()
		self.toggle_record.setChecked(False)
		self.toggle_record.toggled.connect(self.on_record_toggled)
		self.toggle_record.setEnabled(False)
		grid.addWidget(self.toggle_record, r, 3, alignment=QtCore.Qt.AlignLeft)

		grid.addWidget(QtWidgets.QLabel("Subject"), r, 4, alignment=QtCore.Qt.AlignRight)
		self.ed_subject_name = QtWidgets.QLineEdit()
		self.ed_subject_name.setPlaceholderText("Subject name")
		grid.addWidget(self.ed_subject_name, r, 5)

		self.btn_plot = QtWidgets.QPushButton("Show Plots")
		self.btn_plot.clicked.connect(self.on_plot_clicked)
		self.btn_plot.setEnabled(True)
		grid.addWidget(self.btn_plot, r, 6)

		self.lbl_out = QtWidgets.QLabel(
			f"Fixed Fs: {self.record_fs:.1f} Hz"
		)
		self.lbl_out.setStyleSheet("color: rgba(200,240,255,200);")
		grid.addWidget(self.lbl_out, r, 7, 1, 2, alignment=QtCore.Qt.AlignRight)

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

		version_row = QtWidgets.QHBoxLayout()
		version_row.addStretch(1)
		self.lbl_version = QtWidgets.QLabel(APP_VERSION)
		self.lbl_version.setStyleSheet("font-size: 8pt; color: #A8B3CF;")
		version_row.addWidget(self.lbl_version, 0, QtCore.Qt.AlignRight)
		main_layout.addLayout(version_row)

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

		# NEW: RSSI poll timer (updates even when not streaming)
		self.rssi_timer = QtCore.QTimer(self)
		self.rssi_timer.setTimerType(QtCore.Qt.PreciseTimer)
		self.rssi_timer.timeout.connect(self._rssi_tick)
		self.rssi_timer.start(1000)

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


	def _ensure_adv_scanner_started(self):
		"""Start a continuous BLE advertisement scanner (for RSSI) once."""
		if getattr(self, "_adv_scanner", None) is not None:
			return
		try:
			asyncio.run_coroutine_threadsafe(self._start_adv_scanner_async(), self.loop)
		except Exception:
			pass

	async def _start_adv_scanner_async(self):
		"""Run a long-lived BleakScanner with detection_callback to collect adv RSSI."""
		if getattr(self, "_adv_scanner", None) is not None:
			return
		try:
			scanner_cls, _ = _get_bleak()

			def _cb(device, adv):
				try:
					addr = getattr(device, "address", None)
					if not addr:
						return
					# NOTE: AdvertisementData.rssi is the supported API (BLEDevice.rssi is deprecated)
					rssi = getattr(adv, "rssi", None)
					if rssi is None:
						return
					self.adv_rssi[addr] = (int(rssi), time.perf_counter())
				except Exception:
					pass

			self._adv_scanner = scanner_cls(detection_callback=_cb)
			await self._adv_scanner.start()
		except Exception as e:
			self._adv_scanner = None
			try:
				self.status_update.emit(f"Adv RSSI scanner failed: {e}")
			except Exception:
				pass

	# ============ Status ============
	def _set_status(self, text: str):
		self.label_status.setText(f"Status: {text}")

	# ============ Plot ============
	def on_plot_clicked(self):
		try:
			if self.plot_window is None:
				self.plot_window = MultiDevicePlotWindow(self)
			else:
				self.plot_window._rebuild_tabs()
			self.plot_window.show()
			self.plot_window.raise_()
			self.plot_window.activateWindow()
		except Exception as e:
			self.plot_window = None
			self.status_update.emit(f"Plot window failed to open: {e}")
			QtWidgets.QMessageBox.critical(
				self,
				"Plot Error",
				f"Could not open the plot window.\n\n{e}",
			)

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
		self._ensure_adv_scanner_started()
		self.btn_scan.setEnabled(False)
		self.spinner.start()
		self.status_update.emit("Scanning BLE...")
		asyncio.run_coroutine_threadsafe(self._scan_ble(), self.loop)

	async def _scan_ble(self):
		try:
			scanner_cls, _ = _get_bleak()
			devs = await scanner_cls.discover(timeout=5.0)
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
		row.request_battery.connect(self._on_row_battery_requested)
		self.rows[did] = row

		with self.state_lock:
			self.device_order.append(did)

		self.devices_layout.insertWidget(self.devices_layout.count() - 1, row)

		self.byte_buf[did] = bytearray()

		with self.data_lock:
			self.last_sample_idx[did] = None
			self.sample_count[did] = 0
			self.idx_raw[did] = []
			self.data_raw[did] = {k: [] for k in ("ax","ay","az","gx","gy","gz","red","ir")}
			self.rec_idx_raw[did] = []
			self.rec_data_raw[did] = {k: [] for k in self.rec_keys}
			self.rec_t_raw[did] = []

		with self.fs_lock:
			self.fs_est[did] = float(FW_FS_HINT_HZ)

		with self.rate_lock:
			self.rx_win_t0[did] = time.perf_counter()
			self.rx_win_cnt[did] = 0
			self.rx_hz[did] = 0.0

		self.rssi_dbm[did] = None
		try:
			row.set_rssi(None)
		except Exception:
			pass

		with self.plot_lock:
			self.plot_buffers[did] = {"idx": [], "ax": [], "ay": [], "az": [], "gx": [], "gy": [], "gz": [], "red": [], "ir": []}

		self.record_pending[did] = {}

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
		self.client_session.pop(did, None)
		self.rssi_dbm.pop(did, None)

		# Only now: drop client and buffers
		self.byte_buf.pop(did, None)
		self.clients.pop(did, None)
		self.connected.discard(did)

		with self.data_lock:
			self.last_sample_idx.pop(did, None)
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

		self.record_pending.pop(did, None)

		if self.plot_window is not None:
			self.plot_window._rebuild_tabs()

		if len(self.rows) == 0:
			self.toggle_stream.setChecked(False)
			self.toggle_stream.setEnabled(False)
			self.toggle_record.setChecked(False)
			self.toggle_record.setEnabled(False)

		self.status_update.emit("Removed device (disconnected).")

	
	@QtCore.pyqtSlot(str, int)
	def _handle_ble_disconnected(self, did: str, session_id: int):
		if self.client_session.get(did) != session_id:
			return

		# mark disconnected
		self.clients.pop(did, None)
		self.connected.discard(did)
		self.rssi_dbm.pop(did, None)

		# UI: turn off connect slider + clear RSSI
		row = self.rows.get(did)
		if row:
			row.set_toggle(False)
			row.set_rssi(None)

		self.status_update.emit(f"Connection lost: {row.alias() if row else did}")



	def _on_ble_disconnected(self, did: str, session_id: int):
		"""
		Called automatically by Bleak when a device disconnects unexpectedly.
		"""
		# Run UI/state changes in the Qt thread
		QtCore.QMetaObject.invokeMethod(
			self,
			"_handle_ble_disconnected",
			QtCore.Qt.QueuedConnection,
			QtCore.Q_ARG(str, did),
			QtCore.Q_ARG(int, session_id),
		)

	
	# ============ Connect / disconnect ============
	def _on_row_connect_changed(self, did: str, connect: bool):
		if connect:
			self.status_update.emit(f"Connecting: {self.rows[did].alias()} ...")
			asyncio.run_coroutine_threadsafe(self._connect_device_async(did), self.loop)
		else:
			self.status_update.emit(f"Disconnecting: {self.rows[did].alias()} ...")
			asyncio.run_coroutine_threadsafe(self._disconnect_device_async(did), self.loop)

	def _on_row_battery_requested(self, did: str):
		if did not in self.connected:
			self.status_update.emit("Connect the device before requesting battery.")
			return
		asyncio.run_coroutine_threadsafe(self._send_cmd(did, CMD_BATT), self.loop)
		row = self.rows.get(did)
		name = row.alias() if row else did
		self.status_update.emit(f"Requested battery level: {name}")

	async def _connect_device_async(self, did: str):
		if did in self.connected:
			self.status_update.emit(f"Connected: {self.rows[did].alias()}")
			return

		info = self.rows[did].info
		session_id = int(self.client_session.get(did, 0)) + 1
		self.client_session[did] = session_id
		_, client_cls = _get_bleak()
		client = client_cls(	info.address,	
								timeout=10.0,
								disconnected_callback=lambda c, _did=did, _sid=session_id: self._on_ble_disconnected(_did, _sid)
								)

		try:
			await client.connect()
			await client.start_notify(TINZR_BLE_TX_CHAR_UUID, lambda sender, data: self._on_notify(did, data))

			self.clients[did] = client
			self.connected.add(did)

			if self.streaming:
				with self.data_lock:
					self.last_sample_idx[did] = None
					self.sample_count[did] = 0
					self.idx_raw[did].clear()
					for k in self.data_raw[did]:
						self.data_raw[did][k].clear()
					self.record_pending[did] = {}
				await self._send_cmd(did, CMD_START)

			self.rows[did].set_toggle(True)
			self.status_update.emit(f"Connected: {self.rows[did].alias()}")

			# initial RSSI read
			asyncio.run_coroutine_threadsafe(self._read_rssi_async(did), self.loop)

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
				try:
					self.rows[did].set_rssi(None)
				except Exception:
					pass
			self.connected.discard(did)
			self.rssi_dbm.pop(did, None)
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
		self.rssi_dbm.pop(did, None)

		if did in self.rows:
			self.rows[did].set_toggle(False)
			try:
				self.rows[did].set_rssi(None)
			except Exception:
				pass

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
			self.status_update.emit("No connected devices.")
			return

		self.streaming = True
		self.status_update.emit("Streaming: starting...")

		with self.data_lock:
			for did in self.connected:
				self.last_sample_idx[did] = None
				self.sample_count[did] = 0
				self.idx_raw[did].clear()
				for k in self.data_raw[did]:
					self.data_raw[did][k].clear()
				self.record_pending[did] = {}

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

	
	async def _read_rssi_async(self, did: str):
		client = self.clients.get(did)
		if not client:
			return

		rssi = None

		# 1) Newer Bleak API
		try:
			if hasattr(client, "get_rssi"):
				rssi = await client.get_rssi()
		except Exception:
			rssi = None

		# 2) Some backends expose it on the backend object
		if rssi is None:
			try:
				backend = getattr(client, "_backend", None)
				if backend is not None and hasattr(backend, "get_rssi"):
					rssi = await backend.get_rssi()
			except Exception:
				rssi = None

		# 3) If still None, RSSI not available on this stack
		if rssi is None:
			try:
				self._rssi_fail_last[did] = time.perf_counter()
			except Exception:
				pass
			return

		try:
			self.rssi_update.emit(did, int(rssi))
		except Exception:
			pass


	# ============ Recording ============
	def _legacy_on_record_toggled_old(self, checked: bool):
		if checked:
			self._start_recording()
		else:
			self._stop_recording()

	def _legacy_start_recording_old(self):
		if self.recording:
			return
		if len(self.rows) == 0:
			self.toggle_record.setChecked(False)
			self.status_update.emit("Add devices first.")
			return
	
	
		timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
		default_name = f"TinZrMultiWearable_recording_{timestamp}.csv"


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
				self.rec_t_raw[did].clear()
				for k in self.rec_data_raw[did]:
					self.rec_data_raw[did][k].clear()

		# NEW: ticking-clock time origin for this recording
		self._t_rec0 = time.perf_counter()
		self._last_any_record_frame = self._t_rec0


		# NEW: clear reconstructed timestamp continuity
		self._rec_last_t.clear()

		# NEW: write header placeholders now (data will be appended immediately)
		self._write_cursor = 0
		self._csv_header_written = False
		self._flush_counter = 0
		self._write_header_placeholders()

		self.recording = True
		self.status_update.emit(f"Recording: ON (save-as-you-go + sync grid) → {fpath}")

	def _legacy_stop_recording_old(self):
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
				self.rec_t_raw[did].clear()
				for k in self.rec_data_raw[did]:
					self.rec_data_raw[did][k].clear()

	# ============ Save-as-you-go writer ============
	
	# ============ RSSI polling ============
	def _rssi_tick(self):
		"""Update RSSI bars/label.

		We keep a continuous advertisement scanner running (WinRT-safe) and use
		AdvertisementData.rssi when available. This works even when devices are
		disconnected: once they advertise again, RSSI resumes automatically.
		"""
		now = time.perf_counter()

		# Update RSSI for ALL devices shown in the UI (connected or not).
		for did, row in list(self.rows.items()):
			# 1) Prefer advertisement RSSI (from continuous scanner)
			pair = self.adv_rssi.get(did)
			if pair is not None:
				rssi, ts = pair
				if (now - float(ts)) <= 3.0:
					row.set_rssi(int(rssi))
					continue

			# 2) If no recent advertisements, optionally try connection-RSSI if supported
			# (Most WinRT/dotnet stacks won't support it; we keep this best-effort.)
			if did in self.connected:
				try:
					client = self.clients.get(did)
					backend = getattr(client, "_backend", None) if client else None
					has_get = (client is not None and hasattr(client, "get_rssi")) or (backend is not None and hasattr(backend, "get_rssi"))
					if has_get:
						# Throttle attempts if backend keeps throwing
						last_fail = float(self._rssi_fail_last.get(did, 0.0))
						if (now - last_fail) >= 2.0:
							asyncio.run_coroutine_threadsafe(self._read_rssi_async(did), self.loop)
						else:
							row.set_rssi(None)
					else:
						row.set_rssi(None)
				except Exception:
					row.set_rssi(None)
			else:
				# Not connected and no recent ads => unknown
				row.set_rssi(None)

	def _writer_tick(self):
		# Keep file updated while recording (do NOT do heavy work in BLE notify thread)
		if not self.recording:
			return

		# Auto-stop if nothing has been received from ANY device for too long
		now = time.perf_counter()
		if self._last_any_record_frame is not None:
			if (now - float(self._last_any_record_frame)) >= float(AUTO_STOP_NO_DATA_SEC):
				self.status_update.emit(f"No data for {AUTO_STOP_NO_DATA_SEC:.0f}s → auto-stopping recording.")
				# stop gracefully without re-triggering toggled logic twice
				try:
					self.toggle_record.blockSignals(True)
					self.toggle_record.setChecked(False)
				finally:
					self.toggle_record.blockSignals(False)
				self._stop_recording()
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
		f.write("# Columns: time_s + per-device channels (red_nA, ir_nA, ax, ay, az, gx, gy, gz, hr_bpm, spo2_pct, batt_pct)\n")
		f.write("# PPG units: approximate MAX30102 photodiode current in nanoamps\n")
		f.write("# PPG conversion: current_nA = adc_count * 16384 / 262143\n")

		cols = ["time_s"]
		for did in devs:
			a = _safe_name(self.rec_alias_map.get(did, did))
			cols += [
				f"{a}_red_nA", f"{a}_ir_nA",
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
		Ticking-clock writer:
		- time_s is based on perf_counter() since recording started (self._t_rec0)
		- we ALWAYS advance time (fixed record_fs grid)
		- if a device has no data for a given time, we write NaN for that device's fields
		"""
		f = self.record_file
		if f is None or not self._csv_header_written:
			return

		if self._t_rec0 is None:
			# Should not happen, but fail safe
			self._t_rec0 = time.perf_counter()
			self._last_any_record_frame = self._t_rec0

		with self.state_lock:
			devs = list(self.rec_devices_order)
		if not devs:
			return

		now = time.perf_counter()
		t_end = float(now - float(self._t_rec0))
		if t_end < 0:
			t_end = 0.0

		# How many output rows should exist up to NOW?
		n_out = int(t_end * float(self.record_fs))
		if n_out <= self._write_cursor:
			return

		dt = 1.0 / float(self.record_fs)

		# Snapshot buffers under lock (short)
		with self.data_lock:
			snap = {}
			for did in devs:
				tt = list(self.rec_t_raw.get(did, []))
				snap[did] = {
					"t": tt,
					"data": {k: list(self.rec_data_raw.get(did, {}).get(k, [])) for k in self.rec_keys},
				}

		def _fmt(v, is_intish: bool = False):
			np_mod = _get_numpy()
			if v is None or (isinstance(v, float) and np_mod.isnan(v)):
				return "nan"
			if is_intish:
				return f"{float(v):.2f}"
			return f"{float(v):.6f}"

		def _interp_or_nan(t, tt, vv):
			np_mod = _get_numpy()
			# Linear interpolation, but avoid bridging long gaps.
			if tt is None or vv is None:
				return float("nan")
			L = min(len(tt), len(vv))
			if L < 2:
				return float("nan")
			tt = np_mod.asarray(tt[:L], dtype=float)
			vv = np_mod.asarray(vv[:L], dtype=float)

			if t < tt[0] or t > tt[-1]:
				return float("nan")

			j = int(np_mod.searchsorted(tt, t, side="left"))
			if j <= 0 or j >= len(tt):
				return float("nan")

			# If the bracket gap is too large, treat as dropout
			if (tt[j] - tt[j - 1]) > float(GAP_MAX_SEC):
				return float("nan")

			return float(np_mod.interp(t, tt, vv))

		def _zoh_or_nan(t, tt, vv):
			np_mod = _get_numpy()
			# Zero-order hold ONLY within available time range (no holding past last sample).
			if tt is None or vv is None:
				return float("nan")
			L = min(len(tt), len(vv))
			if L < 1:
				return float("nan")
			tt = np_mod.asarray(tt[:L], dtype=float)
			vv = np_mod.asarray(vv[:L], dtype=float)

			if t < tt[0] or t > tt[-1]:
				return float("nan")

			j = int(np_mod.searchsorted(tt, t, side="right") - 1)
			if j < 0 or j >= len(vv):
				return float("nan")

			# If the time since last sample is too large, treat as dropout
			if (t - tt[j]) > float(GAP_MAX_SEC):
				return float("nan")

			return float(vv[j])

		# Write new rows [cursor .. n_out)
		for i in range(self._write_cursor, n_out):
			t = i * dt
			row = [f"{t:.6f}"]

			for did in devs:
				tt = snap[did]["t"]
				dd = snap[did]["data"]

				# IMPORTANT: per-device output order must match header
				for k in self.rec_keys:
					vals = dd.get(k, [])
					if k in ("hr", "spo2", "batt"):
						v = _zoh_or_nan(t, tt, vals)
						row.append(_fmt(v, is_intish=True))
					else:
						v = _interp_or_nan(t, tt, vals)
						row.append(_fmt(v, is_intish=False))

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
		b = self.byte_buf.get(did)
		if b is None:
			self.byte_buf[did] = bytearray()
			b = self.byte_buf[did]

		b.extend(data)
		last_idx = self.sample_count.get(f"{did}__fw_last_idx", None)
		accepted, last_idx, self.byte_buf[did] = _parse_wearable_notify_buffer(b, last_idx)

		if accepted:
			now = time.perf_counter()
			with self.fs_lock:
				self.fs_est[did] = float(FW_FS_HINT_HZ)

			last = accepted[-1]
			self.vitals_update.emit(did, last[9], last[10], last[11])

			if self.streaming:
				with self.data_lock:
					self.sample_count[f"{did}__fw_last_idx"] = last_idx
					for sample_idx, ax, ay, az, gx, gy, gz, red, ir, hr_i, spo2_i, batt_i in accepted:
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

						if self.recording:
							t_rel = (sc - 1) / float(self.record_fs)
							self._last_any_record_frame = now
							self.rec_t_raw[did].append(t_rel)
							self.rec_idx_raw[did].append(sample_idx)
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

	def _on_rssi_update(self, did: str, rssi: int):
		if int(rssi) == 9999:
			self.rssi_dbm[did] = None
		else:
			self.rssi_dbm[did] = int(rssi)

		row = self.rows.get(did)
		if row:
			row.set_rssi(self.rssi_dbm.get(did))


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

	# ============ GUI.py-compatible overrides ============
	def on_record_toggled(self, checked: bool):
		if checked:
			self._start_recording()
		else:
			self._stop_recording()

	def _start_recording(self):
		if self.recording:
			return
		if len(self.connected) == 0:
			self.toggle_record.setChecked(False)
			self.status_update.emit("No connected devices.")
			return
		if not self.streaming:
			self._start_stream_all()
			if not self.streaming:
				self.toggle_record.setChecked(False)
				return

		subject_text = self.ed_subject_name.text().strip()
		if not subject_text:
			self.toggle_record.setChecked(False)
			self.status_update.emit("Enter a subject name before recording.")
			return

		with self.state_lock:
			self.rec_devices_order = [did for did in self.device_order if did in self.connected]
		self.rec_alias_map = {did: self.rows[did].alias() for did in self.rec_devices_order if did in self.rows}
		participant = _safe_name(subject_text)
		if not participant.startswith("sub-"):
			participant = f"sub-{participant}"
		if len(self.rec_devices_order) == 1:
			device_token = f"device-{_safe_name(self.rec_alias_map.get(self.rec_devices_order[0], self.rec_devices_order[0]))}"
		else:
			device_token = "device-multi-" + "+".join(
				_safe_name(self.rec_alias_map.get(did, did)) for did in self.rec_devices_order
			)
		now_local = datetime.now().astimezone()
		timestamp_file = now_local.strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + now_local.strftime("%z")
		timestamp_header = now_local.strftime("%Y-%m-%dT%H:%M:%S:%f")[:-3] + ".0000"
		file_base = f"{participant}_{timestamp_file}"
		default_name = f"{file_base}.csv"
		start_dir = os.path.dirname(os.path.abspath(__file__))
		fpath, _ = QtWidgets.QFileDialog.getSaveFileName(
			self,
			"Save recording CSV",
			os.path.join(start_dir, default_name),
			"CSV Files (*.csv);;All Files (*)"
		)
		if not fpath:
			self.toggle_record.setChecked(False)
			self._stop_stream_all()
			self.status_update.emit("Recording cancelled.")
			return
		if not fpath.lower().endswith(".csv"):
			fpath += ".csv"
		file_base = os.path.splitext(os.path.basename(fpath))[0]
		try:
			self.record_file = open(fpath, "w", newline="", buffering=1)
			self.record_path = fpath
		except Exception as e:
			self.toggle_record.setChecked(False)
			self._stop_stream_all()
			self.status_update.emit(f"Record open failed: {e}")
			return

		self.record_writer = csv.writer(self.record_file)
		self.record_pending = {did: {} for did in self.rec_devices_order}
		self.record_next_idx = 1
		self.record_next_sample_idx = None
		self.record_skip_count = 0
		self._t_rec0 = time.perf_counter()
		start_local_millis = int(self._t_rec0 * 1000.0)
		sample_interval_ms = 1000.0 / float(FW_FS_HINT_HZ)

		self.record_file.write("# ================================================\n")
		self.record_file.write("# TinZrWearable\n")
		self.record_file.write("# ================================================\n")
		self.record_file.write(f"# file_base: {file_base}\n")
		self.record_file.write(f"# participant: {participant}\n")
		self.record_file.write(f"# start_time_str: {device_token}|{timestamp_header}\n")
		self.record_file.write(f"# sample_interval_ms: {sample_interval_ms:.3f}\n")
		self.record_file.write(f"# nominal_fs_hz: {float(FW_FS_HINT_HZ):.3f}\n")
		self.record_file.write(f"# start_local_millis: {start_local_millis}\n")
		self.record_file.write("# imu_accel_units: g\n")
		self.record_file.write("# imu_gyro_units: dps\n")
		self.record_file.write("# imu_accel_fullscale: +/-8g\n")
		self.record_file.write("# imu_gyro_fullscale: +/-1000dps\n")
		self.record_file.write("# -----------------------------------------------\n")
		header = ["sync_idx", "host_time_s"]
		for did in self.rec_devices_order:
			a = _safe_name(self.rec_alias_map.get(did, did))
			header.extend([
				f"{a}_sample_idx",
				f"{a}_ax",
				f"{a}_ay",
				f"{a}_az",
				f"{a}_gx",
				f"{a}_gy",
				f"{a}_gz",
				f"{a}_red_nA",
				f"{a}_ir_nA",
				f"{a}_hr_bpm",
				f"{a}_spo2_pct",
				f"{a}_batt_pct",
			])
		self.record_writer.writerow(header)
		self.recording = True
		self.btn_plot.setEnabled(False)
		if self.plot_window is not None:
			self.plot_window.hide()
		self.status_update.emit(f"Recording to {fpath}")

	def _stop_recording(self):
		if not self.recording:
			return
		self.recording = False
		if self.record_file is not None:
			try:
				self.record_file.write("====================RecordingEndsHere====================\n")
				self.record_file.flush()
				self.record_file.close()
			except Exception:
				pass
		self.record_file = None
		self.record_writer = None
		self.record_pending = {}
		self.record_next_idx = 1
		self.record_next_sample_idx = None
		self.rec_devices_order = []
		self.rec_alias_map = {}
		skipped = self.record_skip_count
		self.record_skip_count = 0
		self.btn_plot.setEnabled(True)
		self.status_update.emit(f"Recording stopped. Skipped sync rows: {skipped}")

	def _writer_tick(self):
		return

	def _flush_sync_rows(self):
		if not self.recording or self.record_writer is None:
			return
		if not self.rec_devices_order:
			return

		while True:
			if self.record_next_sample_idx is None:
				if any(not self.record_pending.get(did) for did in self.rec_devices_order):
					return
				candidate = max(min(self.record_pending[did].keys()) for did in self.rec_devices_order)
				upper = min(max(self.record_pending[did].keys()) for did in self.rec_devices_order)
				while candidate <= upper:
					if all(candidate in self.record_pending.get(did, {}) for did in self.rec_devices_order):
						self.record_next_sample_idx = candidate
						for did in self.rec_devices_order:
							pending = self.record_pending.get(did, {})
							stale_keys = [key for key in pending.keys() if key < candidate]
							for key in stale_keys:
								pending.pop(key, None)
						break
					candidate += 1
				if self.record_next_sample_idx is None:
					return

			have_all = all(
				self.record_next_sample_idx in self.record_pending.get(did, {})
				for did in self.rec_devices_order
			)
			if not have_all:
				latest_ready = []
				for did in self.rec_devices_order:
					pending = self.record_pending.get(did, {})
					if not pending:
						return
					latest_ready.append(max(pending.keys()))
				min_latest = min(latest_ready)
				if min_latest <= self.record_next_sample_idx:
					return
				self.record_next_sample_idx += 1
				self.record_skip_count += 1
				continue

			host_time_s = (self.record_next_idx - 1) / float(FW_FS_HINT_HZ)
			row = [self.record_next_idx, f"{host_time_s:.6f}"]
			for did in self.rec_devices_order:
				sample_idx, ax, ay, az, gx, gy, gz, red, ir, hr, spo2, batt = self.record_pending[did].pop(self.record_next_sample_idx)
				row.extend([
					sample_idx,
					f"{ax:.6f}",
					f"{ay:.6f}",
					f"{az:.6f}",
					f"{gx:.6f}",
					f"{gy:.6f}",
					f"{gz:.6f}",
					f"{red:.6f}",
					f"{ir:.6f}",
					hr,
					spo2,
					batt,
				])
			self.record_writer.writerow(row)
			self.record_next_idx += 1
			self.record_next_sample_idx += 1

	def _on_notify(self, did: str, data: bytes):
		b = self.byte_buf.get(did)
		if b is None:
			self.byte_buf[did] = bytearray()
			b = self.byte_buf[did]
		b.extend(data)

		last_idx = self.last_sample_idx.get(did, None)
		accepted, last_idx, self.byte_buf[did] = _parse_wearable_notify_buffer(b, last_idx)

		if accepted:
			self.last_sample_idx[did] = last_idx
			last = accepted[-1]
			self.vitals_update.emit(did, last[9], last[10], last[11])

			if self.streaming:
				with self.data_lock:
					for sample_idx, ax, ay, az, gx, gy, gz, red, ir, hr_i, spo2_i, batt_i in accepted:
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

						if self.recording:
							if did not in self.record_pending:
								continue
							if self.record_next_sample_idx is not None and sample_idx < self.record_next_sample_idx:
								continue
							self.record_pending[did][sample_idx] = (
								sample_idx, ax, ay, az, gx, gy, gz, red, ir, hr_i, spo2_i, batt_i
							)
				if self.recording:
					self._flush_sync_rows()

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

			try:
				if hasattr(self, "rssi_timer"):
					self.rssi_timer.stop()
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

	w = MultiTinZrViewer()
	w.show()

	sys.exit(app.exec_())


if __name__ == "__main__":
	main()
