#include "TinZrOTA.h"
#include "TinZrCore.h"   

// ==================== Public API ====================

void TinZrOTA::begin(const char* hostname, const TinZrCfg& cfg, uint32_t connect_timeout_ms) {
  _cfg = cfg;
  _hostname = (hostname && hostname[0]) ? hostname : String("tinzr");
  _connectTimeoutMs = connect_timeout_ms;

  Serial.println("\n🚀 TinZrOTA boot");

#ifdef PIN_RGB_LED
  if (_cfg.led_enable) ledSetState(LedState::SUCCESS_STROBE);
#endif


  connectWiFi();   // blocks up to _connectTimeoutMs
  setupOTA();      // safe to call even if not connected yet

  if (WiFi.status() == WL_CONNECTED) {
#ifdef PIN_RGB_LED
    if (_cfg.led_enable) ledSetState(LedState::SUCCESS_STROBE);
#endif
    _connState = CONNECTED;
  } else {
    Serial.println("❌ Wi-Fi connect timeout");
#ifdef PIN_RGB_LED
    if (_cfg.led_enable) ledSetState(LedState::FAIL_BLINK);
#endif
    _connState = FAIL_WAIT;
    _failUntil = millis() + _failHoldMs;  // keep blinking red for 10 s
  }
}

void TinZrOTA::handle() {
  ArduinoOTA.handle();
#ifdef PIN_RGB_LED
  if (_cfg.led_enable) ledUpdate();
#endif

  switch (_connState) {
    case CONNECTED: {
      // If Wi-Fi drops after being connected, enter 10 s FAIL_WAIT then retry
      if (WiFi.status() != WL_CONNECTED) {
        Serial.println("📴 Wi-Fi dropped → red blink 10 s, then retry");
#ifdef PIN_RGB_LED
        if (_cfg.led_enable) ledSetState(LedState::FAIL_BLINK);
#endif
        _connState = FAIL_WAIT;
        _failUntil = millis() + _failHoldMs;
      }
    } break;

    case FAIL_WAIT: {
      // Keep blinking red until the hold window ends, then attempt reconnect
      if ((int32_t)(millis() - _failUntil) >= 0) {
        Serial.println("🔁 Retry Wi-Fi…");
#ifdef PIN_RGB_LED
        if (_cfg.led_enable) ledSetState(LedState::SEARCHING);
#endif
        connectWiFi();   // blocks up to _connectTimeoutMs

        if (WiFi.status() == WL_CONNECTED) {
#ifdef PIN_RGB_LED
          if (_cfg.led_enable) ledSetState(LedState::SUCCESS_STROBE);
#endif
          setupOTA();    // ensure OTA ready after rejoin
          _connState = CONNECTED;
        } else {
#ifdef PIN_RGB_LED
          if (_cfg.led_enable) ledSetState(LedState::FAIL_BLINK);
#endif
          _connState = FAIL_WAIT;
          _failUntil = millis() + _failHoldMs; // another 10 s before next retry
        }
      }
    } break;

    case CONNECTING:
    default:
      // Not used after begin(); retries go via FAIL_WAIT → connectWiFi()
      break;
  }
}

// ==================== Wi-Fi & OTA ====================

void TinZrOTA::connectWiFi() {
  // --- Wi-Fi init: same flow + clear stale state ---
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setTxPower(_cfg.tx_power);
  WiFi.persistent(false);
  WiFi.disconnect(true, true);
  delay(300);

  if (_cfg.use_static) {
    Serial.println("🧭 Static IP requested…");
    if (!WiFi.config(_cfg.ip, _cfg.gw, _cfg.mask, _cfg.dns1, _cfg.dns2)) {
      Serial.println("⚠️  WiFi.config() failed → DHCP fallback");
    }
  } else {
    Serial.println("📱 DHCP mode");
  }

  Serial.printf("📶 Connecting to SSID \"%s\"…\n", _cfg.ssid);
  WiFi.begin(_cfg.ssid, _cfg.pass);

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - start) < _connectTimeoutMs) {
    delay(50);
    ArduinoOTA.handle();         // allow mid-connect OTA pushes
#ifdef PIN_RGB_LED
    if (_cfg.led_enable) ledUpdate();
#endif
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("✅ Wi-Fi connected!");
    Serial.print("🖥️ Hostname: ");  Serial.println(_hostname);
    Serial.print("📡 IP: ");        Serial.println(WiFi.localIP());
  } else {
    Serial.println("❌ Wi-Fi connect timeout");
  }
}

