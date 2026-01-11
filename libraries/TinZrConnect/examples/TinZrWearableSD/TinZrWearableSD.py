import os
import sys
import asyncio
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


class DeviceRow(QtWidgets.QFrame):
	"""One TinZr row: alias, battery, connect toggle, remove button."""
	connect_toggled = QtCore.pyqtSignal(str, bool)  # addr, checked
	remove_clicked = QtCore.pyqtSignal(str)         # addr
	battery_clicked = QtCore.pyqtSignal(str)        # addr

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
		self.toggle_connect.setChecked(False)
		self.toggle_connect.toggled.connect(lambda checked: self.connect_toggled.emit(self.addr, checked))

		lay.addWidget(lbl_connect, 0, QtCore.Qt.AlignVCenter)
		lay.addWidget(self.toggle_connect, 0, QtCore.Qt.AlignVCenter)

		self.btn_remove = QtWidgets.QPushButton("✕")
		self.btn_remove.setFixedSize(28, 28)
		self.btn_remove.setToolTip("Remove device")
		self.btn_remove.setStyleSheet(
			"QPushButton{font-weight:700; border-radius:8px; padding:0px; }"
			"QPushButton:hover{background: rgba(255,255,255,0.08);}"
		)
		self.btn_remove.clicked.connect(lambda: self.remove_clicked.emit(self.addr))
		lay.addWidget(self.btn_remove, 0, QtCore.Qt.AlignVCenter)

	# ---------- UI-safe slots (these are the only ones you should invoke via QMetaObject) ----------
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


