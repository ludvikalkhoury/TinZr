from pathlib import Path
import subprocess
import sys


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    examples_dir = project_dir.parent
    icon_path = project_dir / "TinZr_small_logo.ico"
    entry_script = project_dir / "TinZrWearableSD.py"

    command = [
        "pyinstaller",
        "--noconsole",
        "--onefile",
        "--icon",
        str(icon_path),
        "--paths",
        str(examples_dir),
        "--hidden-import",
        "GUIsHelper",
        "--exclude-module",
        "tensorflow",
        "--exclude-module",
        "torch",
        "--exclude-module",
        "torchvision",
        "--exclude-module",
        "torchaudio",
        "--exclude-module",
        "keras",
        str(entry_script),
    ]

    print("Running:", " ".join(f'"{arg}"' if " " in arg else arg for arg in command))
    result = subprocess.run(command, cwd=project_dir)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
