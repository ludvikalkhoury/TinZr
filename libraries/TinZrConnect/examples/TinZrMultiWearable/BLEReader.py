import os
import sys
import time
import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from bleak import BleakScanner
from PyQt5 import QtCore, QtWidgets, QtGui
from PyQt5.QtGui import QTextCursor

# =========================
# TinZr RSSI Logger (SINGLE DEVICE)
#
# - NO CONNECT / NO STREAM
# - Add device -> ONLY adds to UI (no auto logging).
# - RSSI updates via continuous adv scanner (WinRT-safe).
# - Live RSSI Feed is UNDER the device card (full width).
# - Start/Stop Logging buttons control CSV logging.
#
# UI FIXES:
# - Single device slot (no left list / no splitter).
# - Feed append DOES NOT yank you to bottom if you scrolled up.
#
# NEW:
# - Persistent "Label" field that logs as an extra CSV column on every row
#   until you change it (e.g., distance from PC).
# =========================

os.environ["BLEAK_BACKEND"] = "dotnet"  # important on Windows

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
	sys.path.insert(0, PARENT_DIR)

from GUIsHelper import (
	Spinner,
	apply_tinzr_theme,
)

DEVICE_PREFIX = "TinZr"

# UI/Logging cadence
RSSI_UI_MS     = 250      # update RSSI bars/labels
LOG_EVERY_MS   = 1000     # write to CSV at 1 Hz when logging is ON
RSSI_STALE_SEC = 3.0      # if last adv older than this, treat as missing

# Live list behavior
LIVE_APPEND_EVERY_MS = 500   # how often to append entries to the live feed
LIVE_MAX_LINES       = 2000  # prevent infinite growth


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


def _csv_escape(s: str) -> str:
	"""
	Minimal CSV-safe escaping:
	- wrap in quotes if it contains comma/quote/newline
	- double quotes inside quoted fields
	"""
	s = "" if s is None else str(s)
	if any(ch in s for ch in [",", '"', "\n", "\r"]):
		s = s.replace('"', '""')
		return f'"{s}"'
	return s


# =========================
# Elided QLabel (prevents long address from blowing up width)
# =========================
class ElideLabel(QtWidgets.QLabel):
	def __init__(self, text="", parent=None):
		super().__init__(text, parent)
		self._full_text = text
		self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)

	def setFullText(self, text: str):
		self._full_text = text or ""
		self._update_elide()

	def resizeEvent(self, event):
		super().resizeEvent(event)
		self._update_elide()

	def _update_elide(self):
		fm = QtGui.QFontMetrics(self.font())
		el = fm.elidedText(self._full_text, QtCore.Qt.ElideMiddle, max(10, self.width() - 6))
		super().setText(el)

	def fullText(self):
		return self._full_text


