from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt


DEFAULT_FILES = [
    Path(r"TinZrFilename1.csv"),
    Path(r"TinZrFilename2.csv"),
]


@dataclass
class AxSeries:
    path: Path
    device_name: str
    start_time: datetime
    elapsed_ms: list[float]
    ax_g: list[float]


def parse_header_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H-%M-%S-%f")


def read_ax_series(path: Path) -> AxSeries:
    device_name = path.stem
    start_time: datetime | None = None
    data_lines: list[str] = []

    with path.open("r", encoding="utf-8", newline="") as handle:
        for line in handle:
            if line.startswith("#"):
                header_line = line[1:].strip()
                if header_line.startswith("device_name:"):
                    device_name = header_line.split(":", 1)[1].strip()
                elif header_line.startswith("pc_start_timestamp:"):
                    timestamp_text = header_line.split(":", 1)[1].strip()
                    start_time = parse_header_timestamp(timestamp_text)
                continue

            if line.strip():
                data_lines.append(line)

    if start_time is None:
        raise ValueError(f"{path} does not contain a pc_start_timestamp header.")

    elapsed_ms: list[float] = []
    ax_g: list[float] = []
    reader = csv.DictReader(data_lines)

    if "t_ms" not in (reader.fieldnames or []) or "ax_g" not in (reader.fieldnames or []):
        raise ValueError(f"{path} must contain t_ms and ax_g columns.")

    for row in reader:
        try:
            elapsed_ms.append(float(row["t_ms"]))
            ax_g.append(float(row["ax_g"]))
        except (TypeError, ValueError):
            continue

    return AxSeries(path=path, device_name=device_name, start_time=start_time, elapsed_ms=elapsed_ms, ax_g=ax_g)


def plot_aligned_ax(files: list[Path]) -> None:
    series_list = [read_ax_series(path) for path in files]
    if not series_list:
        raise ValueError("No CSV files were provided.")

    reference_start = min(series.start_time for series in series_list)
    colors = ["red", "blue"]

    figure, axis = plt.subplots(figsize=(12, 6))
    for index, series in enumerate(series_list):
        start_offset_seconds = (series.start_time - reference_start).total_seconds()
        aligned_seconds = [start_offset_seconds + (time_ms / 1000.0) for time_ms in series.elapsed_ms]
        axis.plot(
            aligned_seconds,
            series.ax_g,
            color=colors[index % len(colors)],
            linewidth=1.0,
            label=f"{series.device_name} ax_g",
        )

    axis.set_title("TinZr aligned ax_g")
    axis.set_xlabel(f"Time since {reference_start.isoformat(timespec='microseconds')} (s)")
    axis.set_ylabel("ax_g")
    axis.grid(True, alpha=0.3)
    axis.legend()
    figure.tight_layout()
    plt.show()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot aligned TinZr ax_g values from two CSV files.")
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        default=DEFAULT_FILES,
        help="CSV files to plot. Defaults to the two TinZr CSV files on the Desktop.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plot_aligned_ax(args.files)


if __name__ == "__main__":
    main()