class TinZrWearableSD(QtWidgets.QWidget):
	scan_finished = QtCore.pyqtSignal(object, object)  # (devices, error)

	def __init__(self):
		super().__init__()

		self.setWindowTitle("TinZr Wearable SD Logging")
		self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))

		# ---------- Taller window ----------
		self.setFixedSize(600, 520)
		self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)

		# ---------- Global style / theme (from helper) ----------
		apply_tinzr_theme(self)

		# ---------- State ----------
		self._loop = None
		self._loop_thread = None

		# Discovered devices from last scan
		self._scan_devices = []

		# Added devices
		self.devices = {}

		# Common logging flag for ALL devices
		self._logging_armed = False

		# ---------- UI ----------
		self._build_ui()

		# ---------- Async loop thread ----------
		self._start_async_loop_thread()

		# ---------- Timers ----------
		self.batt_timer = QtCore.QTimer(self)
		self.batt_timer.timeout.connect(self._poll_battery_all)
		self.batt_timer.start(BATT_POLL_MS)

		self.heartbeat_timer = QtCore.QTimer(self)
		self.heartbeat_timer.timeout.connect(self._send_heartbeat_all)

		# ---------- Signals ----------
		self.scan_finished.connect(self._on_scan_finished)

	# ---------------- Qt-thread invoke helper ----------------
	def _invoke(self, obj, method: str, *args):
		"""
		Safe invoke into Qt thread. Assumes target method is a @pyqtSlot with matching signature.
		Supports bool/int/str args only (enough for this app).
		"""
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

	# ================== UI thread helpers (slots) ==================
	@QtCore.pyqtSlot(str)
	def _ui_set_status(self, text: str):
		self.label_status.setText(f"Status: {text}")

	def _set_status(self, text: str):
		self._invoke(self, "_ui_set_status", str(text))

	def _log(self, msg: str):
		self._set_status(msg)

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
		self._send_heartbeat_all()

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
		sub = QtWidgets.QLabel("Multi-device • PC-timestamp SD logging")
		sub.setStyleSheet("font-size: 9pt; color: #A8B3CF;")

		title_box.addWidget(title)
		title_box.addWidget(sub)
		h.addLayout(title_box, 1)

		main_layout.addWidget(header)

		# ================== Controls Card ==================
		ctrl_widget = QtWidgets.QFrame()
		ctrl_widget.setObjectName("card")
		ctrl_layout = QtWidgets.QGridLayout(ctrl_widget)
		ctrl_layout.setContentsMargins(12, 12, 12, 12)
		ctrl_layout.setHorizontalSpacing(10)
		ctrl_layout.setVerticalSpacing(10)

		row = 0

		# Row 0: Scan + devices + Add
		self.btn_scan = QtWidgets.QPushButton("Scan")
		self.btn_scan.clicked.connect(self.on_scan_clicked)

		self.spinner = Spinner(radius=8, line_width=2, parent=self.btn_scan)
		self.spinner.raise_()
		self.btn_scan.installEventFilter(self)
		self._center_spinner_on_button()

		self.combo_devices = QtWidgets.QComboBox()

		# --- Add button (nicer, TinZr blue) ---
		self.btn_add = QtWidgets.QPushButton("＋")  # full-width plus looks cleaner than "+"
		self.btn_add.setFixedSize(44, 34)
		self.btn_add.setCursor(QtCore.Qt.PointingHandCursor)
		self.btn_add.setToolTip("Add selected TinZr")
		self.btn_add.clicked.connect(self.on_add_clicked)

		# TinZr blue style (same shape/feel)
		self.btn_add.setStyleSheet("""
			QPushButton {
				font-size: 14pt;
				font-weight: 700;
				color: #E3F2FD;
				background: rgba(33, 150, 243, 0.18);   /* TinZr blue */
				border: 1px solid rgba(33, 150, 243, 0.45);
				border-radius: 10px;
				padding: 0px;
			}
			QPushButton:hover {
				background: rgba(33, 150, 243, 0.28);
				border: 1px solid rgba(33, 150, 243, 0.65);
			}
			QPushButton:pressed {
				background: rgba(33, 150, 243, 0.38);
			}
			QPushButton:disabled {
				color: rgba(227,242,253,0.35);
				background: rgba(33,150,243,0.10);
				border: 1px solid rgba(33,150,243,0.20);
			}
		""")


		ctrl_layout.addWidget(self.btn_scan, row, 0, 1, 1)
		ctrl_layout.addWidget(self.combo_devices, row, 1, 1, 3)
		ctrl_layout.addWidget(self.btn_add, row, 4, 1, 1)

		row += 1

		# Row 1: SD Log (common)
		lbl_record  = QtWidgets.QLabel("SD Log (All)")
		self.toggle_record  = ToggleSwitch()
		self.toggle_record.setChecked(False)
		self.toggle_record.toggled.connect(self.on_record_toggled)

		ctrl_layout.addWidget(lbl_record,          row, 0)
		ctrl_layout.addWidget(self.toggle_record,  row, 1)
		ctrl_layout.addWidget(QtWidgets.QLabel(""), row, 2, 1, 3)

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

		main_layout.addWidget(ctrl_widget)

		# ================== Devices List Card ==================
		list_card = QtWidgets.QFrame()
		list_card.setObjectName("card")
		list_lay = QtWidgets.QVBoxLayout(list_card)
		list_lay.setContentsMargins(12, 12, 12, 12)
		list_lay.setSpacing(8)

		lbl = QtWidgets.QLabel("Added TinZrs")
		lbl.setStyleSheet("font-size: 10pt; font-weight: 600; color: #E3F2FD;")
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

		main_layout.addWidget(list_card)

		# Disable logging/participant until at least one connected device
		self.toggle_record.setEnabled(False)
		self._ui_enable_participant_controls(False)

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

	# ================== Experiment meta (subject + PC time) ==================
	def _sanitize_subject(self, subject: str) -> str:
		return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in subject)

	def _build_experiment_meta_payload(self) -> bytes:
		subject = self._sanitize_subject(self.edit_participant.text().strip())

		now = datetime.now()
		ms = now.microsecond // 1000
		micro4 = now.microsecond % 10000
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
		self._scan_devices = []
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

		self._scan_devices = devices
		for d in devices:
			label = f"{d.name}  [{d.address}]"
			self.combo_devices.addItem(label, d.address)

		self._log(f"Found {len(devices)} device(s).")

	# ================== Add / Remove ==================
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
		dlg.setWindowFlags(dlg.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)  # <-- removes "?"
		dlg.setStyleSheet(self.styleSheet())  # keep TinZr theme

		ok = dlg.exec_()
		alias = dlg.textValue() if ok else None
		if not ok:
			return

		if not ok:
			return
		alias = alias.strip() if alias else ble_name

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

		self._log("Device removed.")

	# ================== Per-device battery click ==================
	def on_device_battery_clicked(self, addr: str):
		if addr not in self.devices:
			return
		self._run_coro(self._send_cmd_to_device(addr, CMD_BATT))

	# ================== Per-device connect ==================
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
			client = BleakClient(addr)
			await client.connect()

			def _notify(_char, data: bytearray, _addr=addr):
				self._on_notify(_addr, data)

			await client.start_notify(TINZR_BLE_TX_CHAR_UUID, _notify)

			info["client"] = client
			info["connected"] = True

			self._invoke(row, "set_connected", True)
			self._invoke(row, "set_connect_enabled", True)

			self._log(f"Connected: {info['alias']}")

			# enable global SD controls if any is connected
			if self._any_connected():
				self._invoke(self, "_ui_set_record_toggle", self.toggle_record.isChecked(), True)
				self._invoke(self, "_ui_enable_participant_controls", True)

			# immediate battery refresh
			await self._send_cmd_to_device(addr, CMD_BATT)

			# if already logging globally, arm this device too
			if self._logging_armed:
				await self._arm_device_for_logging(addr)

		except Exception as e:
			self._log(f"Connect failed ({addr}): {e}")
			info["client"] = None
			info["connected"] = False
			self._invoke(row, "set_connected", False)
			self._invoke(row, "set_connect_enabled", True)

	async def _disconnect_device(self, addr: str, reason: str = ""):
		info = self.devices.get(addr)
		if not info:
			return

		row: DeviceRow = info["row"]
		client: BleakClient = info.get("client")

		try:
			if self._logging_armed and client and client.is_connected:
				try:
					await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, CMD_STOP, response=False)
				except Exception:
					pass

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
			info["connected"] = False
			self._invoke(row, "set_connected", False)
			self._invoke(row, "set_connect_enabled", True)

			if not self._any_connected():
				self._stop_logging_ui_state()

			if reason != "remove":
				self._log(f"Disconnected: {info['alias']}")

	def _any_connected(self) -> bool:
		return any(info.get("connected") for info in self.devices.values())

	# ================== Notify handler ==================
	def _on_notify(self, addr: str, data: bytearray):
		# runs on bleak thread -> do NOT touch Qt widgets directly
		try:
			text = bytes(data).decode("utf-8", errors="ignore").strip()
		except Exception:
			return

		if not text:
			return

		info = self.devices.get(addr)
		if not info:
			return

		row: DeviceRow = info["row"]

		if text.startswith("BAT:"):
			try:
				raw = text.split(":", 1)[1].strip().replace("%", "").strip()
				if not raw:
					return
				pct = int(raw.split()[0])
				if pct < 0 or pct > 100:
					return
				self._invoke(row, "set_battery", pct)
			except Exception:
				pass
			return

		# keep status line simple
		self._invoke(self, "_ui_set_status", text)

	# ================== Participant ==================
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

	# ================== Record / Heartbeat (COMMON) ==================
	def on_record_toggled(self, checked: bool):
		if not self._any_connected():
			self._log("No connected TinZr.")
			self._invoke(self, "_ui_set_record_toggle", False, False)
			return

		if checked:
			self._run_coro(self._start_logging_all())
		else:
			self._run_coro(self._stop_logging_all())

	async def _arm_device_for_logging(self, addr: str):
		info = self.devices.get(addr)
		if not info or not info.get("connected"):
			return

		name = self.edit_participant.text().strip()
		if not name:
			raise RuntimeError("Set participant before logging")

		safe = self._sanitize_subject(name)
		await self._send_cmd_to_device(addr, f"P:{safe}".encode("utf-8"))
		await self._send_cmd_to_device(addr, self._build_experiment_meta_payload())
		await self._send_cmd_to_device(addr, CMD_START)

	async def _start_logging_all(self):
		try:
			name = self.edit_participant.text().strip()
			if not name:
				self._log("Set participant before logging.")
				self._invoke(self, "_ui_set_record_toggle", False, True)
				return

			for addr, info in list(self.devices.items()):
				if info.get("connected"):
					await self._arm_device_for_logging(addr)

			self._logging_armed = True
			self._log("Logging armed for all connected TinZrs. PC timestamps every 5 seconds.")
			self._start_heartbeat_ui()

		except Exception as e:
			self._log(f"Start logging failed: {e}")
			self._logging_armed = False
			self._invoke(self, "_ui_set_record_toggle", False, True)

	async def _stop_logging_all(self):
		try:
			self._stop_heartbeat_ui()
			await self._send_cmd_all(CMD_STOP)
		except Exception as e:
			self._log(f"Stop logging failed: {e}")
		finally:
			self._logging_armed = False
			self._log("Logging stopped.")

	def _start_heartbeat_ui(self):
		self._invoke(self, "_gui_start_heartbeat")

	def _stop_heartbeat_ui(self):
		self._invoke(self, "_gui_stop_heartbeat")

	def _send_heartbeat_all(self):
		if not self._any_connected() or not self._logging_armed:
			return

		now = datetime.now()
		ms = now.microsecond // 1000
		micro4 = now.microsecond % 10000
		stamp = f"{now:%Y-%m-%dT%H:%M:%S}:{ms:03d}.{micro4:04d}"
		cmd = f"T:{stamp}".encode("utf-8")

		self.label_hb.setText(f"Synchronization trigger: {stamp}")
		self._run_coro(self._send_cmd_all(cmd))

	def _poll_battery_all(self):
		if not self._any_connected():
			return
		self._run_coro(self._send_cmd_all(CMD_BATT))

	def _stop_logging_ui_state(self):
		if self._logging_armed:
			self._logging_armed = False
		self._stop_heartbeat_ui()
		self._invoke(self, "_ui_set_record_toggle", False, False)
		self._invoke(self, "_ui_enable_participant_controls", False)

	# ================== BLE send helpers ==================
	async def _send_cmd_to_device(self, addr: str, payload: bytes):
		info = self.devices.get(addr)
		if not info:
			raise RuntimeError("Unknown device")
		client: BleakClient = info.get("client")
		if not client or not client.is_connected:
			raise RuntimeError("BLE client not connected")
		await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, payload, response=False)

	async def _send_cmd_all(self, payload: bytes):
		for addr, info in list(self.devices.items()):
			client: BleakClient = info.get("client")
			if not client or not client.is_connected:
				continue
			try:
				await client.write_gatt_char(TINZR_BLE_RX_CHAR_UUID, payload, response=False)
			except Exception:
				pass

	def closeEvent(self, event):
		try:
			if self._loop and self._loop.is_running():
				for addr in list(self.devices.keys()):
					self._run_coro(self._disconnect_device(addr, reason="close"))
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
	if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
	if hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
		QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

	main()
