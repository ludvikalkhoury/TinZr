#pragma once
#include <Arduino.h>
#include <Adafruit_NeoPixel.h>

class TinZrLEDAnimator {
public:
  enum State : uint8_t {
    OFF = 0,
    SEARCHING,        // rainbow wheel
    SUCCESS_STROBE,   // green blink N times
    SUCCESS_STEADY,   // steady green
    FAIL_BLINK        // blinking red
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

  // Call from loop() (non-blocking)
  void update() {
    uint32_t now = millis();
    switch (_state) {
      case SEARCHING:       _rainbow(now); break;
      case SUCCESS_STROBE:  _successStrobe(now); break;
      case SUCCESS_STEADY:  /* steady green already set */ break;
      case FAIL_BLINK:      _blinkRed(now); break;
      case OFF: default:    break;
    }
  }

  // Utility for manual color set if needed
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
    // Custom color wave: Blue → Purple → Cyan (no red, no green)
    // pos goes 0–255; we break into 3 equal sections ~85 steps each

    if (pos < 85) {
      // Blue → Purple
      uint8_t t = pos * 3;       // 0 → 255
      return p.Color(t, 0, 255); // R increases, B stays max
    }
    else if (pos < 170) {
      // Purple → Cyan
      pos -= 85;
      uint8_t t = pos * 3;       // 0 → 255
      return p.Color(255, 0, 255 - t); // R fades out, B fades to cyan
    }
    else {
      // Cyan → Blue
      pos -= 170;
      uint8_t t = pos * 3;       // 0 → 255
      return p.Color(0, t, 255); // G increases slightly but never pure green
    }
  }


  void _rainbow(uint32_t now) {
    // advance every 15 ms for smoothness
    if (now - _last < 15) return;
    _last = now;
    _phase = (_phase + 1) & 0xFF;           // 0..255
    _pixels.setPixelColor(0, _wheel(_pixels, (uint8_t)_phase));
    _pixels.show();
  }

  void _successStrobe(uint32_t now) {
    // 5 green blinks, 150ms on / 150ms off, then steady green
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
    // red 300ms on / 300ms off
    const uint16_t half = 300;
    if (now - _last < half) return;
    _last = now;
    // toggle
    static bool on = false;
    on = !on;
    _setColor(on ? 255 : 0, 0, 0);
  }
};
