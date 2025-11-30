#pragma once

#include <Arduino.h>

#include "TinZrConfig.h"
#include "TinZrCore.h"
#include "TinZrConnections.h"   // TinZrBleConnect
#include "TinZrLED.h"           // TinZrStatusLED

struct TinZrWearableConfig {
	const char* hostname           = "TinZrWearable";
	uint16_t    sample_interval_ms = 4;  // default ~250 Hz target
};

// We keep enum for future flexibility, but effectively use BLE_ONLY.
enum class TinZrWearMode : uint8_t {
	BLE_ONLY = 0,
	SD_ONLY,
	BLE_AND_SD,
	NUM_MODES
};

class TinZrWearable {
public:
	TinZrWearable();
	void begin(const TinZrWearableConfig& cfg);
	void handle();

private:
	TinZrWearableConfig _cfg{};
	
	// Status LED (same style as TinZrNode)
	TinZrStatusLED    _statusLED;

#if TINZR_ENABLE_BLE
	TinZrBleConnect   _ble;
	bool              _bleStarted      = false;
	bool              _bleWasConnected = false;
#endif

	// State
	TinZrWearMode     _mode           = TinZrWearMode::BLE_ONLY;
	bool              _sensorsReady   = false;
	bool              _streaming      = false;
	unsigned long     _lastSampleMs   = 0;
	bool              _wasSoftOn      = false;    

	// Internal handlers
	void _handleBLE();
	void _handleStreaming();
	void _applyStreamingChange(bool enable);

	// LED
	void _updateLED();
	void _setErrorLED();
};
