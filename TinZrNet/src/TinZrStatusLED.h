#pragma once

#include <Arduino.h>
#include "TinZrConfig.h"
#include "TinZrCore.h"   // for TinZr.setLED / TinZr.ledOff()

// Unified status LED engine for Wi-Fi, BLE, OTA, etc.
class TinZrStatusLED {
public:
    // Simple high-level states
    enum class Mode : uint8_t {
        OFF = 0,
        WIFI_SEARCH,     // rainbow / scanning
        WIFI_OK,         // solid green
        WIFI_FAIL,       // blinking red
        BLE_ADVERTISING, // rainbow / scanning
        BLE_CONNECTED,   // solid green
        OTA_ACTIVE       // fast cyan blink
    };

    void begin(uint8_t brightness = 25);
    void setMode(Mode m);
    Mode mode() const { return _mode; }

    // Call frequently from loop()/TinZrNode::handle()
    void handle();

private:
#ifdef PIN_RGB_LED
    Mode     _mode        = Mode::OFF;
    uint8_t  _brightness  = 25;
    uint32_t _t           = 0;
    uint16_t _phase       = 0;
    bool     _blinkOn     = false;

    void _setRGB(uint8_t r, uint8_t g, uint8_t b);
    static void _wheel(uint8_t pos, uint8_t& r, uint8_t& g, uint8_t& b);
#else
    // If no RGB LED is present, we still keep the type valid,
    // but all methods become no-ops.
    Mode     _mode        = Mode::OFF;
#endif
};
