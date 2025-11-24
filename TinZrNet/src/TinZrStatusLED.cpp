#include "TinZrStatusLED.h"

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

        case Mode::WIFI_OK:
        case Mode::BLE_CONNECTED:
            // Solid green
            _setRGB(0, 255, 0);
            break;

        case Mode::WIFI_FAIL:
            // Blinking red -> handled in handle()
            _setRGB(255, 0, 0);
            break;

        case Mode::OTA_ACTIVE:
            // Blinking cyan -> handled in handle()
            _setRGB(0, 255, 255);
            break;

        case Mode::WIFI_SEARCH:
        case Mode::BLE_ADVERTISING:
            // Animated rainbow handled in handle()
            break;
    }
#endif
}

void TinZrStatusLED::handle() {
#ifdef PIN_RGB_LED
    uint32_t now = millis();

    switch (_mode) {
        case Mode::WIFI_SEARCH:
        case Mode::BLE_ADVERTISING: {
            // Smooth rainbow: advance every ~15 ms
            if (now - _t < 15) return;
            _t     = now;
            _phase = (_phase + 1) & 0xFF;

            uint8_t r, g, b;
            _wheel((uint8_t)_phase, r, g, b);
            _setRGB(r, g, b);
        } break;

        case Mode::WIFI_FAIL: {
            // Blink red: 300 ms on / 300 ms off
            const uint32_t half = 300;
            if (now - _t < half) return;
            _t       = now;
            _blinkOn = !_blinkOn;
            _setRGB(_blinkOn ? 255 : 0, 0, 0);
        } break;

        case Mode::OTA_ACTIVE: {
            // Fast cyan blink: 150 ms on / 150 ms off
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
            // steady state, nothing to do
            break;
    }
#endif
}

#ifdef PIN_RGB_LED
void TinZrStatusLED::_setRGB(uint8_t r, uint8_t g, uint8_t b) {
    // Delegate to TinZrCore; it owns the NeoPixel and brightness
    TinZr.setLED(r, g, b, _brightness);
}

void TinZrStatusLED::_wheel(uint8_t pos, uint8_t& r, uint8_t& g, uint8_t& b) {
    // Classic color wheel
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
