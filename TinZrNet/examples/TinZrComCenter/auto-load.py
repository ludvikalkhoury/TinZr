import subprocess
import serial
import time
from serial.tools import list_ports
import argparse
import os
import sys

# ==============================
# DEFAULT CONFIG (can be overridden by CLI args)
# ==============================
DEFAULT_SKETCH_PATH = r"C:\Users\lua4006\Desktop\GitRepo\arduino-esp32-tinzr\TinZrNet\examples\ExampleCodeOTA\ExampleCodeOTA.ino"
DEFAULT_FQBN        = "esp32:esp32:esp32c3"
DEFAULT_BAUD        = 115200
DEFAULT_ARDUINO_CLI = r"C:\Users\lua4006\Tools\arduino-cli\arduino-cli.exe"

# Common USB UART / ESP32 VIDs:
#  - 0x303A = Espressif
#  - 0x1A86 = CH340
#  - 0x10C4 = Silicon Labs CP210x
DEFAULT_ALLOWED_VIDS = {0x303A, 0x1A86, 0x10C4}

OPEN_SLEEP        = 0.15
READ_TIMEOUT      = 0.3
AFTER_WRITE_SLEEP = 0.15
# ==============================


def print_all_ports():
    print("[INFO] All detected serial ports:")
    for info in list_ports.comports():
        print(f"  {info.device:6}  desc={info.description!r}  vid={info.vid}  pid={info.pid}  hwid={info.hwid!r}")
    print("")


def get_candidate_tinzr_ports(allowed_vids):
    """
    Filter COM ports to only USB serial devices with allowed VID.
    This avoids hanging on weird system ports like COM4.
    """
    candidates = []
    for info in list_ports.comports():
        if info.vid is None:
            # Likely built-in / virtual / Bluetooth etc. Skip.
            continue
        if allowed_vids and info.vid not in allowed_vids:
            continue
        candidates.append(info)
    return candidates


def probe_show_signature(port_name, baud):
    """
    Try to talk to TinZrConsole on this port via SHOW.
    Returns True if it *responds like* TinZrConsole, else False.
    """
    print(f"    -> Probing {port_name} with SHOW ... ", end="", flush=True)
    try:
        with serial.Serial(port_name, baud, timeout=READ_TIMEOUT) as ser:
            time.sleep(OPEN_SLEEP)
            ser.reset_input_buffer()
            ser.write(b"SHOW\n")
            time.sleep(AFTER_WRITE_SLEEP)
            data = ser.read(512).decode(errors="ignore")
            if "TinZr" in data or "esp32c3-ota" in data:
                print("TinZrConsole ✅")
                return True
            else:
                print("no TinZr response")
                return False
    except Exception as e:
        print(f"error: {e}")
        return False


def upload_to_port(port_name, sketch_path, fqbn, arduino_cli):
    print(f"\n[UPLOAD] Flashing sketch to {port_name} ...")
    cmd = [
        arduino_cli, "upload",
        "--fqbn", fqbn,
        "-p", port_name,
        sketch_path,
    ]
    print("  Running:", " ".join(cmd))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("[ERROR] arduino-cli not found. Check the Arduino CLI path.")
        return
    except Exception as e:
        print(f"[ERROR] Failed to run arduino-cli: {e}")
        return

    if result.returncode == 0:
        print(f"[SUCCESS] Uploaded to {port_name}")
        if result.stdout.strip():
            print("----- STDOUT -----")
            print(result.stdout.strip())
    else:
        print(f"[ERROR] FAILED on {port_name}")
        if result.stdout.strip():
            print("----- STDOUT -----")
            print(result.stdout.strip())
        if result.stderr.strip():
            print("----- STDERR -----")
            print(result.stderr.strip())


def parse_vids_arg(vids_str):
    """
    Parse a comma-separated list of hex or decimal VIDs into a set of ints.
    Example: "303A,1A86" or "0x303A,0x1A86"
    """
    if not vids_str:
        return DEFAULT_ALLOWED_VIDS
    vids = set()
    for part in vids_str.split(","):
        part = part.strip()
        if not part:
            continue
        # allow "303A" or "0x303A"
        if part.lower().startswith("0x"):
            val = int(part, 16)
        else:
            # try hex first, then decimal
            try:
                val = int(part, 16)
            except ValueError:
                val = int(part, 10)
        vids.add(val)
    return vids


