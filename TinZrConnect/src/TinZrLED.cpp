#include "TinZrLED.h"

// ================= TinZrStatusLED =================
void TinZrStatusLED::begin(uint8_t brightness) {
#ifdef PIN_RGB_LED
    _brightness = brightness;
    _mode       = Mode::OFF;
    _t          = 0;
    _phase      = 0;
    _blinkOn    = false;
    TinZr.ledOff();
#endif
}

void TinZrStatusLED::setMode(Mode m) {
#ifdef PIN_RGB_LED
	if (m == _mode) return;

	_mode    = m;
	_t       = 0;
	_phase   = 0;
	_blinkOn = false;

	switch (_mode) {
		case Mode::OFF:
			TinZr.ledOff();
			break;

		// ✅ Solid green only when actually connected
		case Mode::WIFI_OK:
		case Mode::BLE_CONNECTED:
			_setRGB(0,255,0);
			break;

		// ✅ Red (blink handled in handle)
		case Mode::WIFI_FAIL:
			_setRGB(255,0,0);
			break;

		// ✅ Cyan (blink handled in handle)
		case Mode::OTA_ACTIVE:
			_setRGB(0,255,255);
			break;

		// ✅ Searching / advertising → no immediate color,
		//    blinking handled in handle()
		case Mode::WIFI_SEARCH:
		case Mode::BLE_ADVERTISING:
			// do nothing here; handle() will blink green
			break;
	}
#endif
}


void TinZrStatusLED::handle() {
#ifdef PIN_RGB_LED
	uint32_t now = millis();
	switch (_mode) {

		// ✅ Wi-Fi searching AND BLE advertising → flashing green
		case Mode::WIFI_SEARCH:
		case Mode::BLE_ADVERTISING: {
			const uint32_t half = 300; // 300ms on/off
			if (now - _t < half) return;
			_t       = now;
			_blinkOn = !_blinkOn;
			_setRGB(0, _blinkOn ? 255 : 0, 0);  // green on/off
		} break;

		case Mode::WIFI_FAIL: {
			const uint32_t half = 300;
			if (now - _t < half) return;
			_t       = now;
			_blinkOn = !_blinkOn;
			_setRGB(_blinkOn ? 255 : 0, 0, 0);
		} break;

		case Mode::OTA_ACTIVE: {
			const uint32_t half = 150;
			if (now - _t < half) return;
			_t       = now;
			_blinkOn = !_blinkOn;
			_setRGB(0, _blinkOn ? 255 : 0, _blinkOn ? 255 : 0);
		} break;

		case Mode::WIFI_OK:
		case Mode::BLE_CONNECTED:
		case Mode::OFF:
		default:
			break;
	}
#endif
}


#ifdef PIN_RGB_LED
void TinZrStatusLED::_setRGB(uint8_t r, uint8_t g, uint8_t b) {
    TinZr.setLED(r,g,b,_brightness);
}

// wheel() is now unused, but keeping it is harmless
void TinZrStatusLED::_wheel(uint8_t pos, uint8_t& r, uint8_t& g, uint8_t& b) {
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
#endif



// ================= NEW: blocking flash helper =================
void TinZrStatusLED::flashColor(
    uint8_t  r,
    uint8_t  g,
    uint8_t  b,
    uint8_t  brightness,
    uint8_t  times,
    uint16_t on_ms,
    uint16_t off_ms
) {
#ifdef PIN_RGB_LED
    // Save current state
    Mode    oldMode       = _mode;
    uint8_t oldBrightness = _brightness;

    // We temporarily take full control of the LED; mode/handle()
    // blinking is effectively paused during this call.
    for (uint8_t i = 0; i < times; ++i) {
        _brightness = brightness;
        _setRGB(r, g, b);
        TinZr.handle();   // optional: keep core servicing
        delay(on_ms);

        _setRGB(0, 0, 0);
        TinZr.handle();
        delay(off_ms);
    }

    // Restore previous brightness & mode (which also restores the correct color)
    _brightness = oldBrightness;
    setMode(oldMode);
#else
    (void)r; (void)g; (void)b;
    (void)brightness; (void)times;
    (void)on_ms; (void)off_ms;
#endif
}
