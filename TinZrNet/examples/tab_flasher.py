# tab_flasher.py

import os
import sys
import subprocess
import threading
import queue
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
from serial.tools import list_ports

# ==============================
# DEFAULT CONFIG
# ==============================
DEFAULT_SKETCH_PATH = r"C:\Users\lua4006\Desktop\GitRepo\arduino-esp32-tinzr\TinZrNet\examples\ExampleCodeOTA\ExampleCodeOTA.ino"
DEFAULT_FQBN        = "esp32:esp32:esp32c3"
DEFAULT_BAUD        = 115200
DEFAULT_ARDUINO_CLI = r"C:\Users\lua4006\Tools\arduino-cli\arduino-cli.exe"
DEFAULT_VIDS        = "303A,1A86,10C4"   # matches DEFAULT_ALLOWED_VIDS in auto-load.py

DEFAULT_SSID      = "Ludvik"
DEFAULT_PASS      = "Lud12345"
DEFAULT_HOSTNAME  = "TinZr-ota1"

AUTOLOAD_SCRIPT_NAME = "auto-load.py"
# ==============================


def parse_vids_arg(vids_str):
	"""Parse comma-separated VIDs into a set of ints (hex or decimal)."""
	if not vids_str:
		vids_str = DEFAULT_VIDS
	vids = set()
	for part in vids_str.split(","):
		part = part.strip()
		if not part:
			continue
		if part.lower().startswith("0x"):
			val = int(part, 16)
		else:
			try:
				val = int(part, 16)
			except ValueError:
				val = int(part, 10)
		vids.add(val)
	return vids


def get_candidate_tinzr_ports(allowed_vids):
	"""Return list of serial.tools.list_ports.ListPortInfo for matching VIDs."""
	candidates = []
	for info in list_ports.comports():
		if info.vid is None:
			continue
		if allowed_vids and info.vid not in allowed_vids:
			continue
		candidates.append(info)
	return candidates


def split_hostname_base_index(hostname_str):
	"""
	Split hostname into (base, start_index).
	e.g. 'TinZr-ota1' -> ('TinZr-ota', 1)
	     'TinZr-ota'  -> ('TinZr-ota', 1)
	"""
	if not hostname_str:
		return "TinZr-ota", 1

	s = hostname_str.strip()
	i = len(s)
	while i > 0 and s[i-1].isdigit():
		i -= 1

	if i == len(s):
		return s, 1

	base = s[:i]
	idx_str = s[i:]
	try:
		start_idx = int(idx_str)
	except ValueError:
		start_idx = 1
	return base, start_idx