# =========================
# RSSI Bars widget
# =========================
class RSSIBars(QtWidgets.QWidget):
	def __init__(self, parent=None):
		super().__init__(parent)
		self._rssi = None
		self.setFixedSize(60, 18)

	def setRssi(self, rssi_dbm):
		self._rssi = rssi_dbm if rssi_dbm is None else int(rssi_dbm)
		self.update()

	def _bars_from_rssi(self, rssi):
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
# Device row widget (NO CONNECT)
# =========================
class TinZrDeviceRow(QtWidgets.QFrame):
	request_remove  = QtCore.pyqtSignal(str)

	def __init__(self, device_id: str, info: DeviceInfo, alias: str = ""):
		super().__init__()
		self.device_id = device_id
		self.info = info

		self.setFrameShape(QtWidgets.QFrame.StyledPanel)
		self.setStyleSheet("QFrame{border:1px solid rgba(255,255,255,40); border-radius:10px;}")
		self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

		lay = QtWidgets.QGridLayout(self)
		lay.setContentsMargins(10, 8, 10, 8)
		lay.setHorizontalSpacing(10)
		lay.setVerticalSpacing(4)

		self.ed_alias = QtWidgets.QLineEdit()
		self.ed_alias.setText(alias or "")
		self.ed_alias.setPlaceholderText("Alias (optional)")
		self.ed_alias.setMaximumWidth(260)
		self.ed_alias.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)

		self.lbl_addr = ElideLabel("")
		self.lbl_addr.setFullText(info.address)
		self.lbl_addr.setStyleSheet("color: rgba(255,255,255,150);")
		self.lbl_addr.setMinimumWidth(340)

		self.rssi_bars = RSSIBars()
		self.rssi_bars.setToolTip("Signal strength (RSSI)")

		self.lbl_rssi = QtWidgets.QLabel("RSSI: -- dBm")
		self.lbl_rssi.setStyleSheet("color: rgba(180,220,255,200);")
		self.lbl_rssi.setMinimumWidth(120)

		self.btn_remove = QtWidgets.QPushButton("✕")
		self.btn_remove.setFixedWidth(52)
		self.btn_remove.clicked.connect(lambda: self.request_remove.emit(self.device_id))
		self.btn_remove.setToolTip("Remove device")

		lay.addWidget(QtWidgets.QLabel("Name"), 0, 0, alignment=QtCore.Qt.AlignRight)
		lay.addWidget(self.ed_alias, 0, 1, 1, 2)

		lay.addWidget(QtWidgets.QLabel("Addr"), 1, 0, alignment=QtCore.Qt.AlignRight)
		lay.addWidget(self.lbl_addr, 1, 1, 1, 2)

		hrow = QtWidgets.QHBoxLayout()
		hrow.setContentsMargins(0, 0, 0, 0)
		hrow.setSpacing(8)
		hrow.addWidget(self.rssi_bars, 0, QtCore.Qt.AlignVCenter)
		hrow.addWidget(self.lbl_rssi, 0, QtCore.Qt.AlignRight)
		wr = QtWidgets.QWidget()
		wr.setLayout(hrow)
		wr.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
		lay.addWidget(wr, 0, 3, 2, 1, alignment=QtCore.Qt.AlignRight)

		lay.addWidget(self.btn_remove, 0, 4, 2, 1, alignment=QtCore.Qt.AlignVCenter)

		lay.setColumnStretch(0, 0)
		lay.setColumnStretch(1, 1)
		lay.setColumnStretch(2, 0)
		lay.setColumnStretch(3, 0)
		lay.setColumnStretch(4, 0)

	def alias(self) -> str:
		return self.ed_alias.text().strip() or self.info.name or self.info.address

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


