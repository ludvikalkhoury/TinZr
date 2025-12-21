#include "TinZrLED.h"
#include <Adafruit_NeoPixel.h>

// Expect PIN_RGB_LED in variant/config; fallback if not defined:
#ifndef PIN_RGB_LED
#define PIN_RGB_LED 8
#endif

// Global instance
TinZrStatusLED TinZrLED;

// NeoPixel owned by TinZrLED.cpp
static Adafruit_NeoPixel gPixel(1, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

// ---------------- internal helper ----------------
static void wheel_rgb(uint8_t pos, uint8_t& r, uint8_t& g, uint8_t& b) {
	if (pos < 85) {
		r = pos * 3;
		g = 255 - pos * 3;
		b = 0;
	} else if (pos < 170) {
		pos -= 85;
		r = 255 - pos * 3;
		g = 0;
		b = pos * 3;
	} else {
		pos -= 170;
		r = 0;
		g = pos * 3;
		b = 255 - pos * 3;
	}
}

// ================= TinZrTinZrLED =================

void TinZrStatusLED::begin(uint8_t brightness) {
	_brightness  = brightness;
	_mode        = Mode::OFF;
	_t           = millis();
	_phase       = 0;
	_blinkOn     = false;
	_strobeCount = 0;

	gPixel.begin();
	gPixel.setBrightness(_brightness);
	gPixel.clear();
	gPixel.show();

	_setRGB(0, 0, 0);
}

TinZrStatusLED::Mode TinZrStatusLED::mode() const {
	return _mode;
}

void TinZrStatusLED::setMode(Mode m) {
	if (m == _mode) return;

	_mode        = m;
	_t           = millis();
	_phase       = 0;
	_blinkOn     = false;
	_strobeCount = 0;   

	switch (_mode) {
		case Mode::OFF:
			_setRGB(0, 0, 0);
			break;

		// Solid green
		case Mode::WIFI_OK:
		case Mode::BLE_CONNECTED:
		case Mode::SUCCESS_STEADY:
			_setRGB(0, 255, 0);
			break;

		// Animated / blinking handled in handle()
		case Mode::WIFI_SEARCH:
		case Mode::WIFI_FAIL:
		case Mode::BLE_ADVERTISING:
		case Mode::OTA_ACTIVE:
		case Mode::RAINBOW_SEARCH:
		case Mode::SUCCESS_STROBE:
		case Mode::FAIL_BLINK:
		default:
			break;
	}
}

void TinZrStatusLED::handle() {
	uint32_t now = millis();

	switch (_mode) {
		// Blink green
		case Mode::WIFI_SEARCH:
		case Mode::BLE_ADVERTISING:
			_handleBlink(now);
			break;

		// Blink red
		case Mode::WIFI_FAIL:
		case Mode::FAIL_BLINK:
			_handleFailBlink(now);
			break;

		// Blink cyan (faster)
		case Mode::OTA_ACTIVE:
			_handleBlink(now);
			break;

		// Rainbow animation
		case Mode::RAINBOW_SEARCH:
			_handleRainbow(now);
			break;

		// Green strobe then steady green
		case Mode::SUCCESS_STROBE:
			_handleSuccessStrobe(now);
			break;

		// Solid / off do nothing
		case Mode::WIFI_OK:
		case Mode::BLE_CONNECTED:
		case Mode::SUCCESS_STEADY:
		case Mode::OFF:
		default:
			break;
	}
}

void TinZrStatusLED::setColor(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness) {
	_brightness = brightness;
	_setRGB(r, g, b);
}

void TinZrStatusLED::refresh() {
		gPixel.show();
}

void TinZrStatusLED::flashColor(
	uint8_t  r,
	uint8_t  g,
	uint8_t  b,
	uint8_t  brightness,
	uint8_t  times,
	uint16_t on_ms,
	uint16_t off_ms
) {
	Mode    oldMode       = _mode;
	uint8_t oldBrightness = _brightness;
	uint8_t oldPhase      = _phase;
	bool    oldBlinkOn    = _blinkOn;
	uint8_t oldStrobe     = _strobeCount;

	for (uint8_t i = 0; i < times; ++i) {
		_brightness = brightness;
		_setRGB(r, g, b);
		delay(on_ms);

		_setRGB(0, 0, 0);
		delay(off_ms);
	}

	_brightness  = oldBrightness;
	_phase       = oldPhase;
	_blinkOn     = oldBlinkOn;
	_strobeCount = oldStrobe;

	setMode(oldMode);
}

// ---------------- private: low-level output ----------------
void TinZrStatusLED::_setRGB(uint8_t r, uint8_t g, uint8_t b) {
	gPixel.setBrightness(_brightness);
	gPixel.setPixelColor(0, gPixel.Color(r, g, b));
	gPixel.show();
}

// ---------------- private: animation helpers ----------------

void TinZrStatusLED::_handleBlink(uint32_t now) {
	// WIFI_SEARCH / BLE_ADVERTISING: green blink (300ms)
	// OTA_ACTIVE: cyan blink (150ms)
	uint32_t half = 300;

	uint8_t r_on = 0, g_on = 255, b_on = 0;
	if (_mode == Mode::OTA_ACTIVE) {
		half = 150;
		r_on = 0; g_on = 255; b_on = 255; // cyan
	}

	if (now - _t < half) return;
	_t = now;

	_blinkOn = !_blinkOn;
	if (_blinkOn) _setRGB(r_on, g_on, b_on);
	else          _setRGB(0, 0, 0);
}

void TinZrStatusLED::_handleRainbow(uint32_t now) {
	if (now - _t < 15) return;
	_t = now;

	_phase++;

	uint8_t r, g, b;
	wheel_rgb((uint8_t)_phase, r, g, b);
	_setRGB(r, g, b);
}

void TinZrStatusLED::_handleFailBlink(uint32_t now) {
	const uint32_t half = 300;
	if (now - _t < half) return;
	_t = now;

	_blinkOn = !_blinkOn;
	if (_blinkOn) _setRGB(255, 0, 0);
	else          _setRGB(0, 0, 0);
}

void TinZrStatusLED::_handleSuccessStrobe(uint32_t now) {
	// Continuous green blink (forever)
	const uint16_t onMs  = 150;
	const uint16_t offMs = 150;

	// _phase states:
	// 0: set green, wait onMs
	// 1: set off,   wait offMs
	if (_phase == 0) {
		_setRGB(0, 255, 0);
		_phase = 1;
		_t = now;
		return;
	}

	if (_phase == 1) {
		if (now - _t < onMs) return;
		_setRGB(0, 0, 0);
		_phase = 2;
		_t = now;
		return;
	}

	// _phase == 2
	if (now - _t < offMs) return;

	// Loop forever
	_phase = 0;
	_t = now;
}

