#pragma once

#include <Arduino.h>

#include "TinZrConfig.h"
#include "TinZrCore.h"
#include "TinZrBLE.h"          // TinZrBLEConnect (GATT)
#include "TinZrLED.h"          // TinZrStatusLED
#include "TinZrSD.h"           // TinZrSDLogger (SD card logging)

struct TinZrWearableSDConfig {
	const char* hostname           = "TinZrWearableSD";
	// Sampling interval in ms (4 ms ≈ 250 Hz target, resampled to 240 Hz on hub)
	uint16_t    sample_interval_ms = 4;

	// nullptr => use TINZR_SD_LOG_DIR (from TinZrConfig.h)
	const char* sd_log_dir         = nullptr;
};

// We keep enum for future flexibility, but effectively use BLE_ONLY.
enum class TinZrWearSDMode : uint8_t {
	BLE_ONLY = 0,
	SD_ONLY,
	BLE_AND_SD,
	NUM_MODES
};

class TinZrWearableSDClass {
public:
	TinZrWearableSDClass();
	void begin(const TinZrWearableSDConfig& cfg);
	void handle();
	void forceBatteryUpdate();

private:
	TinZrWearableSDConfig _cfg{};

#if TINZR_ENABLE_BLE
	TinZrBLEConnect _ble;
	bool            _bleStarted      = false;
	bool            _bleWasConnected = false;
#endif

	// State
	TinZrWearSDMode _mode           = TinZrWearSDMode::BLE_AND_SD;

	// IMU required; PPG optional
	bool _sensorsReady = false;  // kept for compatibility (== _imuReady)
	bool _imuReady     = false;  // IMU present/configured
	bool _ppgReady     = false;  // PPG present/configured (optional)

	bool          _streaming    = false;
	unsigned long _lastSampleMs = 0;
	bool          _wasSoftOn    = false;

	// ===== SD logging controlled by PC heartbeat =====
	bool     _sdReady             = false;
	bool     _recordArmed         = false;   // set by GUI (START/STOP)
	bool     _recording           = false;   // actual: armed + heartbeat OK
	String   _participant         = "";

	uint32_t _lastHeartbeatMs     = 0;       // local millis when last T: received
	uint32_t _pcAnchorLocalMs     = 0;       // local millis corresponding to _pcAnchorMs
	uint64_t _pcAnchorMs          = 0;       // PC epoch ms received in T:
	bool     _hasPcAnchor         = false;
	uint32_t _sampleIdx 					= 0;


	// Store the *string* timestamp from X: for filename + header
	String   _pcAnchorStr         = "";
	bool     _hasPcAnchorStr      = false;

	// NEW: SD hot-plug probing state
	unsigned long _lastSdProbeMs  = 0;

	// NEW: whether we already wrote the metadata header for the current file
	bool     _logHeaderWritten    = false;

	static constexpr uint32_t HEARTBEAT_PERIOD_MS  = 5000UL;
	static constexpr uint32_t HEARTBEAT_TIMEOUT_MS = 6500UL; // stop logging if heartbeat missing

	// Internal handlers
	static TinZrWearableSDClass* _self;
	static void _bleWriteStatic(const uint8_t* data, size_t len);
	void _handleBleCommand(const uint8_t* data, size_t len);

	void _handleBLE();
	void _handleStreaming();
	void _applyStreamingChange(bool enable);

	// NEW: SD hot-plug probe helper
	void _probeSDHotplug(unsigned long now);

	// NEW: write metadata header + csv header (called once per log open)
	void _writeLogHeaders(const String& file_base);

	// LED
	void _updateLED();
	void _setErrorLED();
};

extern TinZrWearableSDClass TinZrWearableSD;
