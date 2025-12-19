
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

# =========================
# TinZr Multi-Device Viewer
# =========================
#
# - Keeps the TinZr "signature" look by reusing apply_tinzr_theme + ToggleSwitch + Spinner + BatteryWidget
# - Scan ("Find Devices") uses the SAME spinner-overlay animation pattern as your single-device GUI
# - Add as many TinZr devices as you want:
#     * Pick a discovered device, assign an alias, click "Add"
#     * Each added device gets its own row with Connect/Test + Battery + HR/SpO2
# - Global Stream + Record applies to ALL connected devices at once
# - Recording:
#     * "Folder (one CSV per device)" (recommended) -> one CSV per TinZr (fast, clean)
#     * "Single CSV (interleaved)" -> one file with a "device" column
#
# Notes:
# - This expects your same "examples" layout where GUIsHelper.py lives in the parent folder
# - On Windows: keeps BLEAK_BACKEND="dotnet" like your existing TinZrWearable.py

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
CMD_BATT  = b"BAT"

# Optional "flash / identify" command used by Test button.
# If your firmware uses something else, change it here.
CMD_TEST  = b"T"

# ================== Frame format (must match C++) ==============
# <hhhhhhIIBBB (23 bytes)
FRAME_STRUCT = struct.Struct("<hhhhhhIIBBB")
FRAME_SIZE   = FRAME_STRUCT.size  # 23 bytes

ACC_SCALE = 1e-3
GYR_SCALE = 1.0 / 100.0

# ================== Recording ==================
REC_HEADER = "time_s,ax,ay,az,gx,gy,gz,red,ir,hr_bpm,spo2_pct,batt_pct\n"
REC_HEADER_SINGLE = "time_s,device,ax,ay,az,gx,gy,gz,red,ir,hr_bpm,spo2_pct,batt_pct\n"


@dataclass
class DeviceInfo:
	name: str
	address: str


class TinZrDeviceRow(QtWidgets.QFrame):
	"""
	One row in the device list: Alias + address + Connect toggle + Test + Battery + HR/SpO2 + Remove
	"""
	request_remove = QtCore.pyqtSignal(str)     # device_id
	connect_changed = QtCore.pyqtSignal(str, bool)
	test_clicked = QtCore.pyqtSignal(str)

	def __init__(self, device_id: str, info: DeviceInfo, alias: str):
		super().__init__()
		self.device_id = device_id
		self.info = info

		self.setObjectName("deviceRow")
		self.setStyleSheet("""
			QFrame#deviceRow {
				background-color: rgba(255,255,255,0.04);
				border: 1px solid rgba(255,255,255,0.10);
				border-radius: 10px;
			}
		""")

		lay = QtWidgets.QGridLayout(self)
		lay.setContentsMargins(10, 8, 10, 8)
		lay.setHorizontalSpacing(10)
		lay.setVerticalSpacing(6)

		self.ed_alias = QtWidgets.QLineEdit(alias)
		self.ed_alias.setPlaceholderText("Alias / Name (e.g., Kid_01)")
		self.ed_alias.setMinimumWidth(160)

		self.lbl_addr = QtWidgets.QLabel(info.address)
		self.lbl_addr.setStyleSheet("color: #A8B3CF; font-size: 8pt;")

		self.toggle_connect = ToggleSwitch()
		self.toggle_connect.setChecked(False)
		self.toggle_connect.toggled.connect(self._on_connect_toggled)

		self.btn_test = QtWidgets.QPushButton("Test")
		self.btn_test.setFixedWidth(70)
		self.btn_test.clicked.connect(lambda: self.test_clicked.emit(self.device_id))
		self.btn_test.setEnabled(False)

		self.batt = BatteryWidget()
		self.batt.setLevel(None)
		self.batt.clicked.connect(lambda: None)  # keep consistent behavior; main will query if you want

		self.lbl_vitals = QtWidgets.QLabel("HR: -- bpm   SpO₂: -- %")
		self.lbl_vitals.setStyleSheet("""
			QLabel {
				color: #FFFFFF;
				font-size: 8pt;
				font-weight: 500;
				background-color: rgba(0, 0, 0, 120);
				padding: 2px 6px;
				border-radius: 6px;
			}
		""")

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

		lay.addWidget(self.btn_test, 1, 4, alignment=QtCore.Qt.AlignLeft)
		lay.addWidget(self.batt, 0, 5, alignment=QtCore.Qt.AlignRight)
		lay.addWidget(self.lbl_vitals, 1, 5, alignment=QtCore.Qt.AlignRight)
		lay.addWidget(self.btn_remove, 0, 6, 2, 1, alignment=QtCore.Qt.AlignVCenter)

	def alias(self) -> str:
		return self.ed_alias.text().strip() or self.info.name or self.info.address

	def set_connected_ui(self, connected: bool):
		self.btn_test.setEnabled(connected)

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

	def _on_connect_toggled(self, checked: bool):
		self.connect_changed.emit(self.device_id, checked)


