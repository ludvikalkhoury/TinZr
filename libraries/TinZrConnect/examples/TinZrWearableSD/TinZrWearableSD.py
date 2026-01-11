import os
import sys
import asyncio
import time
import threading
from datetime import datetime

from PyQt5 import QtCore, QtGui, QtWidgets
from bleak import BleakScanner, BleakClient

# IMPORTANT on Windows
os.environ["BLEAK_BACKEND"] = "dotnet"

# ===== Make parent "examples" directory importable so we can see GUIsHelper.py =====
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
	sys.path.insert(0, PARENT_DIR)

# ===== Reusable GUI helper pieces (toggle, spinner, battery, theme) =====
# NOTE: This is intentionally the SAME theme/logic used by TinZrWearable.py
from GUIsHelper import (
	ToggleSwitch,
	Spinner,
	BatteryWidget,
	apply_tinzr_theme,
)

# ================== BLE UUIDs & Device Filter ==================
TINZR_BLE_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
TINZR_BLE_RX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # PC -> device (WRITE)
TINZR_BLE_TX_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"  # device -> PC (NOTIFY)

DEVICE_PREFIX = "TinZr"

# ================== Commands (ASCII) ==================
CMD_START = b"S"
CMD_STOP  = b"E"
CMD_BATT  = b"BAT"

CMD_EXP_META_PREFIX = "X:"  # bytes payload: b"X:<subject>|<pc_time>"

HEARTBEAT_PERIOD_S = 5.0

# Battery refresh: every 10 minutes
BATT_POLL_MS = 10 * 60 * 1000


class BatteryWidgetClickable(BatteryWidget):
	clicked = QtCore.pyqtSignal()

	def mousePressEvent(self, event):
		if event.button() == QtCore.Qt.LeftButton:
			self.clicked.emit()
		super().mousePressEvent(event)


