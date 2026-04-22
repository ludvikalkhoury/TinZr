from __future__ import annotations

import numpy as np
from scipy.interpolate import PchipInterpolator, interp1d


def warp_signal(time, signal, speed_factor, method="pchip"):
    """
    Stretch or condense a signal in time while keeping the original sampling interval.

    Parameters
    ----------
    time : array-like
        Time values for the signal.
    signal : array-like
        Signal values.
    speed_factor : float
        > 1.0 makes the signal faster and condenses it in time.
        < 1.0 makes the signal slower and stretches it in time.
    method : str
        Interpolation method used during warping. Supported values are
        ``"linear"`` and ``"pchip"``.

    Returns
    -------
    new_time : np.ndarray
        Time axis with the same sampling interval as the input and a shorter
        or longer duration depending on ``speed_factor``.
    warped_signal : np.ndarray
        Time-warped signal values sampled on ``new_time``.
    """
    signal = np.asarray(signal, dtype=float)
    time = np.asarray(time, dtype=float)

    if signal.ndim != 1 or time.ndim != 1:
        raise ValueError("signal and time must be 1D arrays")
    if len(signal) != len(time):
        raise ValueError("signal and time must have the same length")
    if len(signal) < 2:
        raise ValueError("signal and time must contain at least two samples")
    if speed_factor <= 0:
        raise ValueError("speed_factor must be > 0")
    if np.any(np.diff(time) <= 0):
        raise ValueError("time must be strictly increasing")
    if method not in {"linear", "pchip"}:
        raise ValueError("method must be 'linear' or 'pchip'")

    time0 = time[0]
    time_diffs = np.diff(time)
    dt = float(time_diffs[0])
    if not np.allclose(time_diffs, dt, rtol=1e-6, atol=1e-9):
        raise ValueError("time must have a uniform sampling interval")
    old_duration = float(time[-1] - time0)
    new_duration = old_duration / speed_factor
    sample_count = max(2, int(np.round(new_duration / dt)) + 1)
    new_time = time0 + np.arange(sample_count, dtype=float) * dt
    source_time = time0 + (new_time - time0) * speed_factor

    if method == "linear":
        interpolator = interp1d(
            time,
            signal,
            kind="linear",
            bounds_error=False,
            fill_value=(signal[0], signal[-1]),
        )
    else:
        interpolator = PchipInterpolator(time, signal, extrapolate=True)

    source_time = np.clip(source_time, time[0], time[-1])
    warped_signal = interpolator(source_time)
    return new_time, np.asarray(warped_signal, dtype=float)