class MultiTinZrViewer(QtWidgets.QWidget):
	scan_finished = QtCore.pyqtSignal(object, object)   # (list[DeviceInfo], err)
	vitals_update = QtCore.pyqtSignal(str, int, int, int)  # device_id, hr, spo2, batt
	status_update = QtCore.pyqtSignal(str)

	def __init__(self):
		super().__init__()

		self.setWindowTitle("TinZr Wearable (Multi)")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))

		# Keep a similar "signature" size; devices list is scrollable
		self.setFixedSize(850, 700)
		self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)

		apply_tinzr_theme(self)

		# ===== BLE event loop thread (shared across all clients) =====
		self.loop = asyncio.new_event_loop()
		self.ble_thread = threading.Thread(target=self._run_ble_loop, daemon=True)
		self.ble_thread.start()

		# ===== State =====
		self.discovered: list[DeviceInfo] = []
		self.clients = {}      # device_id -> BleakClient
		self.byte_buf = {}     # device_id -> bytearray
		self.rows = {}         # device_id -> TinZrDeviceRow
		self.connected = set() # device_id
		self.streaming = False
		self.recording = False

		self.rec_mode = "folder"  # "folder" or "single"
		self.rec_folder = None
		self.rec_single_file = None
		self.rec_files = {}        # device_id -> file handle
		self.rec_t0 = None

		# ===== Signals =====
		self.scan_finished.connect(self._handle_scan_result)
		self.vitals_update.connect(self._on_vitals_update)
		self.status_update.connect(self._set_status)

		# ===== UI =====
		main_layout = QtWidgets.QVBoxLayout(self)
		main_layout.setContentsMargins(16, 12, 16, 12)
		main_layout.setSpacing(8)

		# Header (title + hint)
		header = QtWidgets.QWidget()
		h_lay = QtWidgets.QHBoxLayout(header)
		h_lay.setContentsMargins(0, 0, 0, 4)
		h_lay.setSpacing(10)

		title = QtWidgets.QLabel("TinZr Wearable Viewer (Multi-Device)")
		title.setStyleSheet("font-size: 16pt; font-weight: 600; color: #E3F2FD;")
		h_lay.addWidget(title)
		h_lay.addStretch(1)

		main_layout.addWidget(header)

		# Control panel
		ctrl = QtWidgets.QWidget()
		grid = QtWidgets.QGridLayout(ctrl)
		grid.setContentsMargins(0, 0, 0, 0)
		grid.setHorizontalSpacing(12)
		grid.setVerticalSpacing(8)

		r = 0

		self.btn_scan = QtWidgets.QPushButton("Find Devices")
		self.btn_scan.clicked.connect(self.on_scan_clicked)
		grid.addWidget(self.btn_scan, r, 0)

		# SAME spinner overlay style as single-device GUI
		self.spinner = Spinner(radius=8, line_width=2, parent=self.btn_scan)
		self.spinner.raise_()
		self.btn_scan.installEventFilter(self)
		self._center_spinner_on_button()

		self.combo_found = QtWidgets.QComboBox()
		self.combo_found.setMinimumWidth(260)
		grid.addWidget(self.combo_found, r, 1, 1, 3)

		self.ed_new_alias = QtWidgets.QLineEdit()
		self.ed_new_alias.setPlaceholderText("Alias for selected device (optional)")
		grid.addWidget(self.ed_new_alias, r, 4, 1, 2)

		self.btn_add = QtWidgets.QPushButton("Add")
		self.btn_add.clicked.connect(self.on_add_clicked)
		grid.addWidget(self.btn_add, r, 6)

		r += 1

		# Global stream + record + save mode
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

		grid.addWidget(QtWidgets.QLabel("Save Mode"), r, 4, alignment=QtCore.Qt.AlignRight)
		self.combo_save = QtWidgets.QComboBox()
		self.combo_save.addItem("Folder (one CSV per device)", userData="folder")
		self.combo_save.addItem("Single CSV (interleaved)", userData="single")
		self.combo_save.currentIndexChanged.connect(self._on_save_mode_changed)
		grid.addWidget(self.combo_save, r, 5, 1, 2)

		r += 1

		self.lbl_status = QtWidgets.QLabel("Status: Idle")
		self.lbl_status.setObjectName("statusLabel")
		grid.addWidget(self.lbl_status, r, 0, 1, 7)

		main_layout.addWidget(ctrl)

		# Devices list (scroll)
		self.scroll = QtWidgets.QScrollArea()
		self.scroll.setWidgetResizable(True)
		self.scroll.setStyleSheet("QScrollArea { border: none; }")
		self.list_host = QtWidgets.QWidget()
		self.list_lay = QtWidgets.QVBoxLayout(self.list_host)
		self.list_lay.setContentsMargins(0, 0, 0, 0)
		self.list_lay.setSpacing(8)
		self.list_lay.addStretch(1)
		self.scroll.setWidget(self.list_host)
		main_layout.addWidget(self.scroll, 1)

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

	def _set_status(self, text: str):
		self.lbl_status.setText(f"Status: {text}")

	def _run_ble_loop(self):
		asyncio.set_event_loop(self.loop)
		self.loop.run_forever()

	# -------------------------
	# Scan (with same animation)
	# -------------------------
	def on_scan_clicked(self):
		if not self.loop.is_running():
			self._set_status("BLE loop not running")
			return

		self._set_status("Scanning...")
		self.combo_found.clear()
		self.discovered = []

		self.btn_scan.setEnabled(False)
		self.spinner.start()
		self._center_spinner_on_button()

		async def _scan():
			devs = await BleakScanner.discover(timeout=4.0)
			out = []
			for d in devs:
				if d.name and d.name.startswith(DEVICE_PREFIX):
					out.append(DeviceInfo(name=d.name, address=d.address))
			return out

		fut = asyncio.run_coroutine_threadsafe(_scan(), self.loop)

		def _done_callback(f):
			try:
				devs = f.result()
				err = None
			except Exception as e:
				devs = None
				err = e
			self.scan_finished.emit(devs, err)

		fut.add_done_callback(_done_callback)

	def _handle_scan_result(self, devs, err):
		self.spinner.stop()
		self.btn_scan.setEnabled(True)

		if err is not None:
			self._set_status(f"Scan error: {err}")
			return

		if not devs:
			self._set_status("No TinZr devices found")
			return

		self.discovered = devs
		self.combo_found.clear()
		for info in devs:
			self.combo_found.addItem(f"{info.name} ({info.address})", info)

		self._set_status(f"Found {len(devs)} device(s)")

	# -------------------------
	# Add / Remove devices (UI)
	# -------------------------
	def on_add_clicked(self):
		info = self.combo_found.currentData()
		if not info:
			self._set_status("Select a discovered TinZr first")
			return

		device_id = info.address  # stable key
		if device_id in self.rows:
			self._set_status("That TinZr is already added")
			return

		alias = self.ed_new_alias.text().strip()
		if not alias:
			alias = info.name

		row = TinZrDeviceRow(device_id=device_id, info=info, alias=alias)
		row.request_remove.connect(self.on_remove_device)
		row.connect_changed.connect(self.on_device_connect_changed)
		row.test_clicked.connect(self.on_device_test_clicked)

		# Insert above the stretch
		self.list_lay.insertWidget(self.list_lay.count() - 1, row)
		self.rows[device_id] = row
		self.byte_buf[device_id] = bytearray()

		self._set_status(f"Added {info.name}")
		self.ed_new_alias.setText("")

	def on_remove_device(self, device_id: str):
		# If connected, disconnect first
		if device_id in self.connected:
			self._disconnect_device(device_id)

		row = self.rows.pop(device_id, None)
		self.byte_buf.pop(device_id, None)

		if row is not None:
			row.setParent(None)
			row.deleteLater()

		self._refresh_global_controls()
		self._set_status("Removed device")

	# -------------------------
	# Per-device connect/test
	# -------------------------
	def on_device_connect_changed(self, device_id: str, checked: bool):
		if checked:
			self.status_update.emit(f"Connecting to {device_id}...")
			QtWidgets.QApplication.processEvents()
			ok = self._connect_device(device_id)
			if not ok:
				# revert toggle
				row = self.rows.get(device_id)
				if row is not None:
					row.toggle_connect.blockSignals(True)
					row.toggle_connect.setChecked(False)
					row.toggle_connect.blockSignals(False)
				self.status_update.emit("Connect failed")
		else:
			self._disconnect_device(device_id)

		self._refresh_global_controls()

	def on_device_test_clicked(self, device_id: str):
		# "Test" means "flash/identify" by sending CMD_TEST
		if not CMD_TEST:
			self._set_status("CMD_TEST not configured")
			return
		client = self.clients.get(device_id)
		if not client or not client.is_connected:
			self._set_status("Device not connected")
			return

		async def _send():
			await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_TEST)

		fut = asyncio.run_coroutine_threadsafe(_send(), self.loop)
		try:
			fut.result(timeout=2)
			self._set_status(f"Test sent to {self.rows[device_id].alias()}")
		except Exception as e:
			self._set_status(f"Test error: {e}")

	def _connect_device(self, device_id: str) -> bool:
		if not self.loop.is_running():
			self._set_status("BLE loop not running")
			return False

		info = None
		row = self.rows.get(device_id)
		if row is not None:
			info = row.info
		if info is None:
			self._set_status("Unknown device")
			return False

		if device_id in self.clients and self.clients[device_id] and self.clients[device_id].is_connected:
			self.connected.add(device_id)
			row.set_connected_ui(True)
			return True

		async def _connect_coro():
			client = BleakClient(info.address)
			await client.connect()
			if not client.is_connected:
				raise RuntimeError("Failed to connect")

			def _cb(sender, data: bytes, did=device_id):
				self._on_rx(did, sender, data)

			await client.start_notify(TINZR_BLE_TX_CHAR_UUID, _cb)
			return client

		fut = asyncio.run_coroutine_threadsafe(_connect_coro(), self.loop)
		try:
			client = fut.result(timeout=10)
		except Exception as e:
			self._set_status(f"Connect error: {e}")
			return False

		self.clients[device_id] = client
		self.connected.add(device_id)
		row.set_connected_ui(True)
		self._set_status(f"Connected: {row.alias()}")
		return True

	def _disconnect_device(self, device_id: str):
		row = self.rows.get(device_id)
		client = self.clients.get(device_id)

		# If streaming/recording are active, stop them first (global semantics)
		if self.streaming:
			self.on_stream_toggled(False)
			self.toggle_stream.blockSignals(True)
			self.toggle_stream.setChecked(False)
			self.toggle_stream.blockSignals(False)

		if self.recording:
			self.on_record_toggled(False)
			self.toggle_record.blockSignals(True)
			self.toggle_record.setChecked(False)
			self.toggle_record.blockSignals(False)

		if client and client.is_connected and self.loop.is_running():
			self._set_status(f"Disconnecting: {device_id}...")

			async def _disc():
				try:
					await client.stop_notify(TINZR_BLE_TX_CHAR_UUID)
				except Exception:
					pass
				try:
					await client.disconnect()
				except Exception:
					pass

			fut = asyncio.run_coroutine_threadsafe(_disc(), self.loop)
			try:
				fut.result(timeout=3)
			except Exception:
				pass

		self.connected.discard(device_id)
		self.clients.pop(device_id, None)

		if row is not None:
			row.set_connected_ui(False)
			row.toggle_connect.blockSignals(True)
			row.toggle_connect.setChecked(False)
			row.toggle_connect.blockSignals(False)

		self._set_status("Disconnected")

	def _refresh_global_controls(self):
		any_connected = len(self.connected) > 0
		self.toggle_stream.setEnabled(any_connected)
		self.toggle_record.setEnabled(any_connected and self.streaming)

	# -------------------------
	# Global stream (all devices)
	# -------------------------
	def on_stream_toggled(self, checked: bool):
		if checked:
			ok = self._start_stream_all()
			if not ok:
				self.toggle_stream.blockSignals(True)
				self.toggle_stream.setChecked(False)
				self.toggle_stream.blockSignals(False)
		else:
			self._stop_stream_all()

		self._refresh_global_controls()

	def _start_stream_all(self) -> bool:
		if not self.connected:
			self._set_status("Connect at least one TinZr first")
			return False

		# Send CMD_START to all connected clients
		for did in list(self.connected):
			client = self.clients.get(did)
			if not client or not client.is_connected:
				continue
			if CMD_START:
				async def _send(c=client):
					await c.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_START)
				fut = asyncio.run_coroutine_threadsafe(_send(), self.loop)
				try:
					fut.result(timeout=2)
				except Exception as e:
					self._set_status(f"Start error ({did}): {e}")
					return False

		self.streaming = True
		self._set_status(f"Streaming started ({len(self.connected)} device(s))")
		return True

	def _stop_stream_all(self):
		if not self.streaming:
			return

		# Stop recording too (if active)
		if self.recording:
			self.on_record_toggled(False)
			self.toggle_record.blockSignals(True)
			self.toggle_record.setChecked(False)
			self.toggle_record.blockSignals(False)

		for did in list(self.connected):
			client = self.clients.get(did)
			if not client or not client.is_connected:
				continue
			if CMD_STOP:
				async def _send(c=client):
					await c.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_STOP)
				fut = asyncio.run_coroutine_threadsafe(_send(), self.loop)
				try:
					fut.result(timeout=2)
				except Exception:
					pass

		self.streaming = False
		self._set_status("Streaming stopped")

	# -------------------------
	# Recording (all devices)
	# -------------------------
	def _on_save_mode_changed(self):
		mode = self.combo_save.currentData()
		self.rec_mode = mode if mode in ("folder", "single") else "folder"

	def on_record_toggled(self, checked: bool):
		if checked:
			ok = self._start_recording_all()
			if not ok:
				self.toggle_record.blockSignals(True)
				self.toggle_record.setChecked(False)
				self.toggle_record.blockSignals(False)
		else:
			self._stop_recording_all()

		self._refresh_global_controls()

	def _start_recording_all(self) -> bool:
		if not self.streaming:
			self._set_status("Start streaming before recording")
			return False
		if not self.connected:
			self._set_status("No connected devices")
			return False
		if self.recording:
			return True

		self.rec_t0 = time.perf_counter()
		self.rec_files = {}
		self.rec_folder = None
		self.rec_single_file = None

		ts = datetime.now().strftime("%Y%m%d_%H%M%S")

		if self.rec_mode == "folder":
			base = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder to Save Recordings")
			if not base:
				return False
			out_dir = os.path.join(base, f"TinZr_Recordings_{ts}")
			try:
				os.makedirs(out_dir, exist_ok=True)
			except Exception as e:
				self._set_status(f"Folder error: {e}")
				return False

			for did in list(self.connected):
				row = self.rows.get(did)
				name = row.alias() if row else did
				safe = "".join([c if c.isalnum() or c in ("-", "_") else "_" for c in name])
				fpath = os.path.join(out_dir, f"{safe}_{did.replace(':','')}.csv")
				try:
					f = open(fpath, "w", buffering=1)
					f.write(f"# TinZr Multi Recording\n")
					f.write(f"# DateTime: {datetime.now().isoformat()}\n")
					f.write(f"# Device: {name}\n")
					f.write(f"# Address: {did}\n")
					f.write(REC_HEADER)
				except Exception as e:
					self._set_status(f"File error ({name}): {e}")
					# close already-opened
					for _f in self.rec_files.values():
						try:
							_f.close()
						except Exception:
							pass
					self.rec_files = {}
					return False
				self.rec_files[did] = f

			self.rec_folder = out_dir
			self.recording = True
			self._set_status(f"Recording → {out_dir}")
			return True

		# single CSV mode
		fname, _ = QtWidgets.QFileDialog.getSaveFileName(
			self,
			"Save Recording (Single CSV)",
			f"TinZr_Multi_{ts}.csv",
			"CSV Files (*.csv);;All Files (*)",
		)
		if not fname:
			return False

		try:
			f = open(fname, "w", buffering=1)
			f.write(f"# TinZr Multi Recording (interleaved)\n")
			f.write(f"# DateTime: {datetime.now().isoformat()}\n")
			f.write("# Devices:\n")
			for did in list(self.connected):
				row = self.rows.get(did)
				name = row.alias() if row else did
				f.write(f"# - {name} ({did})\n")
			f.write(REC_HEADER_SINGLE)
		except Exception as e:
			self._set_status(f"File error: {e}")
			return False

		self.rec_single_file = f
		self.recording = True
		self._set_status(f"Recording → {fname}")
		return True

	def _stop_recording_all(self):
		if not self.recording:
			return
		self.recording = False

		# Close per-device files
		for f in list(self.rec_files.values()):
			try:
				f.close()
			except Exception:
				pass
		self.rec_files = {}

		# Close single file
		if self.rec_single_file is not None:
			try:
				self.rec_single_file.close()
			except Exception:
				pass
		self.rec_single_file = None

		self._set_status("Recording stopped")

	# -------------------------
	# BLE RX (multi)
	# -------------------------
	def _on_rx(self, device_id: str, sender, data: bytes):
		"""
		Runs in BLE thread. Parse frames; update vitals; optionally write to CSVs.
		"""
		buf = self.byte_buf.get(device_id)
		if buf is None:
			self.byte_buf[device_id] = bytearray()
			buf = self.byte_buf[device_id]

		buf.extend(data)
		n_bytes = len(buf)
		n_frames = n_bytes // FRAME_SIZE
		if n_frames <= 0:
			return

		# We write time in seconds since record start (host time)
		now = time.perf_counter()
		t0 = self.rec_t0

		for i in range(n_frames):
			start = i * FRAME_SIZE
			chunk = buf[start:start + FRAME_SIZE]
			try:
				(
					ax_i, ay_i, az_i,
					gx_i, gy_i, gz_i,
					red_i, ir_i,
					hr_i, spo2_i,
					batt_i
				) = FRAME_STRUCT.unpack(chunk)
			except Exception:
				continue

			# update vitals in GUI
			self.vitals_update.emit(device_id, int(hr_i), int(spo2_i), int(batt_i))

			# recording (raw as received)
			if self.recording and t0 is not None:
				t_s = now - t0

				ax = ax_i * ACC_SCALE
				ay = ay_i * ACC_SCALE
				az = az_i * ACC_SCALE
				gx = gx_i * GYR_SCALE
				gy = gy_i * GYR_SCALE
				gz = gz_i * GYR_SCALE
				red = float(red_i)
				ir  = float(ir_i)

				if self.rec_mode == "folder":
					f = self.rec_files.get(device_id)
					if f is not None:
						try:
							f.write(f"{t_s:.6f},{ax:.6f},{ay:.6f},{az:.6f},{gx:.6f},{gy:.6f},{gz:.6f},{red:.6f},{ir:.6f},{hr_i:.2f},{spo2_i:.2f},{batt_i:.2f}\n")
						except Exception:
							pass
				else:
					f = self.rec_single_file
					if f is not None:
						row = self.rows.get(device_id)
						name = row.alias() if row else device_id
						try:
							f.write(f"{t_s:.6f},{name},{ax:.6f},{ay:.6f},{az:.6f},{gx:.6f},{gy:.6f},{gz:.6f},{red:.6f},{ir:.6f},{hr_i:.2f},{spo2_i:.2f},{batt_i:.2f}\n")
						except Exception:
							pass

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

	# -------------------------
	# Cleanup
	# -------------------------
	def closeEvent(self, event):
		# Stop recording/streaming gracefully
		if self.recording:
			self._stop_recording_all()
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