def main():
    parser = argparse.ArgumentParser(
        description="Auto-load a sketch onto TinZr (ESP32) boards detected over USB."
    )
    parser.add_argument(
        "--sketch", "-s",
        default=DEFAULT_SKETCH_PATH,
        help=f"Path to .ino sketch file (default: {DEFAULT_SKETCH_PATH})"
    )
    parser.add_argument(
        "--fqbn", "-b",
        default=DEFAULT_FQBN,
        help=f"Fully Qualified Board Name, e.g. esp32:esp32:esp32c3 (default: {DEFAULT_FQBN})"
    )
    parser.add_argument(
        "--cli", "-c",
        dest="arduino_cli",
        default=DEFAULT_ARDUINO_CLI,
        help=f"Path to arduino-cli.exe (default: {DEFAULT_ARDUINO_CLI})"
    )
    parser.add_argument(
        "--baud", "-B",
        type=int,
        default=DEFAULT_BAUD,
        help=f"Serial baud rate used for SHOW probe (default: {DEFAULT_BAUD})"
    )
    parser.add_argument(
        "--vids",
        type=str,
        default="",
        help="Comma-separated USB VIDs to treat as TinZr hardware, e.g. '303A,1A86'. "
             "If omitted, uses internal defaults."
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run without interactive ENTER confirmation (for GUI / scripts)."
    )
    parser.add_argument(
        "--only-port",
        type=str,
        default="",
        help="If set, only flash this COM port (must still match VID filter)."
    )

    args = parser.parse_args()

    sketch_path  = args.sketch
    fqbn         = args.fqbn
    arduino_cli  = args.arduino_cli
    baud         = args.baud
    allowed_vids = parse_vids_arg(args.vids)
    only_port    = args.only_port.strip()

    # Basic checks
    if not os.path.isfile(sketch_path):
        print(f"[ERROR] Sketch file not found: {sketch_path}")
        sys.exit(1)
    if not os.path.isfile(arduino_cli):
        print(f"[ERROR] arduino-cli.exe not found: {arduino_cli}")
        sys.exit(1)

    print("========== TinZr Auto-Loader ==========\n")
    print(f"[INFO] Sketch: {sketch_path}")
    print(f"[INFO] FQBN:   {fqbn}")
    print(f"[INFO] CLI:    {arduino_cli}")
    print(f"[INFO] Baud:   {baud}")
    print(f"[INFO] VIDs:   {', '.join(hex(v) for v in allowed_vids)}")
    if only_port:
        print(f"[INFO] Only-port mode: {only_port}")
    print("")

    print_all_ports()

    # 1) Hardware-level detection: USB serial with allowed VID
    candidate_infos = get_candidate_tinzr_ports(allowed_vids)
    if not candidate_infos:
        print("[INFO] No USB serial ports matching allowed VIDs found.")
        return

    if only_port:
        candidate_infos = [info for info in candidate_infos if info.device == only_port]
        if not candidate_infos:
            print(f"[ERROR] Requested port {only_port} not found among candidate TinZr ports.")
            return

    print("[INFO] Candidate TinZr *hardware* ports (by VID):")
    for info in candidate_infos:
        print(f"  - {info.device}  (desc={info.description!r}, vid={info.vid}, pid={info.pid})")
    print("")

    # 2) Optional: check which of these are already running TinZrConsole
    print("[INFO] Checking which candidates respond to SHOW (TinZrConsole):")
    tinzr_console_ports = []
    for info in candidate_infos:
        if probe_show_signature(info.device, baud):
            tinzr_console_ports.append(info.device)

    print("\n====================================")
    print("  SUMMARY")
    print("====================================")
    print("  Candidate TinZr hardware ports (will be flashed):")
    for info in candidate_infos:
        print(f"    - {info.device}")
    if tinzr_console_ports:
        print("\n  Ports already running TinZrConsole (SHOW OK):")
        for p in tinzr_console_ports:
            print(f"    - {p}")
    else:
        print("\n  No ports responded with TinZrConsole signature (maybe old firmware).")
    print("====================================\n")

    if not args.auto:
        input("Press ENTER to upload the sketch to ALL candidate TinZr hardware ports...")

    # 3) Flash the SAME sketch to all candidate TinZr ports
    for info in candidate_infos:
        upload_to_port(info.device, sketch_path, fqbn, arduino_cli)
        time.sleep(1.0)


if __name__ == "__main__":
    main()