void TinZrOTA::setupOTA() {
  ArduinoOTA.setHostname(_hostname.c_str());  // explicit name
  ArduinoOTA.setPort(_cfg.ota_port);          // default 3232

  // Default: NO password (only set if provided & non-empty)
  if (_cfg.ota_password && _cfg.ota_password[0]) {
    //ArduinoOTA.setPassword(_cfg.ota_password);
  }

  ArduinoOTA.onStart([](){ Serial.println("\nOTA start"); });
  ArduinoOTA.onEnd  ([](){ Serial.println("\nOTA end"); });
  ArduinoOTA.onError([](ota_error_t e){ Serial.printf("OTA error %u\n", e); });

  ArduinoOTA.begin();
  Serial.println("🌐 OTA ready (IDE Network Upload / espota.py)");
}


// ==================== LED Animator (via TinZrCore) ====================
#ifdef PIN_RGB_LED

void TinZrOTA::ledBegin() {
  _ledState = LedState::OFF;
  _ledT     = 0;
  _phase    = 0;
  _succCnt  = 0;
  TinZr.ledOff();
}

void TinZrOTA::ledSetState(LedState s) {
  if (_ledState == s) return;

  _ledState = s;
  _ledT     = 0;
  _phase    = 0;
  _succCnt  = 0;

  switch (s) {
    case LedState::OFF:
      TinZr.ledOff();
      break;

    case LedState::SUCCESS_STEADY:
      // steady green
      ledSetRGB(0, 255, 0);
      break;

    default:
      // animated states handled in ledUpdate()
      break;
  }
}

void TinZrOTA::ledUpdate() {
  uint32_t now = millis();

  switch (_ledState) {
    case LedState::SEARCHING: {
      // Smooth rainbow: advance every ~15 ms
      if (now - _ledT < 15) return;
      _ledT  = now;
      _phase = (_phase + 1) & 0xFF;   // 0..255

      uint8_t r, g, b;
      wheel((uint8_t)_phase, r, g, b);
      ledSetRGB(r, g, b);
    } break;

    case LedState::SUCCESS_STROBE: {
      // 5× green blinks, 150ms on / 150ms off, then steady green
      const uint16_t onMs  = 150;
      const uint16_t offMs = 150;

      if (_phase == 0) {
        ledSetRGB(0, 255, 0);
        _phase = 1;
        _ledT  = now;
      } else if (_phase == 1 && now - _ledT >= onMs) {
        ledSetRGB(0, 0, 0);
        _phase = 2;
        _ledT  = now;
      } else if (_phase == 2 && now - _ledT >= offMs) {
        _succCnt++;
        if (_succCnt >= 5) {
          ledSetState(LedState::SUCCESS_STEADY);
        } else {
          _phase = 0;   // next blink
        }
      }
    } break;

    case LedState::SUCCESS_STEADY:
      // nothing: already set in ledSetState
      break;

    case LedState::FAIL_BLINK: {
      // Blink red: 300ms on / 300ms off
      const uint16_t half = 300;
      if (now - _ledT < half) return;
      _ledT = now;

      static bool on = false;
      on = !on;
      ledSetRGB(on ? 255 : 0, 0, 0);
    } break;

    case LedState::OFF:
    default:
      // do nothing
      break;
  }
}

void TinZrOTA::ledSetRGB(uint8_t r, uint8_t g, uint8_t b) {
  // Delegate to Core – it owns the NeoPixel and brightness
  TinZr.setLED(r, g, b, _cfg.led_brightness);
}

void TinZrOTA::wheel(uint8_t pos, uint8_t& r, uint8_t& g, uint8_t& b) {
  // Same color logic as before, but now returns r,g,b instead of a packed uint32_t
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

#endif  // PIN_RGB_LED
