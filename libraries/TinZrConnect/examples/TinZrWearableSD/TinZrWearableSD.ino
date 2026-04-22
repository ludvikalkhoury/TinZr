/*
 * ================================================================
 *  TinZr Wearable BLE Control with saving data to SD card 
 * ================================================================
 *
 * This Arduino sketch demonstrates the simplest possible usage of
 * the TinZrWearableSD module to enable high-rate data logging to
 * a microSD card on the TinZr platform. 
 *
 * The main control (stop/start) is done with a Python-based GUI
 * that communicates with the TinZr over BLE. 
 *
 * ---------------------------------------------------------------
 * Features
 * ---------------------------------------------------------------
 *  - Initializes the TinZr wearable SD logging subsystem
 *  - Configures device hostname and SD log directory
 *  - Supports high-rate periodic sampling (~250 Hz)
 *  - Automatically manages file creation and data flushing
 *  - Minimal user code required (begin + handle)
 *
 * ---------------------------------------------------------------
 * Behavior
 * ---------------------------------------------------------------
 * 1. On startup:
 *    - TinZrWearableSD is initialized using a configuration struct
 *    - The SD card is mounted and validated
 *    - A logging directory is created if it does not already exist
 *    - A new log file is prepared using the device hostname and
 *      timestamp-based naming
 *
 * 2. Runtime operation:
 *    - TinZrWearableSD.handle() is called repeatedly in loop()
 *    - Internally:
 *        • Sensors are sampled at the configured interval
 *        • Data is periodically written to SD
 *        • File integrity and write timing are managed automatically
 *
 * ---------------------------------------------------------------
 * Configuration Parameters
 * ---------------------------------------------------------------
 *  - hostname
 *      Logical device name used for file naming and identification
 *
 *  - sample_interval_ms
 *      Sampling period in milliseconds
 *      (4 ms ≈ 250 Hz effective sample rate)
 *
 *  - sd_log_dir
 *      Directory path on the SD card where log files are stored
 *
 * ---------------------------------------------------------------
 * System Timing
 * ---------------------------------------------------------------
 *  - Sampling and logging are driven by non-blocking timing logic
 *  - No delay is required in loop()
 *  - User code remains responsive and cooperative
 *
 * ---------------------------------------------------------------
 * Dependencies
 * ---------------------------------------------------------------
 * - TinZrWearableSD
 *     Provides:
 *       - SD card initialization and mounting
 *       - High-rate buffered data logging
 *       - Automatic file management
 *       - Non-blocking handle-based execution model
 *
 * - Arduino Core
 *     Provides:
 *       - Main application structure (setup / loop)
 *       - Timing primitives
 *
 * ---------------------------------------------------------------
 * Notes
 * ---------------------------------------------------------------
 * - This example intentionally contains no application logic
 * - All logging behavior is encapsulated within TinZrWearableSD
 * - Logged data persists across power cycles (SD-based storage)
 * - Ideal for:
 *     • Bring-up testing
 *     • Long-duration data acquisition
 *     • Wearable validation and field recording
 *
 * TinZr Platform SD Logging Example with Main Control over
 * BLE with a Python-based GUI
 * ================================================================
 */


// !!!!!!!!!!!!
// !!!!NOTE!!!!
// !!!!!!!!!!!!
// Before you use this code, keep in mind that there could be of data drift and de-synchornization since different TinZr do not run at the same clock. 
// If this is a limitation, then consider using TinZrWearable.ino instead (which sends data over BLE to a computer).


#include <Arduino.h>
#include "TinZrWearableSD.h"

TinZrWearableSDConfig cfg = {
	.hostname                         = "TinZr2", 
	.sample_interval_ms               = 4,      // ≈ 250 Hz
	.enable_pc_clock_drift_correction = true,
	.sd_log_dir                       = "/TinZrLogs"
};

void setup() {
	TinZrWearableSD.begin(cfg);
	delay(10);
}

void loop() {
	TinZrWearableSD.handle();
}
