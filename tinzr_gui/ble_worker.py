# =========================
# File: ble_worker.py
# =========================
import os
os.environ.setdefault("BLEAK_BACKEND", "winrt")  # Windows: try WinRT first

import asyncio, threading
from queue import Queue
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
from bleak import BleakScanner, BleakClient, BleakError

# ---------- BLE Profiles we support ----------
# New TinZr (Nordic UART)
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID      = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write
NUS_TX_UUID      = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify
DEFAULT_NAME     = "TinZr"

# Legacy (classic ESP32 BLE)
LEG_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
LEG_CHAR_UUID    = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # notify
LEG_RX_UUID      = "e7810a71-73ae-499d-8c15-faa9aef0c3f2"   # optional RX (if present)
LEGACY_NAME      = "TinZr"

SCAN_TIMEOUT_SEC = 8.0

@dataclass
class DiscoveredDevice:
    name: str
    address: str


def _get_uuids(ble_device) -> List[str]:
    try:
        return [(x or "").lower() for x in (ble_device.metadata or {}).get("uuids") or []]
    except Exception:
        return []


def _advertises_target(ble_device) -> bool:
    uuids = _get_uuids(ble_device)
    nm = (ble_device.name or "")
    return (
        (DEFAULT_NAME in nm)
        or (LEGACY_NAME in nm)
        or NUS_SERVICE_UUID.lower() in uuids
        or LEG_SERVICE_UUID.lower() in uuids
    )


def _is_writable(ch) -> bool:
    """Return True if characteristic looks writable across backends."""
    try:
        props = getattr(ch, "properties", None)
        if props is None:
            return False
        # iterable of strings
        if isinstance(props, (list, tuple, set)):
            p = {str(x).lower().replace("_", "-") for x in props}
            return ("write" in p) or ("write-without-response" in p)
        # single string
        if isinstance(props, str):
            s = props.lower()
            return ("write" in s)
        # fallback string conversion
        s = str(props).lower()
        return ("write" in s)
    except Exception:
        return False


