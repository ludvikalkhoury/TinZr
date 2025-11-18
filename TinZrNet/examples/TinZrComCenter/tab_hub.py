import socket
import threading
import queue
import time
import struct
import tkinter as tk
from tkinter import ttk, messagebox

DEFAULT_UDP_PORT   = 4210
DEFAULT_TCP_PORT   = 4211
DEFAULT_MCAST_GRP  = "239.1.1.1"


class HubTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        # Networking / hub state
        self.running      = False
        self.udp_thread   = None
        self.tcp_thread   = None
        self.udp_sock     = None
        self.tcp_sock     = None
        self.send_sock    = None

        # ip -> {"last_seen": float}
        self.peers        = {}
        self.peers_lock   = threading.Lock()

        # Queues for thread-safe UI updates
        self.log_queue    = queue.Queue()
        self.event_queue  = queue.Queue()

        self.build_ui()
        self.after(100, self._poll_queues)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def build_ui(self):
        top = tk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)

        # Left side: controls
        left = tk.Frame(top)
        left.pack(side="left", fill="x", expand=True)

        tk.Label(left, text="TinZr Hub (PC as Router)", font=("Segoe UI", 11, "bold")).pack(anchor="w")

        tk.Label(
            left,
            text=(
                "This PC will:\n"
                "• Listen for TinZr UDP HELLO / STATUS / BTN messages\n"
                "• Optionally log any TCP messages from TinZr\n"
                "• Track TinZr peers by IP\n"
                "• Let you send commands (LED/OFF/PING) over UDP"
            ),
            justify="left", fg="gray"
        ).pack(anchor="w", pady=(2, 5))

        grid = tk.Frame(left)
        grid.pack(anchor="w", pady=(4, 4))

        tk.Label(grid, text="UDP port:").grid(row=0, column=0, sticky="w")
        self.udp_port_var = tk.StringVar(value=str(DEFAULT_UDP_PORT))
        tk.Entry(grid, textvariable=self.udp_port_var, width=8).grid(row=0, column=1, sticky="w", padx=4)

        tk.Label(grid, text="TCP port:").grid(row=1, column=0, sticky="w")
        self.tcp_port_var = tk.StringVar(value=str(DEFAULT_TCP_PORT))
        tk.Entry(grid, textvariable=self.tcp_port_var, width=8).grid(row=1, column=1, sticky="w", padx=4)

        tk.Label(grid, text="Multicast group:").grid(row=2, column=0, sticky="w")
        self.mcast_var = tk.StringVar(value=DEFAULT_MCAST_GRP)
        tk.Entry(grid, textvariable=self.mcast_var, width=16).grid(row=2, column=1, sticky="w", padx=4)

        # Kept for compatibility; not used in this simplified UDP-first hub
        self.relay_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            left,
            text="Relay TinZr→TinZr messages via hub (TCP; optional)",
            variable=self.relay_var
        ).pack(anchor="w", pady=(4, 0))

        btn_row = tk.Frame(left)
        btn_row.pack(anchor="w", pady=(8, 0))

        self.start_button = tk.Button(btn_row, text="Start Hub", command=self.on_start_hub)
        self.start_button.pack(side="left", padx=(0, 5))

        self.stop_button = tk.Button(btn_row, text="Stop", command=self.on_stop_hub, state="disabled")
        self.stop_button.pack(side="left")

        self.status_label = tk.Label(left, text="Hub stopped", fg="gray")
        self.status_label.pack(anchor="w", pady=(4, 0))

        # Right side: peer list
        right = tk.Frame(top)
        right.pack(side="right", fill="y", padx=(20, 0))

        tk.Label(right, text="Known TinZr Peers:").pack(anchor="w")
        self.peers_list = tk.Listbox(right, height=10, width=30)
        self.peers_list.pack(fill="y", expand=False)

        # Bottom: log + send area
        bottom = tk.Frame(self)
        bottom.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Log
        log_frame = tk.Frame(bottom)
        log_frame.pack(fill="both", expand=True)

        tk.Label(log_frame, text="Hub Log:").pack(anchor="w")
        self.text_log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.text_log.pack(fill="both", expand=True)

        # Send area
        send_frame = tk.Frame(self)
        send_frame.pack(fill="x", padx=10, pady=(0, 10))

        tk.Label(send_frame, text="Send message (e.g. 'LED 0 255 0 20', 'OFF', 'PING'):").pack(anchor="w")
        self.send_entry = tk.Entry(send_frame)
        self.send_entry.pack(side="left", fill="x", expand=True)
        self.send_entry.bind("<Return>", lambda e: self.on_send_all())

        tk.Button(send_frame, text="Send to all", command=self.on_send_all).pack(side="left", padx=4)
        tk.Button(send_frame, text="Send to selected", command=self.on_send_selected).pack(side="left", padx=4)

    # ------------------------------------------------------------------
    # Hub lifecycle
    # ------------------------------------------------------------------
    def on_start_hub(self):
        if self.running:
            return

        try:
            udp_port = int(self.udp_port_var.get().strip())
            tcp_port = int(self.tcp_port_var.get().strip())
        except ValueError:
            messagebox.showerror("TinZr Com Center", "UDP and TCP ports must be integers.")
            return

        mcast = self.mcast_var.get().strip()
        if not mcast:
            messagebox.showerror("TinZr Com Center", "Multicast group cannot be empty.")
            return

        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.mcast_group = mcast

        self.running = True
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self._set_status("Hub running (listening on UDP + TCP)...", "green")
        self._log(f"[HUB] Starting (UDP {udp_port}, TCP {tcp_port}, MCAST {mcast})")

        # UDP listen+multicast join
        self.udp_thread = threading.Thread(target=self._udp_listener_thread, daemon=True)
        self.udp_thread.start()

        # TCP listener for any TinZr TCP messages (optional but nice)
        self.tcp_thread = threading.Thread(target=self._tcp_listener_thread, daemon=True)
        self.tcp_thread.start()

        # UDP send socket
        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def on_stop_hub(self):
        if not self.running:
            return
        self._log("[HUB] Stopping hub and closing sockets...")
        self.running = False

        # Close sockets to unblock threads
        try:
            if self.udp_sock is not None:
                self.udp_sock.close()
        except Exception:
            pass
        self.udp_sock = None

        try:
            if self.tcp_sock is not None:
                self.tcp_sock.close()
        except Exception:
            pass
        self.tcp_sock = None

        try:
            if self.send_sock is not None:
                self.send_sock.close()
        except Exception:
            pass
        self.send_sock = None

        with self.peers_lock:
            self.peers.clear()
        self._update_peers_list()

        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self._set_status("Hub stopped", "gray")

    # ------------------------------------------------------------------
    # UDP listener (HELLO / STATUS / BTN / etc.)
    # ------------------------------------------------------------------
    def _udp_listener_thread(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)

        try:
            sock.bind(("", self.udp_port))
        except OSError as e:
            self._log(f"[UDP] ERROR binding UDP port {self.udp_port}: {e}")
            self._set_status(f"UDP bind error: {e}", "red")
            return

        try:
            mreq = struct.pack("4sl", socket.inet_aton(self.mcast_group), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            self._log(f"[UDP] Joined multicast group {self.mcast_group}")
        except OSError as e:
            self._log(f"[UDP] WARNING: Failed to join multicast group {self.mcast_group}: {e}")

        self.udp_sock = sock
        self._log(f"[UDP] Listening on 0.0.0.0:{self.udp_port}")

        while self.running:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            ip, port = addr
            text = data.decode("utf-8", errors="replace").strip()
            self._log(f"[UDP RX] from {ip}:{port} -> {text!r}")

            # Learn/update peer
            self._learn_peer(ip)

            # Optionally respond to HELLO like a classic hub
            if text == "HELLO":
                try:
                    sock.sendto(b"HUB-ACK", addr)
                    self._log(f"[UDP]  -> Sent HUB-ACK to {ip}:{port}")
                except OSError as e:
                    self._log(f"[UDP]  -> Failed to send HUB-ACK to {ip}: {e}")

        try:
            sock.close()
        except Exception:
            pass
        self._log("[UDP] Listener stopped.")

    # ------------------------------------------------------------------
    # TCP listener (log any TinZr-initiated TCP messages)
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
            self._log(f"[TCP] Connection from {ip}:{port}")
            threading.Thread(
                target=self._handle_tcp_client,
                args=(conn, addr),
                daemon=True
            ).start()

        try:
            srv.close()
        except Exception:
            pass
        self._log("[TCP] Listener stopped.")

    def _handle_tcp_client(self, conn, addr):
        ip, port = addr
        self._learn_peer(ip)

        with conn:
            while self.running:
                try:
                    data = conn.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", errors="replace").strip()
                self._log(f"[TCP RX {ip}] {text!r}")
                self._learn_peer(ip)

        self._log(f"[TCP] Connection closed from {ip}:{port}")

    # ------------------------------------------------------------------
    # Peer tracking + sending
    # ------------------------------------------------------------------
    def _learn_peer(self, ip: str):
        with self.peers_lock:
            self.peers[ip] = {"last_seen": time.time()}
        self._update_peers_list()

    def _send_udp_to_ip(self, ip: str, data: bytes):
        if self.send_sock is None:
            self._log("[SEND] UDP send socket not ready.")
            return
        try:
            self.send_sock.sendto(data, (ip, self.udp_port))
        except OSError as e:
            self._log(f"[SEND] Failed to send to {ip}: {e}")

    def on_send_all(self):
        text = self.send_entry.get().strip()
        if not text:
            return
        data = (text + "\n").encode("utf-8")
        with self.peers_lock:
            ips = list(self.peers.keys())
        for ip in ips:
            self._send_udp_to_ip(ip, data)
        self._log(f"[PC→ALL/UDP] {data!r}")

    def on_send_selected(self):
        sel = self.peers_list.curselection()
        if not sel:
            return
        idx = sel[0]
        with self.peers_lock:
            ips = list(self.peers.keys())
        if idx < 0 or idx >= len(ips):
            return
        ip = ips[idx]
        text = self.send_entry.get().strip()
        if not text:
            return
        data = (text + "\n").encode("utf-8")
        self._send_udp_to_ip(ip, data)
        self._log(f"[PC→{ip}/UDP] {data!r}")

    # ------------------------------------------------------------------
    # UI update helpers (thread-safe via queues)
    # ------------------------------------------------------------------
    def _log(self, msg: str):
        self.log_queue.put(str(msg))

    def _set_status(self, text: str, color: str):
        self.event_queue.put(("status", text, color))

    def _update_peers_list(self):
        self.event_queue.put(("peers", None, None))

    def _poll_queues(self):
        # Logs
        while not self.log_queue.empty():
            msg = self.log_queue.get()
            self.text_log.config(state="normal")
            self.text_log.insert("end", msg + "\n")
            self.text_log.see("end")
            self.text_log.config(state="disabled")

        # Events
        while not self.event_queue.empty():
            ev = self.event_queue.get()
            kind = ev[0]
            if kind == "status":
                text, color = ev[1], ev[2]
                self.status_label.config(text=text, fg=color)
            elif kind == "peers":
                self.peers_list.delete(0, "end")
                with self.peers_lock:
                    for ip, info in self.peers.items():
                        age = time.time() - info["last_seen"]
                        label = f"{ip}  (seen {age:0.1f}s ago)"
                        self.peers_list.insert("end", label)

        self.after(100, self._poll_queues)
