/*
 * ================================================================
 *  TinZr Wearable SD Logger
 * ================================================================
 *
 * This example records TinZr wearable sensor data directly to a
 * microSD card. The onboard push button toggles recording on and off:
 *
 *   - Red LED:    stopped / ready
 *   - Green LED:  recording
 *
 * BLE is used only as an optional remote start trigger while the device
 * is idle. The GUI may send a device name and one PC timestamp before
 * sending "S" or "START". These values are saved in the CSV header, and
 * the subject, device name, and PC timestamp are included in the file
 * name. After recording starts, BLE is stopped and cannot stop the
 * recording. The only stop control is the onboard button.
 *
 * Each recording is saved as a CSV file in the configured SD folder.
 * GUI-started recordings use names like:
 *   sub-001_device-TinZrBlue_2026-03-13T12-53-32-411123.csv
 * Button-only recordings use sub-unknown.
 *
 * ---------------------------------------------------------------
 * Configuration
 * ---------------------------------------------------------------
 * sample_interval_ms
 *   Sampling period in milliseconds.
 *   The default value of 4 ms targets 250 Hz.
 *
 * sd_log_dir
 *   Directory on the SD card where CSV log files are saved.
 *   The folder is created automatically if it does not exist.
 *
 * hostname
 *   Default TinZr name used in CSV headers and file names. The GUI can
 *   override this at the start command for GUI-started recordings.
 *
 * ---------------------------------------------------------------
 * Logged Columns
 * ---------------------------------------------------------------
 * t_ms, red_nA, ir_nA, ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps
 *
 * The IMU is required. The MAX30102 PPG sensor is optional; if it is
 * not detected, red_nA and ir_nA values are logged as zero. PPG values
 * are approximate photodiode current in nanoamps. Accelerometer values
 * are logged in g, and gyroscope values are logged in degrees per second.
 *
 * Keep setup() and loop() small. The implementation lives in
 * TinZrWearableSD_.h / TinZrWearableSD_.cpp so this file only exposes
 * user-facing configuration.
 * ================================================================
 */

#include "TinZrWearableSD_.h"

TinZrWearableSDConfig cfg = {
	.sample_interval_ms = 4,      // 250 Hz
	.sd_log_dir         = "/TinZrLogs",
	.hostname           = "TinZrBlacl"
};

void setup() {
	TinZrWearableSD_.begin(cfg);
	delay(10);
}

void loop() {
	TinZrWearableSD_.handle();
}