# =========================
# Main Viewer (single device + feed underneath)
# =========================
class TinZrRSSILoggerSingle(QtWidgets.QWidget):
	scan_finished = QtCore.pyqtSignal(object, object)  # (devices, error)
	status_update = QtCore.pyqtSignal(str)

	def __init__(self):
		super().__init__()

		self.setWindowTitle("TinZr RSSI Logger")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))
		self.setFixedSize(1000, 740)
		self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)
		apply_tinzr_theme(self)

		# ===== BLE loop thread =====
		self.loop = asyncio.new_event_loop()
		self.ble_thread = threading.Thread(target=self._run_ble_loop, daemon=True)
		self.ble_thread.start()

		# ===== State =====
		self.discovered: list[DeviceInfo] = []
		self.state_lock = threading.Lock()

		# Single device slot:
		self.current_did = None
		self.current_row = None

		# Advertisement RSSI cache: addr -> (rssi_dbm, t_perf)
		self.adv_rssi = {}
		self._adv_scanner = None

		# ===== Logging =====
		self.log_path = None
		self.log_file = None
		self.logging = False
		self._t0_perf = None

		# ===== Persistent user label (logged every row until changed) =====
		self.current_label = ""

		# Live feed bookkeeping
		self._last_live_append = 0.0

		# ===== Signals =====
		self.scan_finished.connect(self._handle_scan_result)
		self.status_update.connect(self._set_status)

		# ===== UI =====
		main_layout = QtWidgets.QVBoxLayout(self)
		main_layout.setContentsMargins(16, 12, 16, 12)
		main_layout.setSpacing(10)

		# Header
		header = QtWidgets.QWidget()
		h_lay = QtWidgets.QHBoxLayout(header)
		h_lay.setContentsMargins(0, 0, 0, 0)
		h_lay.setSpacing(10)

		title = QtWidgets.QLabel("TinZr RSSI Logger")
		title.setStyleSheet("font-size: 16pt; font-weight: 600; color: #E3F2FD;")
		h_lay.addWidget(title)
		h_lay.addStretch(1)

		self.lbl_log = QtWidgets.QLabel("Log: (not started)")
		self.lbl_log.setStyleSheet("color: rgba(200,240,255,200);")
		h_lay.addWidget(self.lbl_log)

		main_layout.addWidget(header)

		# Controls
		ctrl = QtWidgets.QWidget()
		grid = QtWidgets.QGridLayout(ctrl)
		grid.setContentsMargins(0, 0, 0, 0)
		grid.setHorizontalSpacing(14)
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
		self.combo_found.setMinimumWidth(320)
		grid.addWidget(self.combo_found, r, 1, 1, 3)

		self.ed_new_alias = QtWidgets.QLineEdit()
		self.ed_new_alias.setPlaceholderText("Alias for selected device (optional)")
		grid.addWidget(self.ed_new_alias, r, 4, 1, 2)

		self.btn_add = QtWidgets.QPushButton("Add")
		self.btn_add.clicked.connect(self.on_add_clicked)
		grid.addWidget(self.btn_add, r, 6)

		# Logging controls row
		r += 1
		self.btn_choose_log = QtWidgets.QPushButton("Choose Log File")
		self.btn_choose_log.clicked.connect(self._choose_log_file)
		grid.addWidget(self.btn_choose_log, r, 0)

		self.btn_start_log = QtWidgets.QPushButton("Start Logging")
		self.btn_start_log.clicked.connect(self._start_logging_clicked)
		grid.addWidget(self.btn_start_log, r, 1)

		self.btn_stop_log = QtWidgets.QPushButton("Stop Logging")
		self.btn_stop_log.clicked.connect(self._stop_logging)
		self.btn_stop_log.setEnabled(False)
		grid.addWidget(self.btn_stop_log, r, 2)

		# Persistent label UI
		self.ed_label = QtWidgets.QLineEdit()
		self.ed_label.setPlaceholderText("Label to log (e.g., distance: 2m)")
		self.ed_label.setToolTip("This label is written on every CSV row until you change it.")
		self.ed_label.returnPressed.connect(self._apply_label_from_ui)
		grid.addWidget(self.ed_label, r, 3)

		self.btn_set_label = QtWidgets.QPushButton("Set Label")
		self.btn_set_label.setToolTip("Apply the label (sticky until changed).")
		self.btn_set_label.clicked.connect(self._apply_label_from_ui)
		grid.addWidget(self.btn_set_label, r, 4)

		self.lbl_rate = QtWidgets.QLabel(f"Log rate: {1000.0/LOG_EVERY_MS:.2f} Hz")
		self.lbl_rate.setStyleSheet("color: rgba(200,240,255,200);")
		grid.addWidget(self.lbl_rate, r, 5, 1, 2, alignment=QtCore.Qt.AlignRight)

		main_layout.addWidget(ctrl)

		# =========================
		# Single device area (no scroll)
		# =========================
		self.device_slot = QtWidgets.QFrame()
		self.device_slot.setStyleSheet("QFrame{border:1px solid rgba(255,255,255,22); border-radius:12px;}")
		dv = QtWidgets.QVBoxLayout(self.device_slot)
		dv.setContentsMargins(10, 10, 10, 10)
		dv.setSpacing(8)

		self.device_title = QtWidgets.QLabel("Device")
		self.device_title.setStyleSheet("font-size: 12pt; font-weight: 600; color: rgba(227,242,253,230);")
		dv.addWidget(self.device_title)

		self.device_holder = QtWidgets.QWidget()
		self.device_holder_lay = QtWidgets.QVBoxLayout(self.device_holder)
		self.device_holder_lay.setContentsMargins(0, 0, 0, 0)
		self.device_holder_lay.setSpacing(8)

		# Placeholder text when no device
		self.device_empty = QtWidgets.QLabel("No device added yet.")
		self.device_empty.setStyleSheet("color: rgba(200,240,255,120); padding: 8px;")
		self.device_holder_lay.addWidget(self.device_empty)

		dv.addWidget(self.device_holder)
		main_layout.addWidget(self.device_slot, stretch=0)

		# =========================
		# Live feed UNDER device (full width)
		# =========================
		self.feed_panel = QtWidgets.QFrame()
		self.feed_panel.setStyleSheet("QFrame{border:1px solid rgba(255,255,255,30); border-radius:12px;}")
		rv = QtWidgets.QVBoxLayout(self.feed_panel)
		rv.setContentsMargins(10, 10, 10, 10)
		rv.setSpacing(8)

		lbl = QtWidgets.QLabel("Live RSSI Feed")
		lbl.setStyleSheet("font-size: 12pt; font-weight: 600; color: rgba(227,242,253,230);")
		rv.addWidget(lbl)

		self.live = QtWidgets.QPlainTextEdit()
		self.live.setReadOnly(True)
		self.live.setMaximumBlockCount(LIVE_MAX_LINES)
		self.live.setStyleSheet("""
			QPlainTextEdit {
				background: rgba(2, 8, 23, 200);
				color: rgba(230, 240, 255, 220);
				border: 1px solid rgba(255,255,255,18);
				border-radius: 10px;
				padding: 8px;
				font-family: Consolas, Menlo, monospace;
				font-size: 10pt;
			}
		""")
		rv.addWidget(self.live, stretch=1)

		btns = QtWidgets.QHBoxLayout()
		self.btn_clear_live = QtWidgets.QPushButton("Clear")
		self.btn_clear_live.clicked.connect(self._clear_live)
		btns.addWidget(self.btn_clear_live)
		btns.addStretch(1)
		rv.addLayout(btns)

		main_layout.addWidget(self.feed_panel, stretch=1)

		# --- Live feed: prevent scroll-yank while user is scrolling ---
		self._live_freeze_until = 0.0
		self.live.viewport().installEventFilter(self)
		self.live.verticalScrollBar().sliderPressed.connect(self._live_user_scroll)
		self.live.verticalScrollBar().sliderReleased.connect(self._live_user_scroll)

		# Status
		self.label_status = QtWidgets.QLabel("Status: Idle")
		self.label_status.setObjectName("statusLabel")
		self.label_status.setStyleSheet("color: rgba(255,255,255,170);")
		main_layout.addWidget(self.label_status)

		# Timers
		self.rssi_timer = QtCore.QTimer(self)
		self.rssi_timer.setTimerType(QtCore.Qt.PreciseTimer)
		self.rssi_timer.timeout.connect(self._rssi_tick)
		self.rssi_timer.start(RSSI_UI_MS)

		self.live_timer = QtCore.QTimer(self)
		self.live_timer.setTimerType(QtCore.Qt.PreciseTimer)
		self.live_timer.timeout.connect(self._live_tick)
		self.live_timer.start(LIVE_APPEND_EVERY_MS)

		self.log_timer = QtCore.QTimer(self)
		self.log_timer.setTimerType(QtCore.Qt.PreciseTimer)
		self.log_timer.timeout.connect(self._log_tick)
		self.log_timer.start(LOG_EVERY_MS)

		# Start adv scanner
		self._ensure_adv_scanner_started()

	def _apply_label_from_ui(self):
		self.current_label = (self.ed_label.text() or "").strip()
		if self.current_label:
			self.status_update.emit(f"Label set: {self.current_label}")
		else:
			self.status_update.emit("Label cleared.")

	def _live_user_scroll(self):
		# Freeze auto-follow briefly after any manual scrollbar interaction
		self._live_freeze_until = time.perf_counter() + 0.8

	def eventFilter(self, obj, event):
		# ---- Live feed scroll freeze (only if live exists) ----
		live_exists = hasattr(self, "live") and (self.live is not None)
		if live_exists:
			try:
				if obj is self.live.viewport():
					if event.type() in (QtCore.QEvent.Wheel, QtCore.QEvent.Scroll, QtCore.QEvent.MouseButtonPress):
						self._live_user_scroll()
			except Exception:
				pass

		# ---- Scan button resize: keep spinner centered ----
		if obj is getattr(self, "btn_scan", None) and event.type() == QtCore.QEvent.Resize:
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

	# ===== BLE loop =====
	def _run_ble_loop(self):
		asyncio.set_event_loop(self.loop)
		self.loop.run_forever()

	def _ensure_adv_scanner_started(self):
		if getattr(self, "_adv_scanner", None) is not None:
			return
		try:
			asyncio.run_coroutine_threadsafe(self._start_adv_scanner_async(), self.loop)
		except Exception:
			pass

	async def _start_adv_scanner_async(self):
		if getattr(self, "_adv_scanner", None) is not None:
			return
		try:
			def _cb(device, adv):
				try:
					addr = getattr(device, "address", None)
					if not addr:
						return
					rssi = getattr(adv, "rssi", None)
					if rssi is None:
						return
					self.adv_rssi[addr] = (int(rssi), time.perf_counter())
				except Exception:
					pass

			self._adv_scanner = BleakScanner(detection_callback=_cb)
			await self._adv_scanner.start()
			self.status_update.emit("Adv scanner: ON (RSSI live)")
		except Exception as e:
			self._adv_scanner = None
			self.status_update.emit(f"Adv scanner failed: {e}")

	# ===== Status =====
	def _set_status(self, text: str):
		self.label_status.setText(f"Status: {text}")

	# ===== Scan =====
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
			self.scan_finished.emit(found, None)
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

	# ===== Single device Add / Remove =====
	def on_add_clicked(self):
		info = self.combo_found.currentData()
		if not info:
			return

		did = info.address
		alias = self.ed_new_alias.text().strip() or info.name

		# If same device already added, just update alias
		if self.current_did == did and self.current_row is not None:
			self.current_row.ed_alias.setText(alias)
			self.status_update.emit(f"Updated alias: {self.current_row.alias()}")
			return

		# If a different device is already present, replace it (single-device app)
		if self.current_row is not None:
			self._remove_current_device(silent=True)

		row = TinZrDeviceRow(did, info, alias=alias)
		row.request_remove.connect(self._on_row_remove)

		# Clear placeholder
		self._clear_device_holder()

		self.device_holder_lay.addWidget(row)
		self.current_did = did
		self.current_row = row

		self.status_update.emit(f"Added: {row.alias()} (RSSI only; not logging yet)")

	def _clear_device_holder(self):
		# Remove all widgets from holder layout
		while self.device_holder_lay.count():
			item = self.device_holder_lay.takeAt(0)
			w = item.widget()
			if w is not None:
				w.setParent(None)
				w.deleteLater()

	def _remove_current_device(self, silent: bool = False):
		if self.current_row is not None:
			try:
				self.current_row.setParent(None)
				self.current_row.deleteLater()
			except Exception:
				pass

		self.current_did = None
		self.current_row = None

		# Put placeholder back
		self._clear_device_holder()
		self.device_empty = QtWidgets.QLabel("No device added yet.")
		self.device_empty.setStyleSheet("color: rgba(200,240,255,120); padding: 8px;")
		self.device_holder_lay.addWidget(self.device_empty)

		if not silent:
			self.status_update.emit("Removed device.")

	def _on_row_remove(self, did: str):
		# Single device, remove if it matches current
		if self.current_did == did:
			self._remove_current_device(silent=False)

	# ===== RSSI UI Tick =====
	def _rssi_tick(self):
		# Only update current device
		if self.current_row is None or self.current_did is None:
			return

		now = time.perf_counter()
		pair = self.adv_rssi.get(self.current_did)
		if pair is not None:
			rssi, ts = pair
			if (now - float(ts)) <= float(RSSI_STALE_SEC):
				self.current_row.set_rssi(int(rssi))
				return

		self.current_row.set_rssi(None)

	# ===== Live Feed =====
	def _append_live(self, text: str):
		sb = self.live.verticalScrollBar()
		now = time.perf_counter()

		# Are we currently freezing auto-follow because user scrolled recently?
		frozen = (now < float(getattr(self, "_live_freeze_until", 0.0)))

		# Only follow if user is near bottom AND not actively scrolling
		near_bottom = (sb.value() >= (sb.maximum() - 12))
		should_follow = (near_bottom and (not frozen))

		# Save scroll state BEFORE insert
		old_val = sb.value()

		# Insert at end (more controllable than appendPlainText)
		cursor = self.live.textCursor()
		cursor.movePosition(QTextCursor.End)
		cursor.insertText(text + "\n")

		# Restore scroll unless we decided to follow
		if should_follow:
			sb.setValue(sb.maximum())
		else:
			sb.setValue(old_val)

	def _clear_live(self):
		self.live.clear()

	def _live_tick(self):
		if self.current_row is None or self.current_did is None:
			return

		now = time.perf_counter()
		last = float(self._last_live_append) if self._last_live_append else 0.0
		if (now - last) < (LIVE_APPEND_EVERY_MS / 1000.0) * 0.9:
			return

		wall = datetime.now().strftime("%H:%M:%S.%f")[:-3]
		did = self.current_did
		alias = self.current_row.alias()

		pair = self.adv_rssi.get(did)
		rssi_val = None
		if pair is not None:
			rssi, ts = pair
			if (now - float(ts)) <= float(RSSI_STALE_SEC):
				rssi_val = int(rssi)

		label = (self.current_label or "").strip()
		label_show = f" | Label: {label}" if label else ""

		if rssi_val is None:
			line = f"{wall} | {alias:<18} | {did} | RSSI: --{label_show}"
		else:
			line = f"{wall} | {alias:<18} | {did} | RSSI: {rssi_val:4d} dBm{label_show}"

		self._append_live(line)
		self._last_live_append = now

	# ===== Logging =====
	def _choose_log_file(self) -> bool:
		timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
		default_name = f"TinZrRSSI_{timestamp}.csv"

		start_dir = os.path.dirname(os.path.abspath(__file__))
		fpath, _ = QtWidgets.QFileDialog.getSaveFileName(
			self,
			"Save RSSI log CSV",
			os.path.join(start_dir, default_name),
			"CSV Files (*.csv);;All Files (*)"
		)
		if not fpath:
			return False
		if not fpath.lower().endswith(".csv"):
			fpath += ".csv"

		try:
			if self.log_file is not None:
				self.log_file.close()
		except Exception:
			pass

		try:
			self.log_file = open(fpath, "w", newline="")
			self.log_path = fpath
			self.lbl_log.setText(f"Log: {os.path.basename(fpath)}")
			self.status_update.emit(f"Log file set: {fpath}")
			return True
		except Exception as e:
			self.log_file = None
			self.log_path = None
			self.status_update.emit(f"Failed to open log: {e}")
			return False

	def _start_logging_clicked(self):
		if self.logging:
			return
		if self.current_row is None or self.current_did is None:
			self.status_update.emit("Add a device first.")
			return
		if self.log_file is None:
			if not self._choose_log_file():
				self.status_update.emit("Logging cancelled.")
				return
		self._start_logging()

	def _start_logging(self):
		if self.logging or self.log_file is None:
			return

		self.logging = True
		self.btn_stop_log.setEnabled(True)
		self.btn_start_log.setEnabled(False)
		self._t0_perf = time.perf_counter()

		f = self.log_file
		f.write("# TinZr RSSI Log (advertisement RSSI; no connections)\n")
		f.write(f"# DateTime_start: {datetime.now().isoformat()}\n")
		f.write(f"# Log_period_ms: {int(LOG_EVERY_MS)}\n")
		f.write("# Columns: time_s, wall_time_iso, addr, alias, rssi_dbm, label\n")
		f.write("time_s,wall_time_iso,addr,alias,rssi_dbm,label\n")
		try:
			f.flush()
		except Exception:
			pass

		self.status_update.emit("Logging: ON")

	def _stop_logging(self):
		if not self.logging:
			return

		self.logging = False
		self.btn_stop_log.setEnabled(False)
		self.btn_start_log.setEnabled(True)

		try:
			if self.log_file is not None:
				self.log_file.flush()
				self.log_file.close()
		except Exception:
			pass

		self.status_update.emit(f"Logging: OFF → {self.log_path if self.log_path else '(no file)'}")
		self.log_file = None
		self.log_path = None
		self.lbl_log.setText("Log: (not started)")

	def _log_tick(self):
		if not self.logging or self.log_file is None:
			return
		if self.current_row is None or self.current_did is None:
			return

		f = self.log_file

		now_perf = time.perf_counter()
		t_rel = float(now_perf - float(self._t0_perf)) if self._t0_perf is not None else 0.0
		wall_iso = datetime.now().isoformat(timespec="milliseconds")
		now = time.perf_counter()

		did = self.current_did
		alias = self.current_row.alias()

		pair = self.adv_rssi.get(did)
		rssi_val = np.nan
		if pair is not None:
			rssi, ts = pair
			if (now - float(ts)) <= float(RSSI_STALE_SEC):
				rssi_val = float(rssi)

		rssi_str = "nan" if np.isnan(rssi_val) else f"{int(rssi_val):d}"
		label_str = _csv_escape(self.current_label)

		f.write(f"{t_rel:.3f},{wall_iso},{did},{_safe_name(alias)},{rssi_str},{label_str}\n")

		try:
			f.flush()
		except Exception:
			pass

	# ===== Close =====
	def closeEvent(self, event: QtGui.QCloseEvent):
		try:
			self.status_update.emit("Closing...")

			try:
				self.rssi_timer.stop()
			except Exception:
				pass
			try:
				self.live_timer.stop()
			except Exception:
				pass
			try:
				self.log_timer.stop()
			except Exception:
				pass

			if self.logging:
				self._stop_logging()

			try:
				if self._adv_scanner is not None:
					asyncio.run_coroutine_threadsafe(self._stop_adv_scanner_async(), self.loop)
			except Exception:
				pass

			try:
				if self.loop and self.loop.is_running():
					self.loop.call_soon_threadsafe(self.loop.stop)
			except Exception:
				pass
		except Exception:
			pass

		event.accept()

	async def _stop_adv_scanner_async(self):
		try:
			if self._adv_scanner is not None:
				await self._adv_scanner.stop()
		except Exception:
			pass
		self._adv_scanner = None


def main():
	if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
	if hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

	app = QtWidgets.QApplication(sys.argv)

	w = TinZrRSSILoggerSingle()
	w.show()

	sys.exit(app.exec_())


if __name__ == "__main__":
	main()
