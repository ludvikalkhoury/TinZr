/*
 * ================================================================
 *  TinZr IMU → SD Card Logger (Button-Controlled)
 * ================================================================
 *
 * This Arduino sketch demonstrates synchronized acquisition of
 * inertial measurement unit (IMU) data on the TinZr platform and
 * logging the data to an SD card using a button-controlled workflow.
 *
 * ---------------------------------------------------------------
 * Features
 * ---------------------------------------------------------------
 *  - Initializes the TinZr core system (LED, button, power logic)
 *  - Initializes the onboard IMU (accelerometer + gyroscope)
 *  - Mounts an SD card and prepares a logging directory
 *  - Uses the onboard push button to toggle recording ON / OFF
 *  - Logs IMU data to a CSV file on the SD card
 *  - Provides visual feedback via the RGB status LED
 *
 * ---------------------------------------------------------------
 * Behavior
 * ---------------------------------------------------------------
 * 1. On startup:
 *    - TinZr core and IMU are initialized
 *    - SD card is mounted (if present)
 *    - Status LED flashes green when SD is detected
 *
 * 2. Button interaction:
 *    - First button press:
 *        • Starts a new recording session
 *        • Creates a uniquely named CSV file on the SD card
 *        • Sets the status LED to solid green
 *        • Time stamps reset so the first sample is t = 0 ms
 *
 *    - Second button press:
 *        • Stops the recording session
 *        • Flushes and closes the SD file
 *        • Returns the status LED to flashing green (SD present)
 *
 * 3. While recording:
 *    - IMU data is sampled at ~250 Hz
 *    - Each sample is written as a CSV row:
 *
 *        t_ms, ax, ay, az, gx, gy, gz
 *
 * ---------------------------------------------------------------
 * LED Status Indications
 * ---------------------------------------------------------------
 *  - Flashing green:
 *      SD card present, idle (not recording)
 *
 *  - Solid green:
 *      Actively recording IMU data to SD card
 *
 *  - Flashing red:
 *      SD card not present or SD mount failure
 *
 * ---------------------------------------------------------------
 * Dependencies
 * ---------------------------------------------------------------
 * - TinZrCore
 *     Provides:
 *       - Global TinZrCore object: `TinZr`
 *       - Button handling
 *       - LED state management
 *       - IMU initialization and raw data acquisition
 *
 * - TinZrSD (TinZrSDLogger)
 *     Provides:
 *       - Global TinZrSDLogger object: `TinZrSD`
 *       - SD card mounting and file management
 *       - Automatic filename incrementation
 *       - Background LED state updates based on SD / recording state
 *
 * ---------------------------------------------------------------
 * Notes
 * ---------------------------------------------------------------
 * - IMU values are logged in calibrated physical units
 *   (accelerometer in g, gyroscope in degrees/second),
 *   converted from raw sensor counts using datasheet-defined
 *   scale factors based on the configured full-scale ranges.
 * - CSV files are compatible with Excel, MATLAB, and Python
 * - Time stamps are relative to the start of each recording session
 * - LED animations are non-blocking and driven in the main loop
 *
 * This sketch is intended for:
 *   - Sensor validation
 *   - Data collection
 *   - System bring-up and testing
 *
 * TinZr Platform — SD + IMU Logging Example
 * ================================================================
 */


#include <Arduino.h>
#include "TinZrCore.h"
#include "TinZrSD.h"   // declares global TinZrSD (TinZrSDLogger class)

static bool g_recording = false;
static uint32_t g_t0_ms     = 0;

static void startRecording() {
	if (g_recording) return;

	// Define t=0 at the moment recording starts
	g_t0_ms = millis();

	// Open a new log file
	if (!TinZrSD.openLog("IMU", "csv")) {
		Serial.println("❌ Failed to open log file");
		return;
	}

	// Header
	TinZrSD.writeLine("t_ms,ax,ay,az,gx,gy,gz");

	// Tell SD logger we are recording -> LED should go SOLID GREEN
	TinZrSD.setRecording(true);

	g_recording = true;
	Serial.println("✅ RECORDING STARTED");
}

static void stopRecording() {
	if (!g_recording) return;

	TinZrSD.flush();
	TinZrSD.closeLog();

	// Tell SD logger we stopped -> LED returns to FLASHING GREEN (if SD present)
    TinZrSD.setRecording(false);

	g_recording = false;
	Serial.println("⏹️ RECORDING STOPPED");
}

void setup() {
	Serial.begin(115200);
	delay(200);

	TinZr.begin();       // LED + button + battery init
	delay(100);

	// Init sensors (IMU + (optional) PPG). We'll only use IMU.
	TinZrSensorsConfig scfg;
	scfg.imu_addr = 0x6A;   
	scfg.i2c_fast = true;
	scfg.init_ppg = false;    // IMU ONLY

	if (!TinZr.sensorsBegin(scfg)) {
		Serial.println("❌ IMU not found. Check wiring / I2C address.");
	}

	// SD mount
	if (!TinZrSD.begin()) {
		Serial.println("❌ SD mount failed");
		// We don’t return; you can still read IMU without recording
	} else {
		Serial.println("✅ SD mounted");
	}

    // Ensure LED reflects “not recording” state on boot
	TinZrSD.setRecording(false);
    
	Serial.println("Press the TinZr button to START/STOP recording.");
}

void loop() {

	// SD logger state machine (LED state, retry mount, etc. if you added it)
	TinZrSD.handle();

	// ---------- Button edge detect (toggle recording) ----------
	static bool lastPressed = false;
	bool pressed = TinZr.readButtonState();   // true when pressed (per your core implementation)

	if (!lastPressed && pressed) {
		// Button just pressed -> toggle recording
		if (!g_recording) startRecording();
		else              stopRecording();
	}
	lastPressed = pressed;

	// ---------- IMU read ----------
	TinZrImuSampleSI imu;
	if (TinZr.imuReadSI(imu)) {

		// Only write to SD while recording
		if (g_recording && TinZrSD.mounted() && TinZrSD.logOpen()) {
			
			// Relative time (first sample will be 0 or close to 0)
			uint32_t t_rel = imu.t_ms - g_t0_ms;

			char line[128];
			// raw LSB values
			snprintf(line, sizeof(line),
				"%lu,%.6f,%.6f,%.6f,%.3f,%.3f,%.3f",
				(unsigned long)t_rel,
				imu.ax_g, imu.ay_g, imu.az_g,
				imu.gx_dps, imu.gy_dps, imu.gz_dps
			);
			TinZrSD.writeLine(String(line));
		}


	}

	// Flush occasionally during recording
	if (g_recording && (millis() % 1000) < 5) {
		TinZrSD.flush();
	}

	delay(4); // ~250 Hz loop target
}