class TinZrWearableSD(QtWidgets.QWidget):
	scan_finished = QtCore.pyqtSignal(object, object)  # (devices, error)

	def __init__(self):
		super().__init__()

		self.setWindowTitle("TinZr Wearable SD Logging")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))

		# ---------- Fixed size window (TinZrWearable vibe) ----------
		self.setFixedSize(600, 300)
		self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)

		# ---------- Global style / theme (from helper) ----------
		apply_tinzr_theme(self)

		# ---------- State ----------
		self._loop = None
		self._loop_thread = None
		self._client = None
		self._connected = False
		self._logging_armed = False

		# ---------- Vitals (optional ASCII from firmware) ----------
		self.hr_bpm = None
		self.spo2_pct = None

		# ---------- UI ----------
		self._build_ui()

		# ---------- Async loop thread ----------
		self._start_async_loop_thread()

		# ---------- Timers (created in GUI thread) ----------
		self.batt_timer = QtCore.QTimer(self)
		self.batt_timer.timeout.connect(self._send_battery_query)
		self.batt_timer.start(BATT_POLL_MS)  # poll battery every 10 minutes

		self.heartbeat_timer = QtCore.QTimer(self)
		self.heartbeat_timer.timeout.connect(self._send_heartbeat)

		# ---------- Signals ----------
		self.scan_finished.connect(self._on_scan_finished)

	# ================== UI thread helpers (slots) ==================
	@QtCore.pyqtSlot(str)
	def _ui_set_status(self, text: str):
		self.label_status.setText(f"Status: {text}")

	def _set_status(self, text: str):
		QtCore.QMetaObject.invokeMethod(
			self,
			"_ui_set_status",
			QtCore.Qt.QueuedConnection,
			QtCore.Q_ARG(str, text),
		)

	def _log(self, msg: str):
		self._set_status(msg)

	@QtCore.pyqtSlot(bool, bool)
	def _ui_set_connect_toggle(self, checked: bool, enabled: bool):
		self.toggle_connect.blockSignals(True)
		self.toggle_connect.setChecked(checked)
		self.toggle_connect.blockSignals(False)
		self.toggle_connect.setEnabled(enabled)

	@QtCore.pyqtSlot(bool, bool)
	def _ui_set_record_toggle(self, checked: bool, enabled: bool):
		self.toggle_record.blockSignals(True)
		self.toggle_record.setChecked(checked)
		self.toggle_record.blockSignals(False)
		self.toggle_record.setEnabled(enabled)

	@QtCore.pyqtSlot(bool)
	def _ui_enable_participant_controls(self, enabled: bool):
		self.btn_set_participant.setEnabled(enabled)
		self.edit_participant.setEnabled(enabled)

	@QtCore.pyqtSlot()
	def _gui_start_heartbeat(self):
		self.heartbeat_timer.start(int(HEARTBEAT_PERIOD_S * 1000))
		self._send_heartbeat()  # immediate

	@QtCore.pyqtSlot()
	def _gui_stop_heartbeat(self):
		if self.heartbeat_timer.isActive():
			self.heartbeat_timer.stop()
		self.label_hb.setText("Synchronization trigger: —")

	# ================== UI ==================
	def _build_ui(self):
		main_layout = QtWidgets.QVBoxLayout(self)
		main_layout.setContentsMargins(14, 14, 14, 14)
		main_layout.setSpacing(12)

		# ================== Header ==================
		header = QtWidgets.QFrame()
		header.setObjectName("card")
		h = QtWidgets.QHBoxLayout(header)
		h.setContentsMargins(12, 10, 12, 10)
		h.setSpacing(10)

		title_box = QtWidgets.QVBoxLayout()
		title_box.setSpacing(2)

		title = QtWidgets.QLabel("TinZr Wearable SD")
		title.setStyleSheet("font-size: 16pt; font-weight: 600; color: #E3F2FD;")
		sub = QtWidgets.QLabel("Wearable control • PC-timestamp SD logging")
		sub.setStyleSheet("font-size: 9pt; color: #A8B3CF;")

		title_box.addWidget(title)
		title_box.addWidget(sub)

		h.addLayout(title_box, 1)

		# Clickable battery widget
		self.battery = BatteryWidgetClickable()
		self.battery.setToolTip("Click to refresh battery")
		self.battery.clicked.connect(self.on_battery_clicked)
		h.addWidget(self.battery, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

		main_layout.addWidget(header)

		# ================== Controls Card ==================
		ctrl_widget = QtWidgets.QFrame()
		ctrl_widget.setObjectName("card")
		ctrl_layout = QtWidgets.QGridLayout(ctrl_widget)
		ctrl_layout.setContentsMargins(12, 12, 12, 12)
		ctrl_layout.setHorizontalSpacing(10)
		ctrl_layout.setVerticalSpacing(10)

		row = 0

		# Row 0: Scan + devices
		self.btn_scan = QtWidgets.QPushButton("Scan")
		self.btn_scan.clicked.connect(self.on_scan_clicked)

		self.spinner = Spinner(radius=8, line_width=2, parent=self.btn_scan)
		self.spinner.raise_()
		self.btn_scan.installEventFilter(self)
		self._center_spinner_on_button()

		self.combo_devices = QtWidgets.QComboBox()

		ctrl_layout.addWidget(self.btn_scan, row, 0, 1, 1)
		ctrl_layout.addWidget(self.combo_devices, row, 1, 1, 4)

		row += 1

		# Row 1: Connect + Record
		lbl_connect = QtWidgets.QLabel("Connect")
		lbl_record  = QtWidgets.QLabel("SD Log")

		self.toggle_connect = ToggleSwitch()
		self.toggle_record  = ToggleSwitch()

		self.toggle_connect.setChecked(False)
		self.toggle_record.setChecked(False)

		self.toggle_connect.toggled.connect(self.on_connect_toggled)
		self.toggle_record.toggled.connect(self.on_record_toggled)

		ctrl_layout.addWidget(lbl_connect,          row, 0)
		ctrl_layout.addWidget(self.toggle_connect,  row, 1)
		ctrl_layout.addWidget(lbl_record,           row, 3)
		ctrl_layout.addWidget(self.toggle_record,   row, 4)

		row += 1

		# Row 2: Participant
		lbl_part = QtWidgets.QLabel("Participant")
		self.edit_participant = QtWidgets.QLineEdit()
		self.edit_participant.setPlaceholderText("e.g., Sub001")
		self.btn_set_participant = QtWidgets.QPushButton("Set")
		self.btn_set_participant.clicked.connect(self.on_set_participant)

		ctrl_layout.addWidget(lbl_part,                 row, 0)
		ctrl_layout.addWidget(self.edit_participant,    row, 1, 1, 3)
		ctrl_layout.addWidget(self.btn_set_participant, row, 4)

		row += 1

		# Row 3: Status + Heartbeat
		self.label_status = QtWidgets.QLabel("Status: Idle")
		self.label_status.setStyleSheet("font-family: monospace; font-size: 9pt; color: #A8B3CF;")

		self.label_hb = QtWidgets.QLabel("Synchronization trigger: —")
		self.label_hb.setStyleSheet("font-family: monospace; font-size: 9pt; color: #A8B3CF;")

		ctrl_layout.addWidget(self.label_status, row, 0, 1, 3)
		ctrl_layout.addWidget(self.label_hb,     row, 3, 1, 2)

		row += 1

		# Row 4: Vitals
		self.label_vitals = QtWidgets.QLabel("Vitals: HR -- bpm   SpO₂ -- %")
		self.label_vitals.setStyleSheet("font-family: monospace; font-size: 9pt; color: #A8B3CF;")
		ctrl_layout.addWidget(self.label_vitals, row, 0, 1, 5)

		main_layout.addWidget(ctrl_widget)

		# Disable record & participant until connected
		self.toggle_record.setEnabled(False)
		self._ui_enable_participant_controls(False)

	def on_battery_clicked(self):
		# Manual refresh on click
		self._send_battery_query()

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

	# ================== Vitals UI ==================
	def _update_vitals_label(self):
		hr_txt = f"{int(self.hr_bpm)}" if (self.hr_bpm is not None and self.hr_bpm > 0) else "--"
		spo2_txt = f"{int(self.spo2_pct)}" if (self.spo2_pct is not None and self.spo2_pct > 0) else "--"
		self.label_vitals.setText(f"Vitals: HR {hr_txt} bpm   SpO₂ {spo2_txt} %")

	@QtCore.pyqtSlot(int, int)
	def _set_vitals(self, hr: int, spo2: int):
		self.hr_bpm = hr if hr > 0 else self.hr_bpm
		self.spo2_pct = spo2 if spo2 > 0 else self.spo2_pct
		self._update_vitals_label()

	# ================== Experiment meta (subject + PC time) ==================
	def _sanitize_subject(self, subject: str) -> str:
		return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in subject)

	def _build_experiment_meta_payload(self) -> bytes:
		subject = self._sanitize_subject(self.edit_participant.text().strip())
		
		# Year-Month-Day_Hour-Min-Sec_Millis.Microseconds (MS + 4 micro digits)
		now = datetime.now()
		ms = now.microsecond // 1000
		micro4 = now.microsecond % 10000  # last 4 digits of microseconds
		stamp = f"{now:%Y-%m-%dT%H:%M:%S}:{ms:03d}.{micro4:04d}"

		return f"{CMD_EXP_META_PREFIX} sub-{subject}|{stamp}".encode("utf-8")

	# ================== Async loop thread ==================
	def _start_async_loop_thread(self):
		self._loop = asyncio.new_event_loop()

		def runner():
			asyncio.set_event_loop(self._loop)
			self._loop.run_forever()

		self._loop_thread = threading.Thread(target=runner, daemon=True)
		self._loop_thread.start()

	def _run_coro(self, coro):
		return asyncio.run_coroutine_threadsafe(coro, self._loop)

	# ================== BLE Scanning ==================
	def on_scan_clicked(self):
		self.spinner.start()
		self.btn_scan.setEnabled(False)
		self.combo_devices.clear()
		self._log("Scanning for TinZr devices...")
		self._run_coro(self._scan_ble())

	async def _scan_ble(self):
		try:
			devices = await BleakScanner.discover(timeout=3.0)
			tinzr = [d for d in devices if (d.name or "").startswith(DEVICE_PREFIX)]
			self.scan_finished.emit(tinzr, None)
		except Exception as e:
			self.scan_finished.emit([], e)

	def _on_scan_finished(self, devices, error):
		self.spinner.stop()
		self.btn_scan.setEnabled(True)

		if error:
			self._log(f"Scan error: {error}")
			return

		if not devices:
			self._log("No TinZr devices found.")
			return

		for d in devices:
			label = f"{d.name}  [{d.address}]"
			self.combo_devices.addItem(label, d.address)

		self._log(f"Found {len(devices)} device(s).")

	# ================== BLE Connect / Disconnect ==================
	def on_connect_toggled(self, checked: bool):
		self.toggle_connect.setEnabled(False)

		if checked:
			self._run_coro(self._connect_selected())
		else:
			self._run_coro(self._disconnect())

	async def _connect_selected(self):
		try:
			if self.combo_devices.count() == 0:
				raise RuntimeError("No device selected. Scan first.")

			addr = self.combo_devices.currentData()
			self._log(f"Connecting to {addr} ...")
			self._set_status("Connecting...")

			self._client = BleakClient(addr)
			await self._client.connect()

			await self._client.start_notify(TINZR_BLE_TX_CHAR_UUID, self._on_notify)

			self._connected = True
			self._log("Connected.")
			self._set_status("Connected")

			QtCore.QMetaObject.invokeMethod(
				self,
				"_ui_set_connect_toggle",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, True),
				QtCore.Q_ARG(bool, True),
			)
			QtCore.QMetaObject.invokeMethod(
				self,
				"_ui_set_record_toggle",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, self.toggle_record.isChecked()),
				QtCore.Q_ARG(bool, True),
			)
			QtCore.QMetaObject.invokeMethod(
				self,
				"_ui_enable_participant_controls",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, True),
			)

			# Optional: refresh battery once right after connect
			self._send_battery_query()

		except Exception as e:
			self._log(f"Connect failed: {e}")
			self._set_status("Idle")
			QtCore.QMetaObject.invokeMethod(
				self,
				"_ui_set_connect_toggle",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, False),
				QtCore.Q_ARG(bool, True),
			)
		finally:
			QtCore.QMetaObject.invokeMethod(
				self.toggle_connect,
				"setEnabled",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, True),
			)

	async def _disconnect(self):
		try:
			if self._logging_armed:
				try:
					await self._send_cmd(CMD_STOP)
				except Exception:
					pass

			self._stop_heartbeat_ui()

			if self._client:
				try:
					await self._client.stop_notify(TINZR_BLE_TX_CHAR_UUID)
				except Exception:
					pass
				try:
					await self._client.disconnect()
				except Exception:
					pass

			self._client = None
			self._connected = False
			self._logging_armed = False

			self._log("Disconnected.")
			self._set_status("Idle")

			QtCore.QMetaObject.invokeMethod(
				self,
				"_ui_set_record_toggle",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, False),
				QtCore.Q_ARG(bool, False),
			)
			QtCore.QMetaObject.invokeMethod(
				self,
				"_ui_set_connect_toggle",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, False),
				QtCore.Q_ARG(bool, True),
			)
			QtCore.QMetaObject.invokeMethod(
				self,
				"_ui_enable_participant_controls",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, False),
			)

		except Exception as e:
			self._log(f"Disconnect error: {e}")
		finally:
			QtCore.QMetaObject.invokeMethod(
				self.toggle_connect,
				"setEnabled",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, True),
			)

	# ================== Notify handler ==================
	def _on_notify(self, _char, data: bytearray):
		try:
			text = bytes(data).decode("utf-8", errors="ignore").strip()
		except Exception:
			text = ""

		if not text:
			return

		# Battery: BAT:87
		if text.startswith("BAT:"):
			try:
				raw = text.split(":", 1)[1].strip()
				raw = raw.replace("%", "").strip()
				if not raw:
					return
				pct = int(raw.split()[0])

				# If your firmware is percent already, ignore garbage instead of clamping.
				if pct < 0 or pct > 100:
					return

				QtCore.QMetaObject.invokeMethod(
					self,
					"_set_battery",
					QtCore.Qt.QueuedConnection,
					QtCore.Q_ARG(int, pct),
				)
			except Exception:
				pass
			return

		# Vitals parsing
		t = text.strip()
		tu = t.upper()

		if tu.startswith("VITAL:"):
			try:
				payload = t.split(":", 1)[1].strip()
				parts = [p.strip() for p in payload.replace(";", ",").split(",")]
				hr = int(parts[0])
				spo2 = int(parts[1]) if len(parts) > 1 else -1
				QtCore.QMetaObject.invokeMethod(
					self,
					"_set_vitals",
					QtCore.Qt.QueuedConnection,
					QtCore.Q_ARG(int, hr),
					QtCore.Q_ARG(int, spo2),
				)
				return
			except Exception:
				pass

		if ("HR:" in tu) or ("SPO2:" in tu) or ("SPO₂:" in tu):
			hr_val = None
			spo2_val = None

			tokens = t.replace(",", " ").split()
			for tok in tokens:
				u = tok.upper()
				if u.startswith("HR:"):
					try:
						hr_val = int(tok.split(":", 1)[1])
					except Exception:
						pass
				elif u.startswith("SPO2:") or u.startswith("SPO₂:"):
					try:
						spo2_val = int(tok.split(":", 1)[1])
					except Exception:
						pass

			if hr_val is not None or spo2_val is not None:
				hr_to_set = int(hr_val) if hr_val is not None else (int(self.hr_bpm) if self.hr_bpm else -1)
				sp_to_set = int(spo2_val) if spo2_val is not None else (int(self.spo2_pct) if self.spo2_pct else -1)

				QtCore.QMetaObject.invokeMethod(
					self,
					"_set_vitals",
					QtCore.Qt.QueuedConnection,
					QtCore.Q_ARG(int, hr_to_set),
					QtCore.Q_ARG(int, sp_to_set),
				)
				return

		QtCore.QMetaObject.invokeMethod(
			self,
			"_append_status",
			QtCore.Qt.QueuedConnection,
			QtCore.Q_ARG(str, text),
		)

	@QtCore.pyqtSlot(int)
	def _set_battery(self, pct: int):
		self.battery.setLevel(pct)

	@QtCore.pyqtSlot(str)
	def _append_status(self, text: str):
		self._set_status(text)

	# ================== Participant ==================
	def on_set_participant(self):
		name = self.edit_participant.text().strip()
		if not name:
			self._log("Participant name is empty.")
			return
		if not self._connected:
			self._log("Not connected.")
			return
		self._run_coro(self._send_participant(name))

	async def _send_participant(self, name: str):
		safe = self._sanitize_subject(name)
		cmd = f"P:{safe}".encode("utf-8")
		await self._send_cmd(cmd)
		self._log(f"Participant set: {safe}")

	# ================== Record / Heartbeat ==================
	def on_record_toggled(self, checked: bool):
		if not self._connected:
			self._log("Not connected.")
			QtCore.QMetaObject.invokeMethod(
				self,
				"_ui_set_record_toggle",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, False),
				QtCore.Q_ARG(bool, False),
			)
			return

		if checked:
			self._run_coro(self._start_logging())
		else:
			self._run_coro(self._stop_logging())

	async def _start_logging(self):
		try:
			name = self.edit_participant.text().strip()
			if not name:
				self._log("Set participant before logging.")
				QtCore.QMetaObject.invokeMethod(
					self,
					"_ui_set_record_toggle",
					QtCore.Qt.QueuedConnection,
					QtCore.Q_ARG(bool, False),
					QtCore.Q_ARG(bool, True),
				)
				return

			await self._send_participant(name)
			await self._send_cmd(self._build_experiment_meta_payload())
			await self._send_cmd(CMD_START)

			self._logging_armed = True
			self._set_status("Logging (armed)")
			self._log("Logging armed. PC timestamps will be sent every 5 seconds.")

			self._start_heartbeat_ui()

		except Exception as e:
			self._log(f"Start logging failed: {e}")
			self._logging_armed = False
			self._set_status("Connected")
			QtCore.QMetaObject.invokeMethod(
				self,
				"_ui_set_record_toggle",
				QtCore.Qt.QueuedConnection,
				QtCore.Q_ARG(bool, False),
				QtCore.Q_ARG(bool, True),
			)

	async def _stop_logging(self):
		try:
			self._stop_heartbeat_ui()
			await self._send_cmd(CMD_STOP)
		except Exception as e:
			self._log(f"Stop logging failed: {e}")
		finally:
			self._logging_armed = False
			self._set_status("Connected")
			self._log("Logging stopped.")

	def _start_heartbeat_ui(self):
		QtCore.QMetaObject.invokeMethod(
			self,
			"_gui_start_heartbeat",
			QtCore.Qt.QueuedConnection,
		)

	def _stop_heartbeat_ui(self):
		QtCore.QMetaObject.invokeMethod(
			self,
			"_gui_stop_heartbeat",
			QtCore.Qt.QueuedConnection,
		)

	def _send_heartbeat(self):
		if not self._connected or not self._client or not self._logging_armed:
			return

		# Year-Month-Day_Hour-Min-Sec_Millis.Microseconds (MS + 4 micro digits)
		now = datetime.now()
		ms = now.microsecond // 1000
		micro4 = now.microsecond % 10000  # last 4 digits of microseconds

		stamp = f"{now:%Y-%m-%dT%H:%M:%S}:{ms:03d}.{micro4:04d}"

		# send same text style but with timestamp
		cmd = f"T:{stamp}".encode("utf-8")

		self.label_hb.setText(f"Synchronization trigger: {stamp}")
		self._run_coro(self._send_cmd(cmd))

	def _send_battery_query(self):
		# called by timer (10 min) or by clicking the battery
		if not self._connected or not self._client:
			return
		self._run_coro(self._send_cmd(CMD_BATT))

	# ================== BLE send helper ==================
	async def _send_cmd(self, payload: bytes):
		if not self._client or not self._client.is_connected:
			raise RuntimeError("BLE client not connected")
		await self._client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, payload, response=False)

	def closeEvent(self, event):
		try:
			if self._loop and self._loop.is_running():
				if self._connected:
					self._run_coro(self._disconnect())
				self._loop.call_soon_threadsafe(self._loop.stop)
		except Exception:
			pass
		super().closeEvent(event)


def main():
	app = QtWidgets.QApplication(sys.argv)
	w = TinZrWearableSD()
	w.show()
	sys.exit(app.exec_())


if __name__ == "__main__":
	# Let Qt scale based on DPI so fixed window sizes stay a similar physical size across monitors
	if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
	if hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

	main()