class FlasherTab(ttk.Frame):
	def __init__(self, parent, app):
		super().__init__(parent)
		self.app = app

		self.log_queue = queue.Queue()
		self.worker_thread = None
		self.proc = None

		self.autoload_path = os.path.join(
			os.path.dirname(os.path.abspath(__file__)),
			AUTOLOAD_SCRIPT_NAME
		)

		self.build_ui()
		self.after(100, self.poll_log_queue)

	def build_ui(self):
		# Top config
		frame_top = tk.Frame(self)
		frame_top.pack(fill="x", padx=10, pady=10)

		tk.Label(frame_top, text="Base Sketch (.ino):").grid(row=0, column=0, sticky="w")
		self.sketch_var = tk.StringVar(value=DEFAULT_SKETCH_PATH)
		tk.Entry(frame_top, textvariable=self.sketch_var, width=60).grid(row=0, column=1, sticky="we", padx=5)
		tk.Button(frame_top, text="Browse...", command=self.browse_sketch).grid(row=0, column=2, padx=5)

		tk.Label(frame_top, text="Arduino CLI:").grid(row=1, column=0, sticky="w")
		self.cli_var = tk.StringVar(value=DEFAULT_ARDUINO_CLI)
		tk.Entry(frame_top, textvariable=self.cli_var, width=60).grid(row=1, column=1, sticky="we", padx=5)
		tk.Button(frame_top, text="Browse...", command=self.browse_cli).grid(row=1, column=2, padx=5)

		tk.Label(frame_top, text="FQBN:").grid(row=2, column=0, sticky="w")
		self.fqbn_var = tk.StringVar(value=DEFAULT_FQBN)
		tk.Entry(frame_top, textvariable=self.fqbn_var, width=60).grid(row=2, column=1, sticky="we", padx=5)

		tk.Label(frame_top, text="Baud:").grid(row=3, column=0, sticky="w")
		self.baud_var = tk.StringVar(value=str(DEFAULT_BAUD))
		tk.Entry(frame_top, textvariable=self.baud_var, width=12).grid(row=3, column=1, sticky="w", padx=5)

		tk.Label(frame_top, text="USB VIDs:").grid(row=4, column=0, sticky="w")
		self.vids_var = tk.StringVar(value=DEFAULT_VIDS)
		tk.Entry(frame_top, textvariable=self.vids_var, width=60).grid(row=4, column=1, sticky="we", padx=5)
		tk.Label(frame_top, text="(comma-separated, hex/dec)").grid(row=4, column=2, sticky="w")

		frame_top.columnconfigure(1, weight=1)

		# Wi-Fi / host
		frame_wifi = tk.LabelFrame(self, text="TinZr Wi-Fi / Host Settings")
		frame_wifi.pack(fill="x", padx=10, pady=(0, 10))

		tk.Label(frame_wifi, text="SSID:").grid(row=0, column=0, sticky="w")
		self.ssid_var = tk.StringVar(value=DEFAULT_SSID)
		tk.Entry(frame_wifi, textvariable=self.ssid_var, width=30).grid(row=0, column=1, sticky="w", padx=5)

		tk.Label(frame_wifi, text="Password:").grid(row=1, column=0, sticky="w")
		self.pass_var = tk.StringVar(value=DEFAULT_PASS)
		tk.Entry(frame_wifi, textvariable=self.pass_var, width=30, show="*").grid(row=1, column=1, sticky="w", padx=5)

		tk.Label(frame_wifi, text="Hostname pattern:").grid(row=2, column=0, sticky="w")
		self.host_var = tk.StringVar(value=DEFAULT_HOSTNAME)
		tk.Entry(frame_wifi, textvariable=self.host_var, width=30).grid(row=2, column=1, sticky="w", padx=5)

		tk.Label(
			frame_wifi,
			text="Example: TinZr-ota1 -> boards get TinZr-ota1, TinZr-ota2, TinZr-ota3...",
			fg="gray"
		).grid(row=3, column=0, columnspan=3, sticky="w", pady=(5, 0))

		# Options + buttons
		frame_opts = tk.Frame(self)
		frame_opts.pack(fill="x", padx=10, pady=(0, 10))

		self.auto_var = tk.BooleanVar(value=True)
		tk.Checkbutton(
			frame_opts,
			text="Run auto-load.py in auto mode (no ENTER prompt)",
			variable=self.auto_var
		).pack(anchor="w")

		frame_btns = tk.Frame(self)
		frame_btns.pack(fill="x", padx=10, pady=(0, 10))

		self.run_button = tk.Button(
			frame_btns,
			text="Patch & Flash (Auto-Increment Hostnames)",
			command=self.on_run_clicked
		)
		self.run_button.pack(side="left")

		self.stop_button = tk.Button(
			frame_btns,
			text="Stop",
			command=self.on_stop_clicked,
			state="disabled"
		)
		self.stop_button.pack(side="left", padx=5)

		# Log
		frame_log = tk.Frame(self)
		frame_log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

		tk.Label(frame_log, text="Log:").pack(anchor="w")
		self.text_log = tk.Text(frame_log, height=20, wrap="word", state="disabled")
		self.text_log.pack(fill="both", expand=True)

	# ----- UI helpers -----
	def browse_sketch(self):
		filename = filedialog.askopenfilename(
			title="Select base .ino sketch",
			filetypes=[("Arduino sketch", "*.ino"), ("All files", "*.*")]
		)
		if filename:
			self.sketch_var.set(filename)

	def browse_cli(self):
		filename = filedialog.askopenfilename(
			title="Select arduino-cli.exe",
			filetypes=[("Executable", "*.exe"), ("All files", "*.*")]
		)
		if filename:
			self.cli_var.set(filename)

	def log(self, msg: str):
		self.log_queue.put(str(msg))

	def poll_log_queue(self):
		while not self.log_queue.empty():
			msg = self.log_queue.get()
			self.text_log.config(state="normal")
			self.text_log.insert("end", msg + "\n")
			self.text_log.see("end")
			self.text_log.config(state="disabled")
		self.after(100, self.poll_log_queue)

	# ----- Flashing workflow -----
	def on_run_clicked(self):
		if self.worker_thread is not None and self.worker_thread.is_alive():
			messagebox.showinfo("TinZr Com Center", "Already running. Please wait for the current operation to finish.")
			return

		if not os.path.isfile(self.autoload_path):
			messagebox.showerror("Error", f"auto-load.py not found:\n{self.autoload_path}")
			return

		base_sketch = self.sketch_var.get().strip()
		cli         = self.cli_var.get().strip()
		fqbn        = self.fqbn_var.get().strip()
		baud_str    = self.baud_var.get().strip()
		vids_str    = self.vids_var.get().strip()
		auto_flag   = self.auto_var.get()

		ssid      = self.ssid_var.get().strip()
		wifi_pass = self.pass_var.get().strip()
		host_pat  = self.host_var.get().strip()

		if not os.path.isfile(base_sketch):
			messagebox.showerror("Error", f"Base sketch file not found:\n{base_sketch}")
			return
		if not os.path.isfile(cli):
			messagebox.showerror("Error", f"arduino-cli.exe not found:\n{cli}")
			return
		if not fqbn:
			messagebox.showerror("Error", "FQBN cannot be empty.")
			return
		try:
			int(baud_str)
		except ValueError:
			messagebox.showerror("Error", f"Invalid baud: {baud_str}")
			return
		if not ssid:
			messagebox.showerror("Error", "SSID cannot be empty.")
			return

		allowed_vids = parse_vids_arg(vids_str)
		candidates = get_candidate_tinzr_ports(allowed_vids)
		if not candidates:
			messagebox.showinfo("TinZr Com Center", "No TinZr candidate boards found (by VID filter).")
			return

		base_host, start_idx = split_hostname_base_index(host_pat)

		# Clear log
		self.text_log.config(state="normal")
		self.text_log.delete("1.0", "end")
		self.text_log.config(state="disabled")

		self.log("[INFO] Starting TinZr auto-load with auto-increment hostnames...")
		self.log(f"[INFO] auto-load.py : {self.autoload_path}")
		self.log(f"[INFO] Base sketch  : {base_sketch}")
		self.log(f"[INFO] Arduino CLI  : {cli}")
		self.log(f"[INFO] FQBN         : {fqbn}")
		self.log(f"[INFO] Baud         : {baud_str}")
		self.log(f"[INFO] VIDs         : {', '.join(hex(v) for v in allowed_vids)}")
		self.log(f"[INFO] SSID         : {ssid}")
		self.log(f"[INFO] Host pattern : base='{base_host}', start_index={start_idx}")
		self.log("")
		self.log("[INFO] Candidate ports (order used for hostnames):")
		for i, info in enumerate(candidates, start=0):
			self.log(f"  #{i}  {info.device}  desc={info.description!r}")
		self.log("")

		cmds = []
		devices = []

		for idx, info in enumerate(candidates):
			host_for_port = f"{base_host}{start_idx + idx}"
			patched_path = self.create_patched_sketch(base_sketch, ssid, wifi_pass, host_for_port)

			devices.append({
				"hostname": host_for_port,
				"port": info.device,
				"ssid": ssid,
				"battery": 100,  # placeholder
			})

			self.log(f"[INFO] Port {info.device} will get hostname '{host_for_port}' using sketch: {patched_path}")

			cmd = [
				sys.executable,
				self.autoload_path,
				"--sketch", patched_path,
				"--fqbn", fqbn,
				"--cli", cli,
				"--baud", baud_str,
				"--only-port", info.device,
			]
			if vids_str:
				cmd.extend(["--vids", vids_str])
			if auto_flag:
				cmd.append("--auto")

			cmds.append(cmd)

		# Update devices in the main app (for Devices tab)
		self.app.set_devices(devices)

		self.log("\n[INFO] Commands to run (sequentially):")
		for cmd in cmds:
			self.log("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
		self.log("")

		self.run_button.config(state="disabled")
		self.stop_button.config(state="normal")

		self.worker_thread = threading.Thread(target=self.run_subprocesses, args=(cmds,), daemon=True)
		self.worker_thread.start()

	def create_patched_sketch(self, base_path, ssid, wifi_pass, hostname):
		self.log(f"[INFO] Patching sketch for hostname '{hostname}'...")

		with open(base_path, "r", encoding="utf-8") as f:
			lines = f.readlines()

		def replace_value_in_line(line, field, new_val):
			if field not in line:
				return line
			start = line.find('"')
			end = line.find('"', start + 1)
			if start != -1 and end != -1 and end > start:
				return line[:start+1] + new_val + line[end:]
			return line

		new_lines = []
		for line in lines:
			if ".ssid" in line:
				line = replace_value_in_line(line, ".ssid", ssid)
			if ".pass" in line:
				line = replace_value_in_line(line, ".pass", wifi_pass)
			if ".hostname" in line:
				line = replace_value_in_line(line, ".hostname", hostname)
			new_lines.append(line)

		base_dir = os.path.dirname(base_path)
		base_name = os.path.basename(base_path)
		name_no_ext, ext = os.path.splitext(base_name)
		safe_host = "".join(c if c.isalnum() or c in "-_" else "_" for c in hostname)
		patched_name = f"{name_no_ext}_{safe_host}{ext}"
		patched_path = os.path.join(base_dir, patched_name)

		with open(patched_path, "w", encoding="utf-8") as f:
			f.writelines(new_lines)

		self.log(f"[INFO] Patched sketch written to: {patched_path}")
		return patched_path

	def run_subprocesses(self, cmds):
		try:
			all_ok = True

			for idx, cmd in enumerate(cmds):
				self.log(f"\n[INFO] === Flashing board {idx+1}/{len(cmds)} ===")
				self.proc = subprocess.Popen(
					cmd,
					stdout=subprocess.PIPE,
					stderr=subprocess.STDOUT,
					text=True,
					bufsize=1,
					universal_newlines=True
				)
				for line in self.proc.stdout:
					if line:
						self.log(line.rstrip("\n"))
				self.proc.wait()
				self.log(f"[INFO] auto-load.py exited with code {self.proc.returncode}")

				if self.proc.returncode not in (0,):
					self.log("[WARN] Stopping further flashing due to error.")
					all_ok = False
					break

			if all_ok:
				self.log("\n========================================")
				self.log("  🎉 Congrats! All TinZr boards are loaded.")
				self.log("  They should now be reachable with their")
				self.log("  assigned hostnames (TinZr-ota1,2,3,...).")
				self.log("========================================\n")

		except Exception as e:
			self.log(f"[ERROR] Failed to run auto-load.py: {e}")
		finally:
			self.proc = None
			self.after(0, self.on_subprocess_finished)

	def on_subprocess_finished(self):
		self.run_button.config(state="normal")
		self.stop_button.config(state="disabled")

	def on_stop_clicked(self):
		if self.proc is not None:
			try:
				self.log("[INFO] Terminating current auto-load.py process...")
				self.proc.terminate()
			except Exception as e:
				self.log(f"[ERROR] Failed to terminate process: {e}")
		else:
			self.log("[INFO] No running process to stop.")
