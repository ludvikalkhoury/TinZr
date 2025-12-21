#include "TinZrWiFi.h"
#include "TinZrCore.h"
#include "TinZrLED.h"


// Global instance
TinZrWiFiConnect TinZrWiFi;

// ---- helper: map wifi state -> LED mode ----
static TinZrStatusLED::Mode _wifiLedModeFromState(TinZrWiFiState s) {
	switch (s) {
		case TinZrWiFiState::OFF:
			return TinZrStatusLED::Mode::OFF;

		case TinZrWiFiState::CONNECTING:
			return TinZrStatusLED::Mode::SUCCESS_STROBE;  // flashing green (known working)

		case TinZrWiFiState::CONNECTED:
			return TinZrStatusLED::Mode::SUCCESS_STEADY;  // solid green (known working)

		case TinZrWiFiState::FAILED:
		default:
			return TinZrStatusLED::Mode::FAIL_BLINK;      // flashing red (known working)
	}
}



static const char* _wifiStateName(TinZrWiFiState s) {
	switch (s) {
		case TinZrWiFiState::OFF:        return "OFF";
		case TinZrWiFiState::CONNECTING: return "CONNECTING";
		case TinZrWiFiState::CONNECTED:  return "CONNECTED";
		case TinZrWiFiState::FAILED:     return "FAILED";
		default:                         return "UNKNOWN";
	}
}



void TinZrWiFiConnect::_applyLed(bool force) {
	static TinZrStatusLED::Mode last = TinZrStatusLED::Mode::OFF;
	TinZrStatusLED::Mode m = _wifiLedModeFromState(_state);
	if (!force && m == last) return;
	TinZrLED.setMode(m);
	last = m;
}


void TinZrWiFiConnect::begin(const TinZrWiFiConfig& cfg) {
	
	
	_cfg = cfg;
	
	
	Serial.println();
	Serial.println("=== TinZr WiFi ===");
	Serial.print("SSID: ");
	Serial.println(_cfg.ssid ? _cfg.ssid : "(null)");
	Serial.print("Hostname: ");
	Serial.println(_cfg.hostname ? _cfg.hostname : "(null)");
	Serial.print("Static IP: ");
	Serial.println(_cfg.use_static ? "yes" : "no");
	
	

	WiFi.mode(WIFI_STA);
	WiFi.setSleep(false);

	if (_cfg.hostname && _cfg.hostname[0] != '\0') {
		WiFi.setHostname(_cfg.hostname);
	}

	WiFi.setTxPower(_cfg.tx_power);

	_applyConfig();

	_state = TinZrWiFiState::OFF;
	_last_attempt_ms = 0;
	_retries = 0;

	_applyLed(true);
}

void TinZrWiFiConnect::_applyConfig() {
	if (_cfg.use_static) {
		WiFi.config(_cfg.static_ip, _cfg.gateway, _cfg.subnet, _cfg.dns1, _cfg.dns2);
	} else {
		if (_cfg.force_dhcp_config) {
			WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE);
		}
	}
}

void TinZrWiFiConnect::_startConnect() {
	if (!_cfg.ssid || _cfg.ssid[0] == '\0') {
		_state = TinZrWiFiState::FAILED;
		Serial.println("WiFi: ❌ SSID not set");
		_applyLed(true);
		return;
	}
	
	Serial.print("WiFi: connecting to ");
	Serial.println(_cfg.ssid);
	
	WiFi.begin(_cfg.ssid, _cfg.pass ? _cfg.pass : "");
	_state = TinZrWiFiState::CONNECTING;
	_last_attempt_ms = millis();

	_applyLed(true);
}

bool TinZrWiFiConnect::connect(uint32_t timeout_ms) {
	_startConnect();

	uint32_t t0 = millis();
	while (millis() - t0 < timeout_ms) {

		// OPTIONAL: keep LED animations alive during this blocking wait
		TinZr.handle(); 
		TinZrLED.handle();

		if (WiFi.status() == WL_CONNECTED) {
			_state = TinZrWiFiState::CONNECTED;
			_applyLed(true);
			
			Serial.print("WiFi: ✅ connected, IP=");
			Serial.println(WiFi.localIP());
			
			return true;
		}
		delay(10);
	}

	_state = TinZrWiFiState::FAILED;
	_applyLed(true);
	
	Serial.println("WiFi: ❌ connect timeout");
	
	return false;
}

void TinZrWiFiConnect::disconnect(bool wipe_driver_nvs) {
	Serial.println("WiFi: disconnect");
	
	WiFi.disconnect(true, wipe_driver_nvs);

	_state = TinZrWiFiState::OFF;
	_retries = 0;
	_last_attempt_ms = 0;

	_applyLed(true);
}

void TinZrWiFiConnect::handle() {
	// Keep LED animations alive even if the app forgets to call TinZr.handle()
	TinZr.handle();      // drives LED + core services (like SD does)
	TinZrLED.handle();

	// Update state from WiFi driver
	if (WiFi.status() == WL_CONNECTED) {
		if (_state != TinZrWiFiState::CONNECTED) {
			_state = TinZrWiFiState::CONNECTED;
			Serial.print("WiFi: state -> ");
			Serial.println(_wifiStateName(_state));
			_applyLed(true);
		}
		_retries = 0;
		return;
	}

	// Not connected
	if (_state == TinZrWiFiState::CONNECTED) {
		_state = TinZrWiFiState::CONNECTING;
		Serial.print("WiFi: state -> ");
		Serial.println(_wifiStateName(_state));
		_applyLed(true);
	}

	if (!_cfg.auto_reconnect) {
		if (_state != TinZrWiFiState::OFF && _state != TinZrWiFiState::FAILED) {
			_state = TinZrWiFiState::FAILED;
			_applyLed(true);
		}
		return;
	}

	uint32_t now = millis();
	if (now - _last_attempt_ms < _cfg.reconnect_ms) return;

	if (_cfg.max_retries != 0 && _retries >= _cfg.max_retries) {
		if (_state != TinZrWiFiState::FAILED) {
			_state = TinZrWiFiState::FAILED;
			_applyLed(true);
		}
		return;
	}

	_retries++;
	_startConnect();
}
