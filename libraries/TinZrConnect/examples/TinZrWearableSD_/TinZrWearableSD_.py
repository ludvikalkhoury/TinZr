"""
TinZr Wearable SD logger GUI for the start-only BLE firmware.

Workflow:
1. Keep the TinZr devices as close as possible to the host computer.
2. Pair the TinZr devices with the host, then scan/connect from this GUI.
3. Start recording from the GUI or with the TinZr side button.
4. The GUI sends one PC timestamp for later synchronization before the start command.
5. After a GUI start, the device reports battery level, shuts down BLE, and logs locally to SD.
6. Stop recording only with the TinZr side button.
"""

import os
import sys
import asyncio
import threading
from datetime import datetime
import zlib
import time

from PyQt5 import QtCore, QtGui, QtWidgets
from bleak import BleakScanner, BleakClient

# IMPORTANT on Windows
os.environ["BLEAK_BACKEND"] = "dotnet"

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

TINZR_BLE_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
TINZR_BLE_RX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # PC -> device (WRITE)
TINZR_BLE_TX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"  # device -> PC (NOTIFY)

DEVICE_PREFIX = "TinZr"

CMD_START = b"S"
CMD_BATT  = b"BAT"
CMD_TIME_PREFIX = "T:"
CMD_DEVICE_PREFIX = "D:"

CMD_SD_LS   = b"LS"
CMD_SD_GETP = "GET:"
CMD_SD_ACKP = "ACK:"
CMD_SD_NAKP = "NAK:"

BATT_POLL_MS = 10 * 60 * 1000

STATUS_FIXED_CHARS = 45

__VERSION__ = "V1.0.0"

# =========================
# DEBUG OPTIONS
# =========================
# IMPORTANT: printing every notify / ack will make Qt look "frozen".
DEBUG_PRINT_ALL_NOTIFIES = False
DEBUG_DUMP_SERVICES_ON_CONNECT = True


class BatteryWidgetClickable(BatteryWidget):
	clicked = QtCore.pyqtSignal()

	def mousePressEvent(self, event):
		if event.button() == QtCore.Qt.LeftButton:
			self.clicked.emit()
		super().mousePressEvent(event)


class DeviceRow(QtWidgets.QFrame):
	connect_toggled = QtCore.pyqtSignal(str, bool)
	remove_clicked = QtCore.pyqtSignal(str)
	battery_clicked = QtCore.pyqtSignal(str)

	def __init__(self, addr: str, ble_name: str, alias: str):
		super().__init__()
		self.addr = addr
		self.ble_name = ble_name or "TinZr"
		self.alias = alias or self.ble_name

		self.setObjectName("card")
		lay = QtWidgets.QHBoxLayout(self)
		lay.setContentsMargins(10, 8, 10, 8)
		lay.setSpacing(10)

		left = QtWidgets.QVBoxLayout()
		left.setSpacing(2)

		self.lbl_alias = QtWidgets.QLabel(self.alias)
		self.lbl_alias.setStyleSheet("font-size: 10pt; font-weight: 600; color: #E3F2FD;")
		self.lbl_addr = QtWidgets.QLabel(f"{self.ble_name}  [{self.addr}]")
		self.lbl_addr.setStyleSheet("font-family: monospace; font-size: 8pt; color: #A8B3CF;")

		left.addWidget(self.lbl_alias)
		left.addWidget(self.lbl_addr)
		lay.addLayout(left, 1)

		self.battery = BatteryWidgetClickable()
		self.battery.setToolTip("Click to refresh battery")
		self.battery.clicked.connect(lambda: self.battery_clicked.emit(self.addr))
		lay.addWidget(self.battery, 0, QtCore.Qt.AlignVCenter)

		lbl_connect = QtWidgets.QLabel("Connect")
		self.toggle_connect = ToggleSwitch()
		self.toggle_connect._thumb_radius = 10
		self.toggle_connect._track_radius = 10
		self.toggle_connect._margin = 3
		self.toggle_connect._width = 45
		self.toggle_connect._height = 25
		self.toggle_connect.setFixedSize(self.toggle_connect._width, self.toggle_connect._height)
		self.toggle_connect.update()

		self.toggle_connect.setChecked(False)
		self.toggle_connect.toggled.connect(lambda checked: self.connect_toggled.emit(self.addr, checked))

		lay.addWidget(lbl_connect, 0, QtCore.Qt.AlignVCenter)
		lay.addWidget(self.toggle_connect, 0, QtCore.Qt.AlignVCenter)

		self.btn_remove = QtWidgets.QPushButton("✕")
		self.btn_remove.setFixedSize(22, 22)
		self.btn_remove.setToolTip("Remove device")
		self.btn_remove.setStyleSheet(
			"QPushButton{font-weight:700; border-radius:8px; padding:0px; }"
			"QPushButton:hover{background: rgba(255,255,255,0.08);}"
		)
		self.btn_remove.clicked.connect(lambda: self.remove_clicked.emit(self.addr))
		lay.addWidget(self.btn_remove, 0, QtCore.Qt.AlignVCenter)

	@QtCore.pyqtSlot(bool)
	def set_connected(self, connected: bool):
		self.toggle_connect.blockSignals(True)
		self.toggle_connect.setChecked(bool(connected))
		self.toggle_connect.blockSignals(False)

	@QtCore.pyqtSlot(bool)
	def set_connect_enabled(self, enabled: bool):
		self.toggle_connect.setEnabled(bool(enabled))

	@QtCore.pyqtSlot(int)
	def set_battery(self, pct: int):
		try:
			self.battery.setLevel(int(pct))
		except Exception:
			pass

	@QtCore.pyqtSlot(str)
	def set_alias(self, alias: str):
		alias = (alias or "").strip()
		if not alias:
			alias = self.ble_name
		self.alias = alias
		self.lbl_alias.setText(self.alias)


