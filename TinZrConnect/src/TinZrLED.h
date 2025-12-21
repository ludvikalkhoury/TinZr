#pragma once
#include <Arduino.h>

// =============================
// TinZrStatusLED
//   High-level LED state machine
//   Hardware + animations implemented in TinZrLED.cpp
// =============================

class TinZrStatusLED {
public:
	enum class Mode : uint8_t {
		OFF = 0,

		// Wi-Fi
		WIFI_SEARCH,
		WIFI_OK,
		WIFI_FAIL,

		// BLE
		BLE_ADVERTISING,
		BLE_CONNECTED,

		// OTA
		OTA_ACTIVE,

		// Animated states
		RAINBOW_SEARCH,
		SUCCESS_STROBE,
		SUCCESS_STEADY,
		FAIL_BLINK
	};

	// Lifecycle
	void begin(uint8_t brightness = 50);
	void handle();

	// State control
	void setMode(Mode m);
	Mode mode() const;

	// Direct color control
	void setColor(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness = 50);
	void refresh();

	// Blocking helper
	void flashColor(
		uint8_t  r,
		uint8_t  g,
		uint8_t  b,
		uint8_t  brightness,
		uint8_t  times,
		uint16_t on_ms  = 120,
		uint16_t off_ms = 120
	);

private:
	// Low-level output (writes to NeoPixel in .cpp)
	void _setRGB(uint8_t r, uint8_t g, uint8_t b);

	// Animation helpers (implemented in .cpp)
	void _handleBlink(uint32_t now);
	void _handleRainbow(uint32_t now);
	void _handleFailBlink(uint32_t now);
	void _handleSuccessStrobe(uint32_t now);

	Mode     _mode       = Mode::OFF;
	uint8_t  _brightness = 25;
	uint32_t _t          = 0;
	uint8_t  _phase      = 0;
	bool     _blinkOn    = false;
    uint8_t  _strobeCount = 0;

};

// Global LED controller (defined in TinZrLED.cpp)
extern TinZrStatusLED TinZrLED;
