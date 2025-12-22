# tab_wifi_hub.py  (PyQt5 version of WifiHubTab)
import os 
import sys
import socket
import threading
import queue
import time

from PyQt5 import QtCore, QtWidgets


# =========================
# DPU framing (TinZr WiFi)
# =========================
_DPU_MAGIC0 = ord('T')
_DPU_MAGIC1 = ord('Z')
_DPU_VER    = 1
_DPU_FLAG_CRC32 = 0x01  # reserved (not used by default)

def _u16_le(b0: int, b1: int) -> int:
    return b0 | (b1 << 8)

def dpu_encode(msg_type: int, payload: bytes, want_crc: bool = False) -> bytes:
    """Encode a DPU frame.
    Format (little-endian):
    [T][Z][ver][flags][type u16][len u16][payload...][optional crc32 u32]
    """
    if payload is None:
        payload = b""
    flags = _DPU_FLAG_CRC32 if want_crc else 0
    n = len(payload)
    hdr = bytes([
        _DPU_MAGIC0, _DPU_MAGIC1, _DPU_VER, flags,
        msg_type & 0xFF, (msg_type >> 8) & 0xFF,
        n & 0xFF, (n >> 8) & 0xFF,
    ])
    # NOTE: CRC32 not implemented on PC side yet (want_crc must stay False)
    return hdr + payload

class DPUParser:
    """Incremental DPU parser for TCP streams (handles split/merged packets)."""
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
        if data:
            self.buf.extend(data)

    def pop_frames(self):
        out = []
        while True:
            if len(self.buf) < 8:
                break

            # resync to 'TZ'
            i = 0
            while i + 1 < len(self.buf) and not (self.buf[i] == _DPU_MAGIC0 and self.buf[i+1] == _DPU_MAGIC1):
                i += 1
            if i > 0:
                del self.buf[:i]
                if len(self.buf) < 8:
                    break

            if not (self.buf[0] == _DPU_MAGIC0 and self.buf[1] == _DPU_MAGIC1):
                break

            ver = self.buf[2]
            if ver != _DPU_VER:
                # drop 1 byte and resync
                del self.buf[0]
                continue

            flags = self.buf[3]
            msg_type = _u16_le(self.buf[4], self.buf[5])
            n = _u16_le(self.buf[6], self.buf[7])

            need = 8 + n + (4 if (flags & _DPU_FLAG_CRC32) else 0)
            if len(self.buf) < need:
                break

            payload = bytes(self.buf[8:8+n])
            del self.buf[:need]
            out.append((msg_type, payload))
        return out

try:
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    PARENT_DIR = os.path.dirname(CURRENT_DIR)
    if PARENT_DIR not in sys.path:
        sys.path.insert(0, PARENT_DIR)
    from GUIsHelper import apply_tinzr_theme
except ImportError:
    apply_tinzr_theme = None



DEFAULT_UDP_PORT   = 4210
DEFAULT_TCP_PORT   = 4211
DEFAULT_MCAST_GRP  = "239.1.1.1"


