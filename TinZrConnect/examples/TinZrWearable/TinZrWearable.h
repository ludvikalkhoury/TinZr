#pragma once

#include <Arduino.h>

#include "TinZrConfig.h"
#include "TinZrCore.h"
#include "TinZrBLE.h"          // TinZrBLEConnect (GATT)
#include "TinZrLED.h"          // TinZrStatusLED

struct TinZrWearableConfig {
	const char* hostname           = "TinZrWearable";
	// Sampling interval in ms (4 ms ≈ 250 Hz target, resampled to 240 Hz on hub)
	uint16_t    sample_interval_ms = 4;
};

// We keep enum for future flexibility, but effectively use BLE_ONLY.
enum class TinZrWearMode : uint8_t {
	BLE_ONLY = 0,
	SD_ONLY,
	BLE_AND_SD,
	NUM_MODES
};

class TinZrWearableClass {
public:
	TinZrWearableClass();
	void begin(const TinZrWearableConfig& cfg);
	void handle();
	void forceBatteryUpdate();

private:
	TinZrWearableConfig _cfg{};


#if TINZR_ENABLE_BLE
	TinZrBLEConnect _ble;
	bool           _bleStarted      = false;
	bool           _bleWasConnected = false;
#endif

	// State
	TinZrWearMode _mode           = TinZrWearMode::BLE_ONLY;

	// IMU required; PPG optional
	bool _sensorsReady = false;  // kept for compatibility (== _imuReady)
	bool _imuReady     = false;  // IMU present/configured
	bool _ppgReady     = false;  // PPG present/configured (optional)

	bool          _streaming    = false;
	unsigned long _lastSampleMs = 0;
	bool          _wasSoftOn    = false;

	// Internal handlers
	static TinZrWearableClass* _self;
	static void _bleWriteStatic(const uint8_t* data, size_t len);
	void _handleBleCommand(const uint8_t* data, size_t len);

	void _handleBLE();
	void _handleStreaming();
	void _applyStreamingChange(bool enable);

	// LED
	void _updateLED();
	void _setErrorLED();
};

extern TinZrWearableClass TinZrWearable;