class SDRetrieveDialog(QtWidgets.QDialog):
	sig_log = QtCore.pyqtSignal(str)
	sig_progress = QtCore.pyqtSignal(int, str)
	sig_replace_tail = QtCore.pyqtSignal(str)
	sig_tail_2lines = QtCore.pyqtSignal(str, str)

	def __init__(self, parent, get_connected_devices_cb, refresh_cb, download_cb):
		super().__init__(parent)
		self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
		self.setWindowTitle("Retrieve from SD")
		self.setModal(True)
		self.resize(560, 420)
		self.setFixedSize(self.size())

		self._get_connected_devices = get_connected_devices_cb
		self._refresh_cb = refresh_cb
		self._download_cb = download_cb

		# stable tail region marker (character position)
		self._tail_start_pos = None
		self._last_tail_filename = ""
		self._tail_frozen = False

		lay = QtWidgets.QVBoxLayout(self)
		lay.setContentsMargins(14, 14, 14, 14)
		lay.setSpacing(10)

		top = QtWidgets.QHBoxLayout()
		top.setSpacing(8)
		top.addWidget(QtWidgets.QLabel("TinZr:"))

		self.combo = QtWidgets.QComboBox()
		top.addWidget(self.combo, 1)

		self.btn_refresh = QtWidgets.QPushButton("Refresh")
		self.btn_refresh.clicked.connect(self._on_refresh_clicked)
		top.addWidget(self.btn_refresh)

		lay.addLayout(top)

		self.list_files = QtWidgets.QListWidget()
		self.list_files.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
		lay.addWidget(self.list_files, 1)

		bottom = QtWidgets.QHBoxLayout()
		bottom.setSpacing(8)

		self.btn_select_all = QtWidgets.QPushButton("Select all")
		self.btn_select_all.clicked.connect(lambda: self._set_all(True))
		bottom.addWidget(self.btn_select_all)

		self.btn_clear = QtWidgets.QPushButton("Clear")
		self.btn_clear.clicked.connect(lambda: self._set_all(False))
		bottom.addWidget(self.btn_clear)

		bottom.addStretch(1)

		# Green bar = overall file-count progress (NOT bytes)
		self.progress = QtWidgets.QProgressBar()
		self.progress.setRange(0, 100)
		self.progress.setValue(0)
		self.progress.setFixedHeight(15)
		self.progress.setStyleSheet("""
			QProgressBar {
				border: 1px solid rgba(255,255,255,0.25);
				border-radius: 4px;
				background: rgba(255,255,255,0.08);
				text-align: center;
			}
			QProgressBar::chunk {
				background-color: #4CAF50;
				border-radius: 4px;
			}
		""")
		bottom.addWidget(self.progress, 2)

		self.btn_download = QtWidgets.QPushButton("Download selected → PC")
		self.btn_download.clicked.connect(self._on_download_clicked)
		bottom.addWidget(self.btn_download)

		lay.addLayout(bottom)

		self.txt = QtWidgets.QPlainTextEdit()
		self.txt.setReadOnly(True)
		self.txt.setFixedHeight(110)
		self.txt.setStyleSheet("font-family: monospace; font-size: 9pt; color: #B8C3E0;")
		self.txt.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
		lay.addWidget(self.txt)

		self.sig_log.connect(self._ui_append_log)
		self.sig_progress.connect(self._ui_set_progress)
		self.sig_tail_2lines.connect(self._queue_tail_2lines)

		self._populate_devices()
		
		self._tail_block1 = None  # QTextBlock
		self._tail_block_no = None
		self._pending_tail = None  # (line1, line2)

		self._tail_timer = QtCore.QTimer(self)
		self._tail_timer.setInterval(1000)  # 1 Hz UI paint
		self._tail_timer.timeout.connect(self._flush_tail_pending)
		self._tail_timer.start()

				
		
	@QtCore.pyqtSlot(str, str)
	def _queue_tail_2lines(self, line1: str, line2: str):
		# Called via signal (thread-safe). Just store latest.
		self._pending_tail = (line1, line2)

	def _flush_tail_pending(self):
		if self._tail_frozen:
			return
		if not self._pending_tail:
			return
		line1, line2 = self._pending_tail
		self._pending_tail = None
		# Now do the actual overwrite (your existing method)
		self._ui_set_tail_2lines(line1, line2)

	
	def _ensure_2line_tail(self):
		doc = self.txt.document()

		# If we already have a block number, verify it's still valid and still has a next block
		if self._tail_block_no is not None:
			b1 = doc.findBlockByNumber(int(self._tail_block_no))
			if b1.isValid() and b1.next().isValid():
				return

		# Create two placeholder lines once, at the END
		self.txt.appendPlainText("")  # filename line
		self.txt.appendPlainText("")  # progress line

		# After appending, cache the filename block number (2nd-to-last)
		b2 = doc.lastBlock()
		b1 = b2.previous()
		self._tail_block_no = b1.blockNumber()



	def _is_at_bottom(self) -> bool:
		sb = self.txt.verticalScrollBar()
		return sb.value() >= (sb.maximum() - 2)

	def _restore_scroll(self, was_at_bottom: bool, old_value: int):
		sb = self.txt.verticalScrollBar()
		if was_at_bottom:
			sb.setValue(sb.maximum())
		else:
			sb.setValue(old_value)

	def _ensure_progress_tail(self):
		if self._tail_start_pos is not None:
			return
		self.txt.appendPlainText("")  # separator
		doc = self.txt.document()
		self._tail_start_pos = doc.characterCount() - 1

	@QtCore.pyqtSlot(str, str)
	def _ui_set_tail_2lines(self, line1: str, line2: str):
		if self._tail_frozen:
			return

		self._ensure_2line_tail()

		sb = self.txt.verticalScrollBar()
		was_at_bottom = self._is_at_bottom()
		old_value = sb.value()

		doc = self.txt.document()
		b1 = doc.findBlockByNumber(int(self._tail_block_no))
		if (not b1.isValid()) or (not b1.next().isValid()):
			self._tail_block_no = None
			self._ensure_2line_tail()
			b1 = doc.findBlockByNumber(int(self._tail_block_no))

		b2 = b1.next()

		cur = QtGui.QTextCursor(doc)
		cur.setPosition(b1.position())
		cur.setPosition(b2.position() + b2.length() - 1, QtGui.QTextCursor.KeepAnchor)
		cur.removeSelectedText()
		cur.insertText(f"{line1}\n{line2}")

		self._restore_scroll(was_at_bottom, old_value)




	def log_tqdm_2line(self, filename: str, line2: str):
		self._last_tail_filename = filename or ""
		self.sig_tail_2lines.emit(filename, line2)

	def showEvent(self, event):
		super().showEvent(event)
		self._populate_devices()

	@QtCore.pyqtSlot(str)
	def _ui_append_log(self, s: str):
		sb = self.txt.verticalScrollBar()
		was_at_bottom = self._is_at_bottom()
		old_value = sb.value()
		self.txt.appendPlainText(str(s))
		self._restore_scroll(was_at_bottom, old_value)

	@QtCore.pyqtSlot(int, str)
	def _ui_set_progress(self, pct: int, msg: str):
		try:
			self.progress.setValue(max(0, min(100, int(pct))))
		except Exception:
			pass



	def _populate_devices(self):
		self.combo.clear()
		devs = self._get_connected_devices()
		for addr, label in devs:
			self.combo.addItem(label, addr)
		has = (self.combo.count() > 0)
		self.btn_refresh.setEnabled(has)
		self.btn_download.setEnabled(has)

	def log(self, s: str):
		self.sig_log.emit(str(s))

	def _set_progress(self, pct: int, msg: str = ""):
		self.sig_progress.emit(int(pct), str(msg or ""))

	def _set_all(self, checked: bool):
		for i in range(self.list_files.count()):
			item = self.list_files.item(i)
			item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)

	def _selected_names(self):
		names = []
		for i in range(self.list_files.count()):
			item = self.list_files.item(i)
			if item.checkState() == QtCore.Qt.Checked:
				names.append(item.data(QtCore.Qt.UserRole))
		return names

	def _on_refresh_clicked(self):
		addr = self.combo.currentData()
		if not addr:
			return

		self.btn_refresh.setEnabled(False)
		self.btn_download.setEnabled(False)
		self.progress.setValue(0)
		self.list_files.clear()
		self.log("Listing files...")

		async def _go():
			return await self._refresh_cb(addr)

		def _done(fut):
			self.btn_refresh.setEnabled(True)
			self.btn_download.setEnabled(True)
			self.list_files.clear()
			try:
				files = fut.result()
			except Exception as e:
				self.log(f"LS failed: {e}")
				return

			if not files:
				self.log("No files.")
				return

			self.log(f"Found {len(files)} file(s).")
			for name, size in files:
				it = QtWidgets.QListWidgetItem(f"{name}    ({size} bytes)")
				it.setData(QtCore.Qt.UserRole, name)
				it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
				it.setCheckState(QtCore.Qt.Unchecked)
				self.list_files.addItem(it)

		fut = self.parent()._run_coro(_go())

		self._poll_timer = QtCore.QTimer(self)
		self._poll_timer.setInterval(50)

		def _check():
			if fut.done():
				self._poll_timer.stop()
				self._poll_timer.deleteLater()
				_done(fut)

		self._poll_timer.timeout.connect(_check)
		self._poll_timer.start()

	def _on_download_clicked(self):
		addr = self.combo.currentData()
		names = self._selected_names()
		if not addr or not names:
			self.log("Select at least one file.")
			return

		dest_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Select destination folder")
		if not dest_dir:
			return

		self.btn_refresh.setEnabled(False)
		self.btn_download.setEnabled(False)
		self.progress.setValue(0)
		self._tail_frozen = False

		def progress_cb(pct: int, msg: str = ""):
			# pct here is overall progress by file-count
			self._set_progress(pct, "")

		async def _go():
			await self._download_cb(addr, names, dest_dir, progress_cb)

		def _done(fut):
			self.btn_refresh.setEnabled(True)
			self.btn_download.setEnabled(True)
			try:
				fut.result()
				self.progress.setValue(100)

				fn = self._last_tail_filename or (names[0] if names else "file")
				self._tail_frozen = True
				self.sig_replace_tail.emit(f"{fn}\nDone.")
			except Exception as e:
				fn = self._last_tail_filename or (names[0] if names else "file")
				self._tail_frozen = True
				self.sig_replace_tail.emit(f"{fn}\nDownload failed: {e}")

		fut = self.parent()._run_coro(_go())

		self._poll_timer_dl = QtCore.QTimer(self)
		self._poll_timer_dl.setInterval(50)

		def _check_dl():
			if fut.done():
				print("DL FUT DONE EARLY?", fut.done(), "exception:", fut.exception())
				self._poll_timer_dl.stop()
				self._poll_timer_dl.deleteLater()
				_done(fut)

		self._poll_timer_dl.timeout.connect(_check_dl)
		self._poll_timer_dl.start()