class WifiHubTab(QtWidgets.QWidget):
    """
    TinZr WIFI Hub:
      - Receives HELLO / messages from TinZr nodes via UDP (and multicast)
      - Tracks peers by IP and last_seen
      - Can send UDP messages to all / selected peers
      - Optional TCP listener for node-initiated messages

    All underlying logic is identical to the original Tkinter version;
    only the GUI toolkit has changed.
    """

    def __init__(self, app=None, parent=None):
        super().__init__(parent)
        self.app = app

        # Hub state
        self.running = False
        self.udp_port = DEFAULT_UDP_PORT
        self.tcp_port = DEFAULT_TCP_PORT
        self.mcast_group = DEFAULT_MCAST_GRP

        self.udp_sock = None
        self.tcp_sock = None
        self.send_sock = None

        self.udp_thread = None
        self.tcp_thread = None

        # Peer tracking
        # ip -> {"last_seen": float, "name": Optional[str]}
        self.peers = {}
        self.peers_lock = threading.Lock()
        
        self.ip_to_name = {}      # ip_str -> name
        self.name_to_ip = {}      # name -> ip_str (optional)


        # Active TCP connections (ip -> socket)
        self.tcp_clients = {}
        self.tcp_clients_lock = threading.Lock()

        # Queues for thread-safe UI updates
        self.log_queue = queue.Queue()
        self.event_queue = queue.Queue()

        self._build_ui()

        # Poll queues just like Tk .after(...)
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.timeout.connect(self._poll_queues)
        self._poll_timer.start(100)

        if apply_tinzr_theme is not None:
            apply_tinzr_theme(self)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(20)
        main_layout.addWidget(top)

        # Left side: controls
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        top_layout.addWidget(left, stretch=1)

        title = QtWidgets.QLabel("TinZr WIFI Hub")
        font = title.font()
        font.setPointSize(9)
        font.setBold(True)
        title.setFont(font)
        left_layout.addWidget(title)

        desc = QtWidgets.QLabel(
            "This PC will:\n"
            "• Listen for HELLO / messages from TinZrs (UDP & multicast)\n"
            "• Track each peer by IP and name\n"
            "• Relay messages from PC → TinZr nodes via UDP\n"
            "• Optionally handle TCP messages from TinZr nodes"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("""
            QLabel {
                color: gray;
                font-size: 10pt;
            }
        """)
        left_layout.addWidget(desc)

        # ----- UDP port row -----
        row_udp = QtWidgets.QWidget()
        row_udp_layout = QtWidgets.QHBoxLayout(row_udp)
        row_udp_layout.setContentsMargins(0, 0, 0, 0)
        row_udp_layout.setSpacing(4)

        lbl_udp = QtWidgets.QLabel("UDP port:\t  ")
        row_udp_layout.addWidget(lbl_udp)

        self.udp_port_edit = QtWidgets.QLineEdit(str(DEFAULT_UDP_PORT))
        self.udp_port_edit.setFixedWidth(120)  # wider and close to label
        row_udp_layout.addWidget(self.udp_port_edit)

        row_udp_layout.addStretch(1)
        left_layout.addWidget(row_udp)

        # ----- TCP port row -----
        row_tcp = QtWidgets.QWidget()
        row_tcp_layout = QtWidgets.QHBoxLayout(row_tcp)
        row_tcp_layout.setContentsMargins(0, 0, 0, 0)
        row_tcp_layout.setSpacing(4)

        lbl_tcp = QtWidgets.QLabel("TCP port:\t  ")
        row_tcp_layout.addWidget(lbl_tcp)

        self.tcp_port_edit = QtWidgets.QLineEdit(str(DEFAULT_TCP_PORT))
        self.tcp_port_edit.setFixedWidth(120)
        row_tcp_layout.addWidget(self.tcp_port_edit)

        row_tcp_layout.addStretch(1)
        left_layout.addWidget(row_tcp)

        # ----- Multicast group row -----
        row_mc = QtWidgets.QWidget()
        row_mc_layout = QtWidgets.QHBoxLayout(row_mc)
        row_mc_layout.setContentsMargins(0, 0, 0, 0)
        row_mc_layout.setSpacing(4)

        lbl_mc = QtWidgets.QLabel("Multicast group:\t  ")
        row_mc_layout.addWidget(lbl_mc)

        self.mcast_edit = QtWidgets.QLineEdit(DEFAULT_MCAST_GRP)
        self.mcast_edit.setFixedWidth(220)  # wider for IP string
        row_mc_layout.addWidget(self.mcast_edit)

        row_mc_layout.addStretch(1)
        left_layout.addWidget(row_mc)

        # Relay checkbox (kept for compatibility, not functionally used)
        self.relay_checkbox = QtWidgets.QCheckBox(
            "Relay TinZr→TinZr messages via hub (TCP; optional)"
        )
        self.relay_checkbox.setChecked(True)
        left_layout.addWidget(self.relay_checkbox)

        btn_row = QtWidgets.QWidget()
        btn_layout = QtWidgets.QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)
        left_layout.addWidget(btn_row)

        self.start_button = QtWidgets.QPushButton("Start Hub")
        self.start_button.clicked.connect(self.on_start_hub)
        btn_layout.addWidget(self.start_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.on_stop_hub)
        btn_layout.addWidget(self.stop_button)

        btn_layout.addStretch(1)

        self.status_label = QtWidgets.QLabel("Hub stopped")
        self.status_label.setStyleSheet("color: gray;")
        left_layout.addWidget(self.status_label)

        left_layout.addStretch(1)

        # Right side: peer list
        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        top_layout.addWidget(right, stretch=1)

        right_layout.addWidget(QtWidgets.QLabel("Known TinZr Peers:"))
        self.peers_list = QtWidgets.QListWidget()
        self.peers_list.setMinimumWidth(220)
        right_layout.addWidget(self.peers_list)

        # Bottom: log + send area
        # Log
        log_frame = QtWidgets.QGroupBox("Hub Log:")
        log_layout = QtWidgets.QVBoxLayout(log_frame)
        self.text_log = QtWidgets.QPlainTextEdit()
        self.text_log.setReadOnly(True)
        log_layout.addWidget(self.text_log)
        main_layout.addWidget(log_frame, stretch=1)

        # Send area
        send_frame = QtWidgets.QWidget()
        send_layout = QtWidgets.QHBoxLayout(send_frame)
        send_layout.setContentsMargins(0, 0, 0, 0)
        send_layout.setSpacing(6)

        label = QtWidgets.QLabel(
            "Send message (e.g. 'LED 0 255 0 20', 'BAT', 'PING'):"
        )
        label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        main_layout.addWidget(label)

        self.send_entry = QtWidgets.QLineEdit()
        self.send_entry.returnPressed.connect(self.on_send_all)
        send_layout.addWidget(self.send_entry, stretch=1)

        btn_all = QtWidgets.QPushButton("Send to all")
        btn_all.clicked.connect(self.on_send_all)
        send_layout.addWidget(btn_all)

        btn_sel = QtWidgets.QPushButton("Send to selected")
        btn_sel.clicked.connect(self.on_send_selected)
        send_layout.addWidget(btn_sel)

        main_layout.addWidget(send_frame)

    # ------------------------------------------------------------------
    # Hub lifecycle (same logic as original)
    # ------------------------------------------------------------------
    def on_start_hub(self):
        if self.running:
            return

        try:
            udp_port = int(self.udp_port_edit.text().strip())
            tcp_port = int(self.tcp_port_edit.text().strip())
        except ValueError:
            QtWidgets.QMessageBox.critical(
                self, "TinZr Com Center", "UDP and TCP ports must be integers."
            )
            return

        mcast = self.mcast_edit.text().strip()
        if not mcast:
            QtWidgets.QMessageBox.critical(
                self, "TinZr Com Center", "Multicast group cannot be empty."
            )
            return

        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.mcast_group = mcast

        self.running = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_status("Hub running (listening on UDP + TCP).", "green")
        self._log(f"[HUB] Starting (UDP {udp_port}, TCP {tcp_port}, MCAST {mcast})")

        # UDP listen + multicast join
        self.udp_thread = threading.Thread(
            target=self._udp_listener_thread, daemon=True
        )
        self.udp_thread.start()

        # TCP listener
        self.tcp_thread = threading.Thread(
            target=self._tcp_listener_thread, daemon=True
        )
        self.tcp_thread.start()

        # UDP send socket
        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Allow broadcast and kick off discovery burst
        try:
            self.send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass

        threading.Thread(target=self._discovery_burst, daemon=True).start()
    
    
    
    def _set_device_name(self, ip_str: str, name: str):
        name = (name or "").strip()
        if not name:
            return
        self.ip_to_name[ip_str] = name
        self.name_to_ip[name] = ip_str
        # Refresh UI list/table if you have a method for that
        try:
            self._refresh_devices_ui()
        except Exception:
            pass



    def _discovery_burst(self):
        """
        On hub startup, send a few HUB-QUERY packets via multicast and broadcast
        so silent nodes will announce themselves with HELLO.
        """
        msg = b"HUB-QUERY"
        for i in range(5):  # try 5 times over ~5 seconds
            if not self.running or self.send_sock is None:
                break
            try:
                # Multicast query
                try:
                    self.send_sock.sendto(msg, (self.mcast_group, self.udp_port))
                except OSError as e:
                    self._log(f"[DISCOVERY] Multicast HUB-QUERY failed: {e}")

                # Broadcast query
                try:
                    self.send_sock.sendto(msg, ("255.255.255.255", self.udp_port))
                except OSError as e:
                    self._log(f"[DISCOVERY] Broadcast HUB-QUERY failed: {e}")

                self._log(f"[DISCOVERY] Sent HUB-QUERY burst {i+1}/5")
            except Exception as e:
                self._log(f"[DISCOVERY] Error during HUB-QUERY burst: {e}")
                break

            time.sleep(1.0)

    def on_stop_hub(self):
        if not self.running:
            return

        self.running = False
        self._set_status("Hub stopping...", "gray")
        self._log("[HUB] Stopping hub...")

        # Close UDP socket
        if self.udp_sock is not None:
            try:
                self.udp_sock.close()
            except Exception:
                pass
            self.udp_sock = None

        # Close TCP socket
        if self.tcp_sock is not None:
            try:
                self.tcp_sock.close()
            except Exception:
                pass
            self.tcp_sock = None

        # Close send socket
        if self.send_sock is not None:
            try:
                self.send_sock.close()
            except Exception:
                pass
            self.send_sock = None

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._set_status("Hub stopped", "gray")
        self._log("[HUB] Stopped.")


    # ------------------------------------------------------------------
    # UDP listener (same logic, but HELLO is now silent)
    # ------------------------------------------------------------------
    def _udp_listener_thread(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind(("", self.udp_port))
        except OSError as e:
            self._log(f"[UDP] ERROR binding UDP port {self.udp_port}: {e}")
            self._set_status(f"UDP bind error: {e}", "red")
            return

        # Join multicast group
        try:
            mreq = socket.inet_aton(self.mcast_group) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            self._log(f"[UDP] Joined multicast group {self.mcast_group}")
        except OSError as e:
            self._log(
                f"[UDP] WARNING: Could not join multicast group {self.mcast_group}: {e}"
            )

        self.udp_sock = sock
        self._log(f"[UDP] Listening on 0.0.0.0:{self.udp_port}")

        sock.settimeout(0.5)

        while self.running:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            ip, port = addr
            text = data.decode("utf-8", errors="replace").strip()

            # HELLO from node: "HELLO" or "HELLO <name>"
            if text.startswith("HELLO"):
                # Do NOT log HELLO; just learn the peer quietly
                parts = text.split(maxsplit=1)
                name = parts[1] if len(parts) > 1 else None
                self._learn_peer(ip, name)
                try:
                    sock.sendto(b"HUB-ACK", addr)
                    # also do NOT log HUB-ACK for HELLO
                except OSError as e:
                    # only log if something goes wrong
                    self._log(f"[UDP] HUB-ACK send failed to {self._peer_tag(ip)}: {e}")
            else:
                # Normal message: log + update last_seen
                self._log(f"[UDP RX {self._peer_tag(ip)}:{port}] {text!r}")
                self._learn_peer(ip)

        try:
            sock.close()
        except Exception:
            pass
        self._log("[UDP] Listener stopped.")


    # ------------------------------------------------------------------
    # TCP listener (same logic)
    # ------------------------------------------------------------------
    def _tcp_listener_thread(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("", self.tcp_port))
            srv.listen(8)
        except OSError as e:
            self._log(f"[TCP] ERROR binding TCP port {self.tcp_port}: {e}")
            self._set_status(f"TCP bind error: {e}", "red")
            return

        self.tcp_sock = srv
        self._log(f"[TCP] Listening on 0.0.0.0:{self.tcp_port}")

        while self.running:
            try:
                conn, addr = srv.accept()
            except OSError:
                break

            ip, port = addr
            self._log(f"[TCP] Connection from {self._peer_tag(ip)}:{port}")
            threading.Thread(
                target=self._handle_tcp_client,
                args=(conn, addr),
                daemon=True,
            ).start()

        try:
            srv.close()
        except Exception:
            pass
        self._log("[TCP] Listener stopped.")

    
    def _handle_tcp_client(self, conn, addr):
            ip, port = addr
            self._learn_peer(ip)

            # Register this TCP client so we can send commands back
            with self.tcp_clients_lock:
                self.tcp_clients[ip] = conn

            parser = DPUParser()

            try:
                with conn:
                    while self.running:
                        try:
                            data = conn.recv(4096)
                        except OSError:
                            break
                        if not data:
                            break

                        self._learn_peer(ip)

                        # DPU framed stream
                        parser.feed(data)
                        for msg_type, payload in parser.pop_frames():
                            if msg_type == 1:
                                text = payload.decode("utf-8", errors="replace").strip()
                                if text:
                                    self._log(f"[TCP RX {self._peer_tag(ip)}] {text!r}")
                                    # If nodes ever send "HELLO <name>" via TCP, learn it
                                    if text.startswith("HELLO"):
                                        parts = text.split(maxsplit=1)
                                        name = parts[1] if len(parts) > 1 else None
                                        self._learn_peer(ip, name)
                            else:
                                self._log(f"[TCP RX {self._peer_tag(ip)}] frame type={msg_type} len={len(payload)}")
            finally:
                with self.tcp_clients_lock:
                    if self.tcp_clients.get(ip) is conn:
                        self.tcp_clients.pop(ip, None)

            self._log(f"[TCP] Connection closed from {self._peer_tag(ip)}:{port}")

    # ------------------------------------------------------------------
    # Peer tracking + sending (same logic)
    # ------------------------------------------------------------------
    def _learn_peer(self, ip: str, name: str | None = None):
        """Track peers by IP, but prefer showing device name when available.

        If no name has ever been provided, we assign a stable, friendly alias
        (e.g., TinZr-40) so the UI does not show only raw IPs. If a real name
        arrives later (HELLO <name>), it overwrites the alias.
        """
        with self.peers_lock:
            entry = self.peers.get(ip, {"last_seen": time.time(), "name": None})
            entry["last_seen"] = time.time()

            if name is not None and str(name).strip():
                entry["name"] = str(name).strip()
            else:
                if not entry.get("name"):
                    try:
                        last = ip.split(".")[-1]
                        entry["name"] = f"TinZr-{last}"
                    except Exception:
                        entry["name"] = ip

            self.peers[ip] = entry
        self._update_peers_list()


    def _peer_name(self, ip: str) -> str:
        """Return best-known device name for an IP (falls back to the IP string)."""
        with self.peers_lock:
            info = self.peers.get(ip)
            if info and info.get("name"):
                return str(info["name"])
        return ip

    def _peer_tag(self, ip: str) -> str:
        """Short tag used in log lines: prefers name, but keeps IP for clarity."""
        name = self._peer_name(ip)
        return f"{name}<{ip}>" if name and name != ip else ip



    def _peer_tag(self, ip: str) -> str:
        """Short tag used in log lines: prefers name, but keeps IP for clarity."""
        name = self._peer_name(ip)
        return f"{name}<{ip}>" if name and name != ip else ip


    def _update_peers_list(self):
            self.event_queue.put(("peers_updated", None))


    def _tcp_send_text(self, ip: str, text: str) -> bool:
        """Send a text command/event over TCP using DPU type=1. Returns True on success."""
        payload = (text.strip() + "\n").encode("utf-8")
        frame = dpu_encode(1, payload)

        with self.tcp_clients_lock:
            conn = self.tcp_clients.get(ip)

        if conn is None:
            return False

        try:
            conn.sendall(frame)
            return True
        except OSError:
            # drop dead socket
            with self.tcp_clients_lock:
                if self.tcp_clients.get(ip) is conn:
                    self.tcp_clients.pop(ip, None)
            return False

    def on_send_all(self):
            msg = self.send_entry.text().strip()
            if not msg:
                return

            with self.peers_lock:
                targets = list(self.peers.keys())

            if not targets:
                self._log("[SEND] No peers to send to.")
                return

            for ip in targets:
                # Prefer TCP (DPU), fallback to UDP
                if self._tcp_send_text(ip, msg):
                    self._log(f"[SEND] TCP(DPU) -> {self._peer_tag(ip)}:{self.tcp_port} : {msg!r}")
                    continue

                if self.send_sock is None:
                    self._log("[SEND] UDP send socket not available (and TCP not connected).")
                    continue

                try:
                    self.send_sock.sendto(msg.encode("utf-8"), (ip, self.udp_port))
                    self._log(f"[SEND] UDP -> {self._peer_tag(ip)}:{self.udp_port} : {msg!r}")
                except OSError as e:
                    self._log(f"[SEND] Failed to send to {self._peer_tag(ip)}: {e}")

    def on_send_selected(self):
            msg = self.send_entry.text().strip()
            if not msg:
                return

            selected_items = self.peers_list.selectedItems()
            if not selected_items:
                self._log("[SEND] No peer selected.")
                return

            for item in selected_items:
                ip = item.data(QtCore.Qt.UserRole)
                if not ip:
                    continue

                # Prefer TCP (DPU), fallback to UDP
                if self._tcp_send_text(ip, msg):
                    self._log(f"[SEND] TCP(DPU) -> {self._peer_tag(ip)}:{self.tcp_port} : {msg!r}")
                    continue

                if self.send_sock is None:
                    self._log("[SEND] UDP send socket not available (and TCP not connected).")
                    continue

                try:
                    self.send_sock.sendto(msg.encode("utf-8"), (ip, self.udp_port))
                    self._log(f"[SEND] UDP -> {self._peer_tag(ip)}:{self.udp_port} : {msg!r}")
                except OSError as e:
                    self._log(f"[SEND] Failed to send to {self._peer_tag(ip)}: {e}")

    # ------------------------------------------------------------------
    # Queue polling & UI helpers
    # ------------------------------------------------------------------
    def _poll_queues(self):
        # Logs
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.text_log.appendPlainText(msg)
            self.text_log.verticalScrollBar().setValue(
                self.text_log.verticalScrollBar().maximum()
            )

        # Events
        while True:
            try:
                ev, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if ev == "peers_updated":
                self._refresh_peers_list()

    def _refresh_peers_list(self):
        self.peers_list.clear()
        with self.peers_lock:
            for ip, info in sorted(self.peers.items()):
                name = info.get("name") or ip
                display = f"{name} ({ip})" if name and name != ip else ip
                last_seen = info.get("last_seen", 0.0)
                age = time.time() - last_seen
                label = f"{name} [{ip}] ({age:.1f}s ago)"
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, ip)
                self.peers_list.addItem(item)

    def _log(self, msg: str):
        self.log_queue.put(msg)

    def _set_status(self, text: str, color: str = "gray"):
        self.status_label.setText(text)
        # crude color mapping, as in Tk version
        if color == "green":
            css = "color: #56d364;"   # GitHub green-ish
        elif color == "red":
            css = "color: #ff6b6b;"
        else:
            css = "color: gray;"
        self.status_label.setStyleSheet(css)
