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
 * Each recording is saved as a CSV file in the configured SD folder.
 * Files are named sequentially as 1.csv, 2.csv, 3.csv, and so on.
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
	.sd_log_dir         = "/TinZrLogs"
};

void setup() {
	TinZrWearableSD_.begin(cfg);
	delay(10);
}

void loop() {
	TinZrWearableSD_.handle();
}
