#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoOTA.h>


// External config struct (not nested!)
struct TinZrCfg {
  const char* ssid = "";
  const char* pass = "";
  bool      use_static = false;
  IPAddress ip   = IPAddress(192,168,1,40);
  IPAddress gw   = IPAddress(192,168,1,1);
  IPAddress mask = IPAddress(255,255,255,0);
  IPAddress dns1 = IPAddress(8,8,8,8);
  IPAddress dns2 = IPAddress(8,8,4,4);
  const char* ota_password = nullptr;     // default: none
  uint16_t    ota_port     = 3232;
  wifi_power_t tx_power    = WIFI_POWER_8_5dBm;
  uint8_t led_brightness   = 25;
  bool led_enable = true;
};

class TinZrOTA {
public:
  void begin(const char* hostname, const TinZrCfg& cfg, uint32_t connect_timeout_ms = 15000);
  void handle();

  bool        connected() const { return WiFi.status() == WL_CONNECTED; }
  
  // "Ready" = Wi-Fi connected AND LED is in steady-success state
  bool        ready() const {
  #ifdef PIN_RGB_LED
    return (_connState == CONNECTED) && (_ledState == LedState::SUCCESS_STEADY);
  #else
    // If no LED, just use connection state
    return (_connState == CONNECTED);
  #endif
  }
    
  
  IPAddress   ip()        const { return WiFi.localIP(); }
  const String& host()    const { return _hostname; }

  // Optional: tweak how long to keep FAIL_BLINK before retry (ms)
  void setFailHoldMs(uint32_t ms) { _failHoldMs = ms; }

private:
  // ===== Wi-Fi & OTA =====
  void connectWiFi();
  void setupOTA();

  // ===== State / timing =====
  enum ConnState : uint8_t { CONNECTING, CONNECTED, FAIL_WAIT };

  TinZrCfg _cfg;
  String _hostname;
  uint32_t _connectTimeoutMs = 30000;

  ConnState _connState = CONNECTING;
  uint32_t _failHoldMs = 10000;   // 10 s red blink before next retry
  uint32_t _failUntil  = 0;       // millis() deadline for FAIL_WAIT

  // (legacy) not used anymore for 2s reconnect loop, kept for compatibility if you reference it elsewhere
  unsigned long _lastReconnect = 0;

#ifdef PIN_RGB_LED
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
  // Compute an RGB from a 0–255 phase value
  static void wheel(uint8_t pos, uint8_t& r, uint8_t& g, uint8_t& b);
#endif
};
