#pragma once

#include <Arduino.h>

#include "TinZrConfig.h"
#include "TinZrCore.h"
#include "TinZrConnections.h"   // TinZrBleConnect
#include "TinZrLED.h"           // TinZrStatusLED

struct TinZrMultiWearableConfig {
	const char* hostname           = "TinZrMultiWearable";
	// Sampling interval in ms (4 ms ≈ 250 Hz target, resampled to 240 Hz on hub)
	uint16_t    sample_interval_ms = 4;
};

// We keep enum for future flexibility, but effectively use BLE_ONLY.
enum class TinZrMultiWearMode : uint8_t {
	BLE_ONLY = 0,
	SD_ONLY,
	BLE_AND_SD,
	NUM_MODES
};

class TinZrMultiWearable {
public:
	TinZrMultiWearable();
	void begin(const TinZrMultiWearableConfig& cfg);
	void handle();
	void forceBatteryUpdate();

private:
	TinZrMultiWearableConfig _cfg{};

	// Status LED (same style as TinZrNode)
	TinZrStatusLED    _statusLED;

#if TINZR_ENABLE_BLE
	TinZrBleConnect   _ble;
	bool              _bleStarted      = false;
	bool              _bleWasConnected = false;
#endif

	// State
	TinZrMultiWearMode _mode           = TinZrMultiWearMode::BLE_ONLY;

	// IMU required; PPG optional
	bool              _sensorsReady   = false;  // kept for compatibility (== _imuReady)
	bool              _imuReady       = false;  // IMU present/configured
	bool              _ppgReady       = false;  // PPG present/configured (optional)

	bool              _streaming      = false;
	unsigned long     _lastSampleMs   = 0;
	bool              _wasSoftOn      = false;

	// Internal handlers
	static TinZrMultiWearable* _self;
	static void _bleCallbackStatic(
		IPAddress from,
		const uint8_t* data,
		size_t len
	);
	void _handleBleCommand(const uint8_t* data, size_t len);

	void _handleBLE();
	void _handleStreaming();
	void _applyStreamingChange(bool enable);

	// LED
	void _updateLED();
	void _setErrorLED();
};
