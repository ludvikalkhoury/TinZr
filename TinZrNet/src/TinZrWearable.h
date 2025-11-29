#pragma once

#include <Arduino.h>
#include <SD.h>

#include "TinZrConfig.h"
#include "TinZrCore.h"
#include "TinZrConnections.h"   // TinZrBleConnect
#include "TinZrLED.h"           // TinZrStatusLED

struct TinZrWearableConfig {
	const char* hostname           = "TinZrWearable";
	uint16_t    sample_interval_ms = 4;  // default ~250 Hz target
};

// Operational modes (we still keep these, but SD is ignored now)
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

	// LED (same style as TinZrNode)
	TinZrStatusLED    _statusLED;

#if TINZR_ENABLE_BLE
	TinZrBleConnect   _ble;
	bool              _bleStarted      = false;
	bool              _bleWasConnected = false;
#endif

	// State
	TinZrWearMode     _mode           = TinZrWearMode::BLE_ONLY;
	bool              _sensorsReady   = false;
	bool              _sdReady        = false;   // SD is disabled, but kept for compatibility
	bool              _streaming      = false;
	unsigned long     _lastSampleMs   = 0;

	// SD logging (NO-OP in this build; kept for linker compatibility)
	File              _logFile;
	unsigned long     _lastFlushMs    = 0;

	// Button multi-click
	bool              _btnRaw             = true;
	bool              _btnStable          = true;
	bool              _btnLastStable      = true;
	unsigned long     _btnLastChange      = 0;
	uint8_t           _btnClickCount      = 0;
	unsigned long     _lastShortClickTime = 0;

	// Internal handlers
	void _handleBLE();
	void _handleButton();
	void _handleStreaming();

	// Mode / SD
	void _cycleMode();                       // six-click
	void _applyStreamingChange(bool enable); // triple-click
	void _startSDLogging();
	void _stopSDLogging();
	bool _openNewLogFile();
	void _writeHeader();
	void _flashModeLED(TinZrWearMode wearMode);

	// LED
	void _updateLED();
	void _setErrorLED();
};