class AsyncBleWorker:
    """Unified BLE UART client (NUS + Legacy)."""

    def __init__(self, ui_queue: Queue):
        self._uiq = ui_queue
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._client: Optional[BleakClient] = None
        self._found: Dict[str, Any] = {}
        self._rx_buf = bytearray()
        self._mode: Optional[str] = None
        self._notify_uuid: Optional[str] = None
        self._write_uuid: Optional[str] = None
        self._thread.start()

    # ---------- internal ----------
    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._alive())

    async def _alive(self):
        while True:
            await asyncio.sleep(0.05)

    # ---------- lifecycle ----------
    def stop(self):
        async def _s():
            if self._client and self._client.is_connected:
                try:
                    if self._notify_uuid:
                        await self._client.stop_notify(self._notify_uuid)
                except Exception:
                    pass
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
        fut = asyncio.run_coroutine_threadsafe(_s(), self._loop)
        try:
            fut.result(3)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=3)

    def log(self, msg: str):
        self._uiq.put(("log", msg))

    # ---------- scan ----------
    def scan(self, timeout: float = SCAN_TIMEOUT_SEC):
        async def _scan():
            backend = os.environ.get("BLEAK_BACKEND")
            self.log(f"Scanning ({backend})…")

            named: List[DiscoveredDevice] = []
            self._found.clear()

            devs = await BleakScanner.discover(timeout=timeout)
            for d in devs:
                self._found[d.address] = d
                nm = (d.name or "").strip()
                if nm or _advertises_target(d):
                    if not nm:
                        nm = "(no-name)"
                    named.append(DiscoveredDevice(nm, d.address))

            def priority(dd: DiscoveredDevice) -> Tuple[int,int,str]:
                d = self._found.get(dd.address)
                nm = dd.name.lower()
                uu = _get_uuids(d)
                return (
                    0 if (DEFAULT_NAME.lower() in nm or LEGACY_NAME.lower() in nm) else 1,
                    0 if (NUS_SERVICE_UUID.lower() in uu or LEG_SERVICE_UUID.lower() in uu) else 1,
                    nm
                )

            named.sort(key=priority)

            if not named and os.name == "nt" and backend != "dotnet":
                self.log("No candidates with WinRT. Retrying with BLEAK_BACKEND=dotnet…")
                os.environ["BLEAK_BACKEND"] = "dotnet"
                devs = await BleakScanner.discover(timeout=timeout)
                for d in devs:
                    self._found[d.address] = d
                    nm = (d.name or "").strip()
                    if nm or _advertises_target(d):
                        if not nm: nm = "(no-name)"
                        named.append(DiscoveredDevice(nm, d.address))
                named.sort(key=priority)

            if not named:
                self.log("Scan done: 0 candidates. Check power/advertising.")
            else:
                self.log("Scan results:")
                for dd in named[:10]:
                    self.log(f"  - {dd.name} [{dd.address}]")
                self._uiq.put(("hint_autopick", named[0].address))

            self._uiq.put(("scan_result", named))
        return asyncio.run_coroutine_threadsafe(_scan(), self._loop)

    # ---------- connect ----------
    def connect(self, address: str):
        async def _con():
            try:
                if self._client and self._client.is_connected:
                    await self._client.disconnect()
                self.log(f"Connecting to {address}…")
                target = self._found.get(address, address)
                c = BleakClient(target, timeout=12)
                await c.connect()

                svcs = await c.get_services()
                svc_uuids = {s.uuid.lower() for s in svcs}

                if NUS_SERVICE_UUID.lower() in svc_uuids:
                    self._mode = "nus"
                    self._notify_uuid = NUS_TX_UUID
                    self._write_uuid  = NUS_RX_UUID
                    await c.start_notify(NUS_TX_UUID, self._on_notify)
                    self.log("Mode: Nordic UART (NUS).")

                elif LEG_SERVICE_UUID.lower() in svc_uuids:
                    self._mode = "legacy"
                    self._notify_uuid = LEG_CHAR_UUID
                    self._write_uuid  = None

                    # Log all chars
                    self.log("GATT layout:")
                    for svc in svcs:
                        self.log(f"  svc {svc.uuid}")
                        for ch in svc.characteristics:
                            self.log(f"    ch {ch.uuid} props={getattr(ch,'properties',None)}")

                    # Prefer explicit RX UUID
                    for svc in svcs:
                        for ch in svc.characteristics:
                            if ch.uuid.lower() == LEG_RX_UUID.lower():
                                self._write_uuid = ch.uuid
                                break
                        if self._write_uuid:
                            break

                    # Fallback: any writable char in legacy service
                    if not self._write_uuid:
                        for svc in svcs:
                            if svc.uuid.lower() == LEG_SERVICE_UUID.lower():
                                for ch in svc.characteristics:
                                    if ch.uuid.lower() != LEG_CHAR_UUID.lower() and _is_writable(ch):
                                        self._write_uuid = ch.uuid
                                        break
                                break

                    await c.start_notify(LEG_CHAR_UUID, self._on_notify)

                    if self._write_uuid:
                        self.log(f"Mode: Legacy with RX ({self._write_uuid}).")
                    else:
                        self.log("Mode: Legacy (notify-only).")

                else:
                    try:
                        await c.start_notify(NUS_TX_UUID, self._on_notify)
                        self._mode = "nus"
                        self._notify_uuid = NUS_TX_UUID
                        self._write_uuid = NUS_RX_UUID
                        self.log("Mode: NUS (fallback by char).")
                    except Exception:
                        await c.start_notify(LEG_CHAR_UUID, self._on_notify)
                        self._mode = "legacy"
                        self._notify_uuid = LEG_CHAR_UUID
                        self._write_uuid = None
                        self.log("Mode: Legacy (fallback by char).")

                self._client = c
                self._rx_buf.clear()
                self._uiq.put(("connected", True))
                self.log("Connected.")
            except Exception as e:
                self._uiq.put(("connected", False))
                self.log(f"Connect failed: {e}")
        return asyncio.run_coroutine_threadsafe(_con(), self._loop)

    # ---------- disconnect ----------
    def disconnect(self):
        async def _d():
            if self._client and self._client.is_connected:
                try:
                    if self._notify_uuid:
                        await self._client.stop_notify(self._notify_uuid)
                except Exception:
                    pass
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
            self._client = None
            self._uiq.put(("connected", False))
            self.log("Disconnected.")
        return asyncio.run_coroutine_threadsafe(_d(), self._loop)

    # ---------- write ----------
    def write_line(self, text: str, require_response: bool = False):
        async def _w():
            if not (self._client and self._client.is_connected):
                self.log("Not connected.")
                return
            if not self._write_uuid:
                self.log("Write ignored: no writable characteristic on this device.")
                return
            data = (text.rstrip("\r\n") + "\n").encode()
            try:
                await self._client.write_gatt_char(self._write_uuid, data, response=require_response)
                self.log(f"→ {text}")
            except BleakError as e:
                self.log(f"Write failed: {e}")
        return asyncio.run_coroutine_threadsafe(_w(), self._loop)

    # ---------- notifications ----------
    def _on_notify(self, _h, data: bytearray):
        if not (self._client and self._client.is_connected):
            return

        self._rx_buf.extend(data)
        while True:
            nl = self._rx_buf.find(b"\n")
            if nl == -1:
                break
            raw = self._rx_buf[:nl]
            del self._rx_buf[:nl + 1]
            line = raw.decode(errors="replace").rstrip("\r")

            if line.startswith("IMU,"):
                self._uiq.put(("imu", line)); continue
            if line.startswith(("VBAT,", "BAT,")):
                self._uiq.put(("bat", line)); continue
            if line.startswith("PPG,"):
                self._uiq.put(("ppg", line)); continue

            if self._mode == "legacy":
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 5:
                    try:
                        ax, ay, az = float(parts[0]), float(parts[1]), float(parts[2])
                        ir, red = float(parts[3]), float(parts[4])
                        imu_norm = f"IMU,{ax:.3f},{ay:.3f},{az:.3f},0,0,0,0"
                        ppg_norm = f"PPG,{int(ir)},{int(red)},0"
                        self._uiq.put(("imu", imu_norm))
                        self._uiq.put(("ppg", ppg_norm))
                        continue
                    except Exception:
                        pass
            self._uiq.put(("notify", line))
