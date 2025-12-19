#pragma once
#include <Arduino.h>
#include "TinZrCore.h"
#include <Adafruit_NeoPixel.h>

// =============================
// TinZrStatusLED  (high-level LED states)
// =============================

class TinZrStatusLED {
public:
    enum class Mode : uint8_t {
        OFF = 0,
        WIFI_SEARCH,
        WIFI_OK,
        WIFI_FAIL,
        BLE_ADVERTISING,
        BLE_CONNECTED,
        OTA_ACTIVE
    };

    void begin(uint8_t brightness = 25);
    void setMode(Mode m);
    Mode mode() const { return _mode; }
    void handle();
    
    void flashColor(
        uint8_t  r,
        uint8_t  g,
        uint8_t  b,
        uint8_t  brightness,
        uint8_t  times,
        uint16_t on_ms  = 120,
        uint16_t off_ms = 120
    );
    
    void setColor(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness);
    void refresh();

    
private:
#ifdef PIN_RGB_LED
    Mode     _mode       = Mode::OFF;
    uint8_t  _brightness = 25;
    uint32_t _t          = 0;
    uint16_t _phase      = 0;
    bool     _blinkOn    = false;

    void _setRGB(uint8_t r, uint8_t g, uint8_t b);
    static void _wheel(uint8_t pos, uint8_t& r, uint8_t& g, uint8_t& b);
#else
    Mode     _mode       = Mode::OFF;
#endif
};

// =============================
// TinZrLEDAnimator (extra effects on a NeoPixel)
// =============================

class TinZrLEDAnimator {
public:
  enum State : uint8_t {
    OFF = 0,
    SEARCHING,
    SUCCESS_STROBE,
    SUCCESS_STEADY,
    FAIL_BLINK
  };

  TinZrLEDAnimator(uint16_t numPixels, uint8_t pin)
  : _pixels(numPixels, pin, NEO_GRB + NEO_KHZ800) {}

  void begin(uint8_t brightness = 80) {
    _pixels.begin();
    _pixels.setBrightness(brightness);
    _pixels.show();
  }

  void setState(State s) {
    if (_state == s) return;
    _state = s;
    _phase = 0;
    _last = 0;
    if (s == SUCCESS_STEADY) _setColor(0,255,0);
    if (s == OFF)            _setColor(0,0,0);
  }

  State state() const { return _state; }

  void update() {
    uint32_t now = millis();
    switch (_state) {
      case SEARCHING:       _rainbow(now); break;
      case SUCCESS_STROBE:  _successStrobe(now); break;
      case SUCCESS_STEADY:  break;
      case FAIL_BLINK:      _blinkRed(now); break;
      case OFF: default:    break;
    }
  }

  void setColor(uint8_t r, uint8_t g, uint8_t b) { _setColor(r,g,b); }

private:
  Adafruit_NeoPixel _pixels;
  State _state = OFF;
  uint32_t _last = 0;
  uint16_t _phase = 0;
  uint8_t  _successCount = 0;

  void _setColor(uint8_t r, uint8_t g, uint8_t b) {
    _pixels.setPixelColor(0, _pixels.Color(r,g,b));
    _pixels.show();
  }

  static uint32_t _wheel(Adafruit_NeoPixel& p, uint8_t pos) {
    if (pos < 85) {
      uint8_t t = pos * 3;
      return p.Color(t, 0, 255);
    } else if (pos < 170) {
      pos -= 85;
      uint8_t t = pos * 3;
      return p.Color(255, 0, 255 - t);
    } else {
      pos -= 170;
      uint8_t t = pos * 3;
      return p.Color(0, t, 255);
    }
  }

  void _rainbow(uint32_t now) {
    if (now - _last < 15) return;
    _last = now;
    _phase = (_phase + 1) & 0xFF;
    _pixels.setPixelColor(0, _wheel(_pixels, (uint8_t)_phase));
    _pixels.show();
  }

  void _successStrobe(uint32_t now) {
    const uint16_t onMs = 150, offMs = 150;
    if (_phase == 0) { _setColor(0,255,0); _phase = 1; _last = now; return; }
    if (_phase == 1 && now - _last >= onMs) { _setColor(0,0,0); _phase = 2; _last = now; return; }
    if (_phase == 2 && now - _last >= offMs) {
      _successCount++;
      if (_successCount >= 5) { setState(SUCCESS_STEADY); return; }
      _phase = 0;
    }
  }

  void _blinkRed(uint32_t now) {
    const uint16_t half = 300;
    if (now - _last < half) return;
    _last = now;
    static bool on = false;
    on = !on;
    _setColor(on ? 255 : 0, 0, 0);
  }
};
