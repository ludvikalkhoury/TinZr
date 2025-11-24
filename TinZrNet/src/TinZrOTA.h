#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include "TinZrConfig.h"     // for TINZR_ENABLE_OTA, PIN_RGB_LED, etc.

// ============================================================================
//  Flat OTA / Wi-Fi config (external)
// ============================================================================
struct TinZrCfg {
  const char* ssid       = "";
  const char* pass       = "";
  bool        use_static = false;

  IPAddress ip   = IPAddress(192,168,1,40);
  IPAddress gw   = IPAddress(192,168,1,1);
  IPAddress mask = IPAddress(255,255,255,0);
  IPAddress dns1 = IPAddress(8,8,8,8);
  IPAddress dns2 = IPAddress(8,8,4,4);

  const char*  ota_password   = nullptr;
  uint16_t     ota_port       = 3232;
  wifi_power_t tx_power       = WIFI_POWER_8_5dBm;

  uint8_t led_brightness = 25;
  bool    led_enable     = true;
};


// ============================================================================
//  OTA class (full when enabled, clean stubs when disabled)
// ============================================================================
#if TINZR_ENABLE_OTA
  #include <ArduinoOTA.h>
#endif


class TinZrOTA {
public:

// ============================================================================
//  FULL IMPLEMENTATION (when OTA enabled)
// ============================================================================
#if TINZR_ENABLE_OTA

  void begin(const char* hostname,
             const TinZrCfg& cfg,
             uint32_t connect_timeout_ms = 15000);

  void handle();

  bool connected() const {
    return WiFi.status() == WL_CONNECTED;
  }

  bool ready() const {
#ifdef PIN_RGB_LED
    return (_connState == CONNECTED &&
            _ledState == LedState::SUCCESS_STEADY);
#else
    return (_connState == CONNECTED);
#endif
  }

  IPAddress ip() const { return WiFi.localIP(); }

  const String& host() const { return _hostname; }

  void setFailHoldMs(uint32_t ms) { _failHoldMs = ms; }


// ============================================================================
//  STUB IMPLEMENTATION (when OTA disabled)
// ============================================================================
#else

  void begin(const char*, const TinZrCfg&, uint32_t = 15000) {}
  void handle() {}

  bool connected() const { return false; }
  bool ready() const     { return false; }

  IPAddress ip() const { return IPAddress(0,0,0,0); }

  const String& host() const {
    static String empty;
    return empty;
  }

  void setFailHoldMs(uint32_t) {}

#endif // TINZR_ENABLE_OTA



private:
#if TINZR_ENABLE_OTA

  // -------------------------------------------------------------------------
  // Wi-Fi + OTA internals
  // -------------------------------------------------------------------------
  void connectWiFi();
  void setupOTA();

  enum ConnState : uint8_t {
    CONNECTING,
    CONNECTED,
    FAIL_WAIT
  };

  TinZrCfg   _cfg{};
  String     _hostname{};
  uint32_t   _connectTimeoutMs = 30000;

  ConnState  _connState = CONNECTING;
  uint32_t   _failHoldMs = 10000;
  uint32_t   _failUntil  = 0;

  unsigned long _lastReconnect = 0; // kept for compatibility


#ifdef PIN_RGB_LED
  // -------------------------------------------------------------------------
  // LED animation state machine
  // -------------------------------------------------------------------------
  enum class LedState : uint8_t {
    OFF = 0,
    SEARCHING,
    SUCCESS_STROBE,
    SUCCESS_STEADY,
    FAIL_BLINK
  };

  LedState  _ledState = LedState::OFF;
  uint32_t  _ledT     = 0;
  uint16_t  _phase    = 0;
  uint8_t   _succCnt  = 0;

  void ledBegin();
  void ledSetState(LedState s);
  void ledUpdate();
  void ledSetRGB(uint8_t r, uint8_t g, uint8_t b);

  static void wheel(uint8_t pos, uint8_t& r, uint8_t& g, uint8_t& b);
#endif // PIN_RGB_LED

#endif // TINZR_ENABLE_OTA
};