class TinZrWearableSD(QtWidgets.QWidget):
	scan_finished = QtCore.pyqtSignal(object, object)

	def __init__(self):
		super().__init__()

		self.setWindowTitle("TinZr Wearable SD Logging")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))

		self.setFixedSize(600, 550)
		self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)

		apply_tinzr_theme(self)

		self._loop = None
		self._loop_thread = None

		self._scanner = None
		self._scan_in_progress = False

		self._scan_devices = []
		self.devices = {}
		self._logging_armed = False
		self._expect_disconnect_after_start = set()

		self._sd_ls_buffers = {}
		self._sd_ls_futures = {}
		self._sd_xfer = {}

		self._build_ui()
		self._start_async_loop_thread()

		self.batt_timer = QtCore.QTimer(self)
		self.batt_timer.timeout.connect(self._poll_battery_all)
		self.batt_timer.start(BATT_POLL_MS)

		self.scan_finished.connect(self._on_scan_finished)

	def _fit_status(self, s: str, width: int = STATUS_FIXED_CHARS) -> str:
		s = (s or "").replace("\n", " ").replace("\r", " ")
		if len(s) <= width:
			return s.ljust(width)
		if width <= 1:
			return s[:width]
		return s[:width-1] + "…"

	def _resolve_future_threadsafe(self, fut, value=None, exc: Exception = None):
		if fut is None:
			return
		try:
			if exc is not None:
				self._loop.call_soon_threadsafe(lambda: (not fut.done()) and fut.set_exception(exc))
			else:
				self._loop.call_soon_threadsafe(lambda: (not fut.done()) and fut.set_result(value))
		except Exception:
			pass

	def _invoke(self, obj, method: str, *args):
		qargs = []
		for a in args:
			if isinstance(a, bool):
				qargs.append(QtCore.Q_ARG(bool, a))
			elif isinstance(a, int):
				qargs.append(QtCore.Q_ARG(int, a))
			else:
				qargs.append(QtCore.Q_ARG(str, str(a)))

		QtCore.QMetaObject.invokeMethod(
			obj,
			method,
			QtCore.Qt.QueuedConnection,
			*qargs,
		)

	@QtCore.pyqtSlot(str, bool)
	def _ui_set_device_connected_flag(self, addr: str, connected: bool):
		info = self.devices.get(addr)
		if not info:
			return
		info["connected"] = bool(connected)

	@QtCore.pyqtSlot(str)
	def _ui_set_status(self, text: str):
		fixed = self._fit_status(text)
		self.label_status.setText(f"Status: {fixed}")

	@QtCore.pyqtSlot(bool, bool)
	def _ui_set_record_toggle(self, checked: bool, enabled: bool):
		self.btn_start_recording.setEnabled(bool(enabled) and not bool(checked))
		self.btn_start_recording.setText("Recording..." if checked else "Start Recording")

	@QtCore.pyqtSlot(bool)
	def _ui_enable_participant_controls(self, enabled: bool):
		self.btn_set_participant.setEnabled(enabled)
		self.edit_participant.setEnabled(enabled)

	@QtCore.pyqtSlot(bool)
	def _ui_enable_sd_retrieve(self, enabled: bool):
		self.btn_retrieve_sd.setEnabled(bool(enabled))
	
	@QtCore.pyqtSlot()
	def _ui_force_logging_off(self):
		# GUI-thread: reflect "logging is OFF"
		self._stop_logging_ui_state()

	
	
	
	def _set_status(self, text: str):
		self._invoke(self, "_ui_set_status", str(text))

	def _log(self, msg: str):
		self._set_status(msg)
	
	async def _handle_ble_disconnect(self, addr: str, why: str = "lost"):
		alias = (self.devices.get(addr) or {}).get("alias", addr)
		expected_after_start = addr in self._expect_disconnect_after_start

		info = self.devices.get(addr)
		if info:
			info["client"] = None
			info["connected"] = False
			row = info.get("row")
			self._invoke(self, "_ui_set_device_connected_flag", addr, False)
			if row:
				self._invoke(row, "set_connected", False)
				self._invoke(row, "set_connect_enabled", True)

		if expected_after_start:
			self._expect_disconnect_after_start.discard(addr)
			self._logging_armed = True
			self._invoke(self, "_ui_set_record_toggle", True, False)
			self._invoke(self, "_ui_enable_participant_controls", False)
			self._invoke(self, "_ui_enable_sd_retrieve", False)
			self._log(f"Recording started on {alias}. BLE disconnected as expected. Stop with the TinZr side button.")
			return

		if not self._any_connected():
			if self._logging_armed:
				self._logging_armed = False
				self._invoke(self, "_ui_force_logging_off")
			self._invoke(self, "_ui_enable_sd_retrieve", False)
			self._log(f"Disconnected ({why}): {alias}. No devices remain connected.")
			return

		self._log(f"Disconnected ({why}): {alias}. Remaining devices stay active.")


	# =========================
	# CRC32 file helper (disk verification)
	# =========================
	def _crc32_file(self, path: str) -> int:
		crc = 0
		with open(path, "rb") as f:
			for chunk in iter(lambda: f.read(64 * 1024), b""):
				crc = zlib.crc32(chunk, crc)
		return crc & 0xFFFFFFFF

	# =========================
	# UI
	# =========================
	def _build_ui(self):
		main_layout = QtWidgets.QVBoxLayout(self)
		main_layout.setContentsMargins(14, 14, 14, 14)
		main_layout.setSpacing(12)

		header = QtWidgets.QFrame()
		header.setObjectName("card")
		h = QtWidgets.QHBoxLayout(header)
		h.setContentsMargins(12, 10, 12, 10)
		h.setSpacing(10)

		title_box = QtWidgets.QVBoxLayout()
		title_box.setSpacing(2)

		title = QtWidgets.QLabel("TinZr Wearable SD")
		title.setStyleSheet("font-size: 16pt; font-weight: 600; color: #E3F2FD;")
		sub = QtWidgets.QLabel("Multi-device - autonomous local-time SD logging")
		sub.setStyleSheet("font-size: 9pt; color: #A8B3CF;")

		title_box.addWidget(title)
		title_box.addWidget(sub)
		h.addLayout(title_box, 1)
		main_layout.addWidget(header)

		self.tabs = QtWidgets.QTabWidget()
		self.tab_logger = QtWidgets.QWidget()
		logger_layout = QtWidgets.QVBoxLayout(self.tab_logger)
		logger_layout.setContentsMargins(0, 0, 0, 0)
		logger_layout.setSpacing(12)

		ctrl_widget = QtWidgets.QFrame()
		ctrl_widget.setObjectName("card")
		ctrl_layout = QtWidgets.QGridLayout(ctrl_widget)
		ctrl_layout.setContentsMargins(12, 12, 12, 12)
		ctrl_layout.setHorizontalSpacing(10)
		ctrl_layout.setVerticalSpacing(10)

		row = 0

		self.btn_scan = QtWidgets.QPushButton("Scan")
		self.btn_scan.clicked.connect(self.on_scan_clicked)

		self.spinner = Spinner(radius=8, line_width=2, parent=self.btn_scan)
		self.spinner.raise_()
		self.btn_scan.installEventFilter(self)
		self._center_spinner_on_button()

		self.combo_devices = QtWidgets.QComboBox()

		self.btn_add = QtWidgets.QPushButton("+")
		self.btn_add.setFixedSize(44, 34)
		self.btn_add.setCursor(QtCore.Qt.PointingHandCursor)
		self.btn_add.setToolTip("Add selected TinZr")
		self.btn_add.clicked.connect(self.on_add_clicked)
		self.btn_add.setStyleSheet("""
			QPushButton {
				font-size: 15pt;
				font-weight: 900;
				color: #E3F2FD;
				background: rgba(255,255,255,0.06);
				border: none;
				border-radius: 10px;
				padding: 0px;
			}
			QPushButton:hover { background: rgba(255,255,255,0.10); }
			QPushButton:pressed { background: rgba(255,255,255,0.14); }
		""")

		ctrl_layout.addWidget(self.btn_scan, row, 0, 1, 1)
		ctrl_layout.addWidget(self.combo_devices, row, 1, 1, 3)
		ctrl_layout.addWidget(self.btn_add, row, 4, 1, 1)
		row += 1

		lbl_part = QtWidgets.QLabel("Participant")
		self.edit_participant = QtWidgets.QLineEdit()
		self.edit_participant.setPlaceholderText("e.g., test001")
		self.btn_set_participant = QtWidgets.QPushButton("Set")
		self.btn_set_participant.clicked.connect(self.on_set_participant)

		ctrl_layout.addWidget(lbl_part, row, 0)
		ctrl_layout.addWidget(self.edit_participant, row, 1, 1, 3)
		ctrl_layout.addWidget(self.btn_set_participant, row, 4)
		row += 1

		lbl_record = QtWidgets.QLabel("SD Log (All)")
		self.btn_start_recording = QtWidgets.QPushButton("Start Recording")
		self.btn_start_recording.clicked.connect(self.on_start_recording_clicked)

		ctrl_layout.addWidget(lbl_record, row, 0)
		ctrl_layout.addWidget(self.btn_start_recording, row, 1)

		self.btn_retrieve_sd = QtWidgets.QPushButton("Retrieve from SD")
		self.btn_retrieve_sd.clicked.connect(self.on_retrieve_sd_clicked)
		self.btn_retrieve_sd.setEnabled(False)
		self.btn_retrieve_sd.setVisible(False)
		row += 1

		self.label_status = QtWidgets.QLabel("Status: Idle")
		self.label_instructions = QtWidgets.QLabel(
			"Keep TinZrs close to this computer. Pair them first, then connect and start recording. "
			"To stop, press the TinZr side button."
		)
		self.label_instructions.setWordWrap(True)
		self.label_status.setStyleSheet("font-size: 9pt; color: #A8B3CF;")
		self.label_status.setTextFormat(QtCore.Qt.PlainText)
		self.label_instructions.setStyleSheet("font-size: 9pt; color: #A8B3CF;")

		ctrl_layout.addWidget(self.label_status, row, 0, 1, 5)
		row += 1
		ctrl_layout.addWidget(self.label_instructions, row, 0, 1, 5)

		logger_layout.addWidget(ctrl_widget)

		list_card = QtWidgets.QFrame()
		list_card.setObjectName("card")
		list_lay = QtWidgets.QVBoxLayout(list_card)
		list_lay.setContentsMargins(12, 12, 12, 12)
		list_lay.setSpacing(8)

		lbl = QtWidgets.QLabel("Added TinZrs")
		list_lay.addWidget(lbl)

		self.scroll = QtWidgets.QScrollArea()
		self.scroll.setWidgetResizable(True)
		self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
		self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

		self.list_container = QtWidgets.QWidget()
		self.list_vbox = QtWidgets.QVBoxLayout(self.list_container)
		self.list_vbox.setContentsMargins(0, 0, 0, 0)
		self.list_vbox.setSpacing(8)
		self.list_vbox.addStretch(1)

		self.scroll.setWidget(self.list_container)
		list_lay.addWidget(self.scroll)
		logger_layout.addWidget(list_card)

		self.tab_help = QtWidgets.QWidget()
		help_layout = QtWidgets.QVBoxLayout(self.tab_help)
		help_layout.setContentsMargins(12, 12, 12, 12)
		help_layout.setSpacing(10)

		help_text = QtWidgets.QLabel(
			"Operation\n"
			"1. Keep the TinZrs close to this computer.\n"
			"2. Pair them in Logger tab; scan then connect.\n"
			"3. Enter a participant name before starting.\n"
			"4. Press Start Recording to send participant, device name, and PC timestamp.\n"
			"5. BLE turns off during SD logging to avoid timing drift.\n"
			"6. Stop recording only with the TinZr side button.\n\n"
			"LED colors\n"
			"Flashing Red: SD card not detected at the firmware reset time.\n"
			"Red: standby, BLE available.\n"
			"Blinking green/red: GUI connected, not recording.\n"
			"Solid green: recording to SD.\n"
			"Flashing red: startup/error state.\n\n"
			"Battery\n"
			"Battery is requested automatically when a TinZr connects and again at GUI start."
		)
		help_text.setWordWrap(True)
		help_text.setTextFormat(QtCore.Qt.PlainText)
		help_text.setStyleSheet("font-size: 9.5pt; color: #E3F2FD;")
		help_layout.addWidget(help_text)
		help_layout.addStretch(1)

		self.tabs.addTab(self.tab_logger, "Logger")
		self.tabs.addTab(self.tab_help, "Instructions")
		main_layout.addWidget(self.tabs, 1)

		self.btn_start_recording.setEnabled(False)
		self._ui_enable_participant_controls(False)

		footer = QtWidgets.QHBoxLayout()
		footer.addStretch(1)

		self.lbl_version = QtWidgets.QLabel(__VERSION__)
		self.lbl_version.setStyleSheet("font-size: 8pt; color: #A8B3CF;")
		footer.addWidget(self.lbl_version)
		main_layout.addLayout(footer)

	def eventFilter(self, obj, event):
		if obj == self.btn_scan and event.type() == QtCore.QEvent.Resize:
			self._center_spinner_on_button()
		return super().eventFilter(obj, event)

	def _center_spinner_on_button(self):
		btn = self.btn_scan
		if btn is None:
			return
		x = (btn.width() - self.spinner.width()) // 2
		y = (btn.height() - self.spinner.height()) // 2
		self.spinner.move(x, y)

	# =========================
	# Async loop thread
	# =========================
	def _start_async_loop_thread(self):
		self._loop = asyncio.new_event_loop()

		def runner():
			asyncio.set_event_loop(self._loop)
			self._loop.run_forever()

		self._loop_thread = threading.Thread(target=runner, daemon=True)
		self._loop_thread.start()

	def _run_coro(self, coro, done_cb=None):
		fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
		if done_cb is not None:
			def _cb(_f):
				def _call():
					try:
						done_cb(_f)
					except Exception as e:
						print("DONE_CB ERROR:", e)
				QtCore.QTimer.singleShot(0, _call)
			fut.add_done_callback(_cb)
		return fut

	# =========================
	# Scan/Add/Remove/Connect
	# =========================
	def on_scan_clicked(self):
		self.spinner.start()
		self.btn_scan.setEnabled(False)
		self.combo_devices.clear()
		self._scan_devices = []
		self._log("Scanning for TinZr devices...")
		self._run_coro(self._scan_ble())

	async def _stop_scan_async(self):
		try:
			if self._scanner is not None:
				try:
					await self._scanner.stop()
				except Exception:
					pass
		finally:
			self._scanner = None
			self._scan_in_progress = False

	async def _scan_ble(self):
		await self._stop_scan_async()
		self._scan_in_progress = True

		try:
			self._scanner = BleakScanner()
			await self._scanner.start()
			await asyncio.sleep(3.0)
			await self._scanner.stop()

			devices = list(self._scanner.discovered_devices)
			tinzr = [d for d in devices if (d.name or "").startswith(DEVICE_PREFIX)]
			self.scan_finished.emit(tinzr, None)

		except Exception as e:
			self.scan_finished.emit([], e)

		finally:
			self._scanner = None
			self._scan_in_progress = False

	def _on_scan_finished(self, devices, error):
		self.spinner.stop()
		self.btn_scan.setEnabled(True)

		if error:
			self._log(f"Scan error: {error}")
			return
		if not devices:
			self._log("No TinZr devices found.")
			return

		self._scan_devices = devices
		for d in devices:
			label = f"{d.name}  [{d.address}]"
			self.combo_devices.addItem(label, d.address)

		self._log(f"Found {len(devices)} device(s).")

	def on_add_clicked(self):
		if self.combo_devices.count() == 0:
			self._log("Scan first, then add.")
			return

		addr = self.combo_devices.currentData()
		ble_name = None
		for d in self._scan_devices:
			if d.address == addr:
				ble_name = d.name
				break
		if not ble_name:
			ble_name = "TinZr"

		if addr in self.devices:
			self._log("Device already added.")
			return

		dlg = QtWidgets.QInputDialog(self)
		dlg.setWindowTitle("Alias")
		dlg.setLabelText("Alias name for this TinZr:")
		dlg.setTextValue(ble_name)
		dlg.setWindowFlags(dlg.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
		dlg.setStyleSheet(self.styleSheet())

		ok = dlg.exec_()
		if not ok:
			return
		alias = (dlg.textValue() or ble_name).strip() or ble_name

		row = DeviceRow(addr=addr, ble_name=ble_name, alias=alias)
		row.connect_toggled.connect(self.on_device_connect_toggled)
		row.remove_clicked.connect(self.on_device_remove_clicked)
		row.battery_clicked.connect(self.on_device_battery_clicked)

		self.list_vbox.insertWidget(self.list_vbox.count() - 1, row)

		self.devices[addr] = {
			"ble_name": ble_name,
			"alias": alias,
			"client": None,
			"connected": False,
			"row": row,
		}

		self._log(f"Added: {alias}")

	def on_device_remove_clicked(self, addr: str):
		if addr not in self.devices:
			return
		self._run_coro(self._remove_device(addr))

	async def _remove_device(self, addr: str):
		try:
			await self._disconnect_device(addr, reason="remove")
		except Exception:
			pass

		info = self.devices.get(addr)
		if not info:
			return

		row = info.get("row")
		if row:
			self._invoke(row, "set_connected", False)
			row.setParent(None)
			row.deleteLater()

		self.devices.pop(addr, None)

		if not self._any_connected():
			self._stop_logging_ui_state()
			self._invoke(self, "_ui_enable_sd_retrieve", False)

		self._log("Device removed.")

	def on_device_battery_clicked(self, addr: str):
		if addr not in self.devices:
			return
		self._run_coro(self._send_cmd_to_device(addr, CMD_BATT, response=False))

	def on_device_connect_toggled(self, addr: str, checked: bool):
		if addr not in self.devices:
			return

		row: DeviceRow = self.devices[addr]["row"]
		self._invoke(row, "set_connect_enabled", False)

		if checked:
			self._run_coro(self._connect_device(addr))
		else:
			self._run_coro(self._disconnect_device(addr))

	async def _connect_device(self, addr: str):
		info = self.devices.get(addr)
		if not info:
			return
		row: DeviceRow = info["row"]

		try:
			self._log(f"Connecting to {addr} ...")
			def _on_disconnect_cb(_client, _addr=addr):
				# This callback is invoked by Bleak; jump back into our asyncio thread safely.
				try:
					self._run_coro(self._handle_ble_disconnect(_addr, why="BLE dropped"))
				except Exception:
					pass
			client = BleakClient(addr, disconnected_callback=_on_disconnect_cb)

			await client.connect()

			if DEBUG_DUMP_SERVICES_ON_CONNECT:
				try:
					svcs = client.services
					print("=== SERVICES / CHARS ===")
					for s in svcs:
						print("SERVICE", s.uuid)
						for c in s.characteristics:
							print("  CHAR", c.uuid, "props=", c.properties)
					print("========================")
				except Exception as e:
					print("Service dump failed:", e)

			def _notify(_char, data: bytearray, _addr=addr):
				self._on_notify(_addr, data)

			await client.start_notify(TINZR_BLE_TX_CHAR_UUID, _notify)

			info["client"] = client
			self._invoke(self, "_ui_set_device_connected_flag", addr, True)

			self._invoke(row, "set_connected", True)
			self._invoke(row, "set_connect_enabled", True)

			self._log(f"Connected: {info['alias']}")

			if self._any_connected():
				self._invoke(self, "_ui_set_record_toggle", self._logging_armed, True)
				self._invoke(self, "_ui_enable_participant_controls", True)
				self._invoke(self, "_ui_enable_sd_retrieve", True)

			await self._send_cmd_to_device(addr, CMD_BATT, response=False)

			if self._logging_armed:
				self._logging_armed = False
				self._invoke(self, "_ui_set_record_toggle", False, True)
				self._invoke(self, "_ui_enable_participant_controls", True)
				self._log(f"Reconnected: {info['alias']}. Recording state cleared for SD retrieval or a new run.")

		except Exception as e:
			self._log(f"Connect failed ({addr}): {e}")
			info["client"] = None
			info["connected"] = False
			self._invoke(row, "set_connected", False)
			self._invoke(row, "set_connect_enabled", True)

	async def _disconnect_device(self, addr: str, reason: str = "", stop_logging_on_disconnect: bool = False):
		info = self.devices.get(addr)
		if not info:
			return

		row: DeviceRow = info["row"]
		client: BleakClient = info.get("client")

		try:
			# This firmware is start-only over BLE. Recording stops only from the TinZr side button.

			if client:
				try:
					await client.stop_notify(TINZR_BLE_TX_CHAR_UUID)
				except Exception:
					pass
				try:
					await client.disconnect()
				except Exception:
					pass
		finally:
			info["client"] = None
			self._invoke(self, "_ui_set_device_connected_flag", addr, False)
			self._invoke(row, "set_connected", False)
			self._invoke(row, "set_connect_enabled", True)

			if not self._any_connected():
				self._stop_logging_ui_state()
				self._invoke(self, "_ui_enable_sd_retrieve", False)

			if reason != "remove":
				self._log(f"Disconnected: {info['alias']}")

	def _any_connected(self) -> bool:
		for info in self.devices.values():
			client = info.get("client")
			if (client is not None) and getattr(client, "is_connected", False):
				return True
			if bool(info.get("connected")):
				return True
		return False

	# =========================
	# Notify handler (SD transfer fixes + UI throttling)
	# =========================
	def _on_notify(self, addr: str, data: bytearray):
		raw = bytes(data)

		info = self.devices.get(addr)
		if not info:
			return
		row: DeviceRow = info["row"]

		# Binary SD transfer packet
		if len(raw) >= 9 and raw[0] == ord("D"):
			try:
				seq = int.from_bytes(raw[1:3], "little")
				plen = int.from_bytes(raw[3:5], "little")
				crc = int.from_bytes(raw[5:9], "little") & 0xFFFFFFFF
				payload = raw[9:9 + plen]
				if len(payload) != plen:
					return
			except Exception:
				return

			st = self._sd_xfer.get(addr)
			if not st or st.get("mode") != "receiving":
				return

			expected = int(st.get("seq_expected", 0))
			if seq != expected:
				self._run_coro(self._send_cmd_to_device(
					addr, f"{CMD_SD_NAKP}{expected}".encode("utf-8"), response=False
				))
				return

			pc_crc = zlib.crc32(payload) & 0xFFFFFFFF
			if pc_crc != crc:
				self._run_coro(self._send_cmd_to_device(
					addr, f"{CMD_SD_NAKP}{seq}".encode("utf-8"), response=False
				))
				return

			# WRITE
			try:
				if st.get("fh") is None:
					mode = "ab" if int(st.get("bytes", 0)) > 0 else "wb"
					st["fh"] = open(st["dest_path"], mode)
				st["fh"].write(payload)
			except Exception:
				fut = st.get("future")
				if fut and not fut.done():
					self._resolve_future_threadsafe(fut, exc=RuntimeError("Failed writing to destination file"))
				return

			# update counters
			st["crc_acc"] = zlib.crc32(payload, st.get("crc_acc", 0)) & 0xFFFFFFFF
			st["bytes"] = int(st.get("bytes", 0)) + plen
			st["seq_expected"] = expected + 1
			st["last_rx_monotonic"] = time.monotonic()

			size = int(st.get("size") or 0)
			done = int(st.get("bytes") or 0)

			# UI tail = per-file bytes progress (THROTTLED)
			if size > 0:
				pct = int(100.0 * done / float(size))

				now_m = time.monotonic()
				last_ui_t = float(st.get("last_ui_monotonic") or 0.0)
				last_ui_pct = int(st.get("last_ui_pct") if st.get("last_ui_pct") is not None else -1)

				# at most 10 Hz OR if pct changes
				if (now_m - last_ui_t) >= 0.10 or pct != last_ui_pct:
					st["last_ui_monotonic"] = now_m
					st["last_ui_pct"] = pct

					i = int(st.get("file_i") or 1)
					tot = int(st.get("file_total") or 1)
					name = st.get("name") or "file"
					width = 28
					filled = int(round(width * (pct / 100.0)))
					bar = "█" * filled + "░" * (width - filled)

					dlg = getattr(self, "_sd_dialog_ref", None)
					if dlg is not None:
						try:
							dlg.log_tqdm_2line(
								filename=name,
								line2=f"[{i}/{tot}] [{bar}] {pct:3d}%  ({done}/{size} bytes)"
							)
						except Exception:
							pass

			# ACK (must not block)
			self._run_coro(self._send_cmd_to_device(
				addr, f"{CMD_SD_ACKP}{seq}".encode("utf-8"), response=False
			))

			# Finalize if we have all bytes (even if END was early)
			if size > 0 and done >= size:
				fut = st.get("future")
				if fut and not fut.done() and not st.get("_finalize_attempted", False):
					st["_finalize_attempted"] = True
					try:
						if st.get("fh"):
							st["fh"].flush()
							st["fh"].close()
							st["fh"] = None
					except Exception:
						pass

					try:
						exp_crc = int(st.get("crc32") or 0) & 0xFFFFFFFF
						disk_crc = self._crc32_file(st["dest_path"])
						if disk_crc == exp_crc:
							self._resolve_future_threadsafe(fut, value=True)
						else:
							self._resolve_future_threadsafe(
								fut,
								exc=RuntimeError(
									f"VERIFY FAILED: disk_crc=0x{disk_crc:08X} exp=0x{exp_crc:08X}"
								)
							)
					except Exception as e:
						self._resolve_future_threadsafe(fut, exc=RuntimeError(f"Finalize verify failed: {e}"))

			# Throttle STATUS updates too (or Qt will backlog on big files)
			if size > 0:
				pct2 = int(100.0 * st["bytes"] / float(size))
				now_m = time.monotonic()

				last_status_t = float(st.get("last_status_monotonic") or 0.0)
				last_status_pct = int(st.get("last_status_pct") if st.get("last_status_pct") is not None else -1)

				# update at most 4 Hz OR when percent changes
				if (now_m - last_status_t) >= 1.0 or pct2 != last_status_pct:
					st["last_status_monotonic"] = now_m
					st["last_status_pct"] = pct2
					self._invoke(
						self,
						"_ui_set_status",
						f"Retrieving {st.get('name')} ... {pct2}% ({st['bytes']}/{size})"
					)

			return

		# Text notifications
		try:
			text = raw.decode("utf-8", errors="ignore").rstrip("\r\n")
		except Exception:
			return
		if not text:
			return

		if DEBUG_PRINT_ALL_NOTIFIES:
			print(f"[NOTIFY {addr}] {text}")

		# SD list protocol
		if text.startswith("LS:BEGIN"):
			self._sd_ls_buffers[addr] = []
			return

		if text.startswith("LS:END") or text.startswith("LS: sent files ="):
			fut = self._sd_ls_futures.get(addr)
			lines = list(self._sd_ls_buffers.get(addr, []))
			self._resolve_future_threadsafe(fut, value=lines)
			return

		if text.startswith("LS:ERR|"):
			if addr not in self._sd_ls_buffers:
				self._sd_ls_buffers[addr] = []
			self._sd_ls_buffers[addr].append(text.replace("LS:", "", 1))
			return

		if text.startswith("LS:"):
			if addr not in self._sd_ls_buffers:
				self._sd_ls_buffers[addr] = []
			line = text.split(":", 1)[1].strip()
			if line:
				self._sd_ls_buffers[addr].append(line)
			return

		# GET begin/end
		if text.startswith("GET:BEGIN"):
			parts = text.split("|")
			if len(parts) >= 4:
				st = self._sd_xfer.get(addr)
				if st and st.get("name") == parts[1]:
					try:
						st["size"] = int(parts[2])
						st["crc32"] = int(parts[3], 16) & 0xFFFFFFFF
						st["mode"] = "receiving"
						st["seq_expected"] = 0
						st["crc_acc"] = 0
						st["bytes"] = 0
						st["_finalize_attempted"] = False
						st["last_rx_monotonic"] = time.monotonic()
						st["got_end"] = False
						st["end_crc"] = None
						# reset UI throttle for a clean display
						st["last_ui_monotonic"] = 0.0
						st["last_ui_pct"] = -1
					except Exception:
						pass
			return

		# IMPORTANT: GET:END can arrive EARLY. Mark it; do not finalize unless bytes>=size.
		if text.startswith("GET:END"):
			st = self._sd_xfer.get(addr)
			if not st:
				return

			try:
				parts = text.split("|")
				if len(parts) >= 2:
					st["end_crc"] = int(parts[1], 16) & 0xFFFFFFFF
			except Exception:
				st["end_crc"] = None
			st["got_end"] = True

			# If already complete, finalize here as well
			try:
				size = int(st.get("size") or 0)
				done = int(st.get("bytes") or 0)
			except Exception:
				size = 0
				done = 0

			if size > 0 and done >= size:
				try:
					if st.get("fh"):
						st["fh"].flush()
						st["fh"].close()
						st["fh"] = None
				except Exception:
					pass

				ok = True
				try:
					exp_crc = int(st.get("crc32") or 0) & 0xFFFFFFFF
					disk_crc = self._crc32_file(st["dest_path"])
					if disk_crc != exp_crc:
						ok = False
				except Exception:
					ok = False

				fut = st.get("future")
				if fut:
					if ok:
						self._resolve_future_threadsafe(fut, value=True)
					else:
						self._resolve_future_threadsafe(
							fut,
							exc=RuntimeError(
								f"VERIFY FAILED: bytes={done}/{size} exp=0x{int(st.get('crc32') or 0) & 0xFFFFFFFF:08X}"
							)
						)
			return

		if text.startswith("BAT:"):
			try:
				rawp = text.split(":", 1)[1].strip().replace("%", "").strip()
				if not rawp:
					return
				pct = int(rawp.split()[0])
				if pct < 0 or pct > 100:
					return
				self._invoke(row, "set_battery", pct)
			except Exception:
				pass
			return

		self._invoke(self, "_ui_set_status", text)

	# =========================
	# Participant / Logging
	# =========================
	def _sanitize_subject(self, subject: str) -> str:
		return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in subject)

	def on_set_participant(self):
		name = self.edit_participant.text().strip()
		if not name:
			self._log("Participant name is empty.")
			return
		if not self._any_connected():
			self._log("No connected TinZr.")
			return
		self._run_coro(self._send_participant_all(name))

	async def _send_participant_all(self, name: str):
		safe = self._sanitize_subject(name)
		cmd = f"P:{safe}".encode("utf-8")
		await self._send_cmd_all(cmd)
		self._log(f"Participant set: {safe}")

	def on_start_recording_clicked(self):
		if not self._any_connected():
			self._log("No connected TinZr.")
			self._invoke(self, "_ui_set_record_toggle", bool(self._logging_armed), False)
			return

		if self._logging_armed:
			self._log("Recording already started. Stop with the TinZr side button.")
			self._invoke(self, "_ui_set_record_toggle", True, False)
			return

		self._run_coro(self._start_logging_all())

	async def _arm_device_for_logging(self, addr: str):
		info = self.devices.get(addr)
		if not info or not info.get("connected"):
			return

		self._expect_disconnect_after_start.add(addr)
		subject = self._sanitize_subject(self.edit_participant.text().strip())
		device_alias = info.get("alias") or info.get("ble_name") or "TinZr"
		device_name = self._sanitize_subject(device_alias)
		now_local = datetime.now().astimezone()
		pc_timestamp = now_local.strftime("%Y-%m-%dT%H-%M-%S-") + f"{now_local.microsecond:06d}"
		await self._send_cmd_to_device(addr, f"P:{subject}".encode("utf-8"), response=False)
		await asyncio.sleep(0.05)
		await self._send_cmd_to_device(addr, f"{CMD_DEVICE_PREFIX}{device_name}".encode("utf-8"), response=False)
		await asyncio.sleep(0.05)
		await self._send_cmd_to_device(addr, f"{CMD_TIME_PREFIX}{pc_timestamp}".encode("utf-8"), response=False)
		await asyncio.sleep(0.05)
		await self._send_cmd_to_device(addr, CMD_START, response=False)

	async def _start_logging_all(self):
		try:
			name = self.edit_participant.text().strip()
			if not name:
				self._log("Set participant before logging.")
				self._invoke(self, "_ui_set_record_toggle", False, True)
				return

			tasks = [
				self._arm_device_for_logging(addr)
				for addr, info in list(self.devices.items())
				if info.get("connected")
			]
			if not tasks:
				self._log("No connected devices available for logging.")
				self._invoke(self, "_ui_set_record_toggle", False, self._any_connected())
				return
			if tasks:
				await asyncio.gather(*tasks)

			self._logging_armed = True
			self._invoke(self, "_ui_set_record_toggle", True, False)
			self._invoke(self, "_ui_enable_participant_controls", False)
			self._invoke(self, "_ui_enable_sd_retrieve", False)
			self._log("Start command sent. BLE will disconnect and logging will continue on SD. Stop with the TinZr side button.")

		except Exception as e:
			self._log(f"Start logging failed: {e}")
			self._logging_armed = False
			self._invoke(self, "_ui_set_record_toggle", False, True)

	async def _stop_logging_all(self):
		self._log("Stop recording with the TinZr side button.")

	def _poll_battery_all(self):
		if not self._any_connected():
			return
		self._run_coro(self._send_cmd_all(CMD_BATT))

	def _stop_logging_ui_state(self):
		self._logging_armed = False
		self._invoke(self, "_ui_set_record_toggle", False, False)
		self._invoke(self, "_ui_enable_participant_controls", False)

	# =========================
	# SD Retrieve
	# =========================
	def on_retrieve_sd_clicked(self):
		dlg = SDRetrieveDialog(
			self,
			self._get_connected_devices_for_sd,
			self._sd_refresh_files,
			self._sd_download_files,
		)
		self._sd_dialog_ref = dlg
		dlg.setStyleSheet(self.styleSheet())
		dlg.exec_()
		self._sd_dialog_ref = None

	def _get_connected_devices_for_sd(self):
		devs = []
		for addr, info in self.devices.items():
			client = info.get("client")
			is_conn = bool(info.get("connected")) or (client is not None and getattr(client, "is_connected", False))
			if is_conn:
				label = f"{info.get('alias', info.get('ble_name','TinZr'))}  [{addr}]"
				devs.append((addr, label))
		return devs

	async def _sd_refresh_files(self, addr: str):
		self._sd_ls_futures.pop(addr, None)

		loop = asyncio.get_running_loop()
		fut = loop.create_future()
		self._sd_ls_futures[addr] = fut
		self._sd_ls_buffers.pop(addr, None)

		await self._send_cmd_to_device(addr, CMD_SD_LS, response=False)

		try:
			lines = await asyncio.wait_for(fut, timeout=8.0)
		except asyncio.TimeoutError:
			lines = self._sd_ls_buffers.get(addr, [])

		out = []
		for ln in lines:
			if ln.startswith("ERR|"):
				continue
			if "|" in ln:
				parts = ln.split("|", 1)
			elif "," in ln:
				parts = ln.split(",", 1)
			else:
				continue
			name = parts[0].strip()
			try:
				size = int(parts[1].strip())
			except Exception:
				size = 0
			if name:
				out.append((name, size))
		return out

	async def _sd_download_files(self, addr: str, names, dest_dir: str, progress_cb):
		total = len(names)
		for i, name in enumerate(names):
			prefix = f"[{i+1}/{total}] "
			dest_path = os.path.abspath(os.path.join(dest_dir, name))

			# ALWAYS START CLEAN
			try:
				if os.path.exists(dest_path):
					os.remove(dest_path)
			except Exception:
				pass

			loop = asyncio.get_running_loop()
			file_future = loop.create_future()

			self._sd_xfer[addr] = {
				"mode": "waiting_begin",
				"name": name,
				"dest_path": dest_path,
				"fh": None,
				"size": None,
				"crc32": None,
				"crc_acc": 0,
				"bytes": 0,
				"seq_expected": 0,
				"future": file_future,
				"file_i": i + 1,
				"file_total": total,
				"_finalize_attempted": False,
				"last_rx_monotonic": 0.0,
				"got_end": False,
				"end_crc": None,
				# UI throttle
				"last_ui_monotonic": 0.0,
				"last_ui_pct": -1,
			}

			# Green bar = file-count progress
			progress_cb(int(100.0 * i / max(1, total)), f"{prefix}Requesting {name} ...")
			await self._send_cmd_to_device(addr, f"{CMD_SD_GETP}{name}".encode("utf-8"), response=False)

			# Watchdog:
			#  - NAK expected seq when stalled / END came early
			#  - If repeated NAKs do not resume data, re-GET clean (firmware likely stopped after early END)
			start = time.monotonic()
			last_poke = 0.0
			poke_count = 0
			reget_count = 0
			last_reget = 0.0

			while True:
				if file_future.done():
					file_future.result()
					break

				if (time.monotonic() - start) > 5*24*60*60: # stop after 5 days 
					st = self._sd_xfer.get(addr) or {}
					print("TIMEOUT:",
						  "pct=", (100.0 * int(st.get("bytes") or 0) / max(1, int(st.get("size") or 1))),
						  "done=", st.get("bytes"),
						  "size=", st.get("size"),
						  "got_end=", st.get("got_end"),
						  "mode=", st.get("mode"),
						  "seq_expected=", st.get("seq_expected"),
						  "since_rx=", time.monotonic() - float(st.get("last_rx_monotonic") or 0.0))
					raise RuntimeError(f"Timeout downloading {name} (no completion)")


				st = self._sd_xfer.get(addr) or {}
				last_rx = float(st.get("last_rx_monotonic") or 0.0)
				since_rx = time.monotonic() - last_rx if last_rx > 0 else 1e9

				got_end = bool(st.get("got_end"))
				try:
					size = int(st.get("size") or 0)
					done = int(st.get("bytes") or 0)
				except Exception:
					size = 0
					done = 0

				need_more = (size > 0 and done < size)

				# don't spam recovery before BEGIN
				if st.get("mode") == "waiting_begin":
					await asyncio.sleep(0.05)
					continue

				# 1) NAK poke
				if need_more and (since_rx > 2.0 or got_end) and (time.monotonic() - last_poke) > 1.0:
					exp = int(st.get("seq_expected") or 0)
					poke_count += 1
					try:
						await self._send_cmd_to_device(addr, f"{CMD_SD_NAKP}{exp}".encode("utf-8"), response=False)
						last_poke = time.monotonic()
					except Exception:
						pass

				# 2) If repeated NAKs don't resume, re-GET clean
				if need_more and (poke_count >= 4) and (since_rx > 4.0) and (time.monotonic() - last_reget) > 5.0:
					reget_count += 1
					last_reget = time.monotonic()

					print(f"[PC -> {addr}] REGET:{name}  (attempt {reget_count})  done={done}/{size}")

					try:
						if st.get("fh"):
							try:
								st["fh"].flush()
								st["fh"].close()
							except Exception:
								pass
							st["fh"] = None

						try:
							if os.path.exists(dest_path):
								os.remove(dest_path)
						except Exception:
							pass

						st["mode"] = "waiting_begin"
						st["size"] = None
						st["crc32"] = None
						st["crc_acc"] = 0
						st["bytes"] = 0
						st["seq_expected"] = 0
						st["last_rx_monotonic"] = 0.0
						st["_finalize_attempted"] = False
						st["got_end"] = False
						st["end_crc"] = None
						st["last_ui_monotonic"] = 0.0
						st["last_ui_pct"] = -1

						poke_count = 0

						await self._send_cmd_to_device(addr, f"{CMD_SD_GETP}{name}".encode("utf-8"), response=False)

					except Exception:
						pass

				if reget_count >= 3:
					raise RuntimeError(f"Device keeps ending early / stalling; failed after {reget_count} re-GET attempts for {name}")

				await asyncio.sleep(0.05)

			# close handle if still open
			st = self._sd_xfer.get(addr)
			if st and st.get("fh"):
				try:
					st["fh"].close()
				except Exception:
					pass
				st["fh"] = None

			# Completed file -> update overall progress
			st = self._sd_xfer.get(addr, {})
			progress_cb(
				int(100.0 * (i+1) / max(1, total)),
				f"{prefix}Verified OK: {name}  ({st.get('bytes',0)} bytes)"
			)

	# =========================
	# BLE send helpers
	# =========================
	async def _send_cmd_to_device(self, addr: str, payload: bytes, response: bool = False):
		info = self.devices.get(addr)
		if not info:
			raise RuntimeError("Unknown device")

		client: BleakClient = info.get("client")
		if (not client) or (not getattr(client, "is_connected", False)):
			# Auto-handle the drop without stopping the remaining session.
			await self._handle_ble_disconnect(addr, why="send failed (not connected)")
			raise RuntimeError("BLE client not connected")

		try:
			await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, payload, response=bool(response))
		except Exception as e:
			# If the exception smells like a dead link, treat it the same way.
			msg = str(e).lower()
			if ("not connected" in msg) or ("disconnected" in msg) or ("device not found" in msg):
				await self._handle_ble_disconnect(addr, why=f"send exception: {e}")
			raise


	async def _send_cmd_all(self, payload: bytes):
		tasks = []
		for addr, info in list(self.devices.items()):
			client: BleakClient = info.get("client")
			if not client or not client.is_connected:
				continue
			tasks.append(self._send_cmd_to_device(addr, payload, response=False))

		if not tasks:
			return

		results = await asyncio.gather(*tasks, return_exceptions=True)
		for result in results:
			if isinstance(result, Exception):
				print(f"BROADCAST SEND ERROR: {result}")

	# =========================
	# Close
	# =========================
	def closeEvent(self, event):
		try:
			if self._loop and self._loop.is_running():
				futures = []

				try:
					fut_scan = self._run_coro(self._stop_scan_async())
					futures.append(fut_scan)
				except Exception:
					pass

				for addr in list(self.devices.keys()):
					try:
						fut = self._run_coro(
							self._disconnect_device(
								addr,
								reason="close",
								stop_logging_on_disconnect=bool(self._logging_armed),
							)
						)
						futures.append(fut)
					except Exception:
						pass

				for fut in futures:
					try:
						fut.result(timeout=3.0)
					except Exception:
						pass

				try:
					self._loop.call_soon_threadsafe(self._loop.stop)
				except Exception:
					pass

		except Exception as e:
			print("CloseEvent error:", e)

		finally:
			super().closeEvent(event)


def main():
	app = QtWidgets.QApplication(sys.argv)
	w = TinZrWearableSD()
	w.show()
	sys.exit(app.exec_())


if __name__ == "__main__":
	if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
	if hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

	main()
