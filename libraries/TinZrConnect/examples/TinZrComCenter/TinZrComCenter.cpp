#include "TinZrComCenter.h"
#include <string.h>

TinZrWiFiCom* TinZrWiFiCom::_self = nullptr;

TinZrWiFiCom::TinZrWiFiCom() {
	// no-op
}

TinZrWiFiCom WiFiCom;

void TinZrWiFiCom::begin(const TinZrWiFiConfig& cfg) {
	_cfg = cfg;
	_self = this;

	Serial.begin(115200);
	delay(200);

	Serial.println();
	Serial.println("===== TinZr Com Center Node (WiFi TCP+DPU) =====");

	TinZr.begin();

#if TINZR_ENABLE_WIFI
	// ✅ Use the full config as-is (single source of truth)
	TinZrWiFi.begin(_cfg);
	TinZrWiFi.connect(15000);
	
	Serial.print("WiFi IP: ");
	Serial.println(TinZrWiFi.ip());
	
	// Use TinZrWiFi-owned helpers (NOT node-owned)
	TinZrWiFiMcast& mcast = TinZrWiFi.mcast();
	TinZrWiFiTCP&   tcp   = TinZrWiFi.tcp();

	



	// If you want discovery, start mcast via TinZrWiFi helper
	if (_cfg.mcast_enable) {
		mcast.end();
		if (mcast.begin(_cfg.mcast_group, _cfg.mcast_port)) {
			mcast.setName(_cfg.hostname ? _cfg.hostname : "tinzr");
			mcast.setHelloInterval(_cfg.mcast_hello_ms);
			mcast.sendHello();
		} else {
			Serial.println("TinZrNode: ❌ mcast begin failed");
		}
	}

	// Decide hub IP (override or discovery)
	IPAddress hub = _cfg.hub_ip;
	if (hub == IPAddress(0,0,0,0) && _cfg.mcast_enable) {
		hub = mcast.hubIP();
	}

	// Configure TCP client
	tcp.setHub(hub, _cfg.tcp_port);
	tcp.setTimeout(_cfg.tcp_connect_timeout_ms, _cfg.tcp_io_timeout_ms);
	tcp.onFrame(_onTcpFrame);

	_netStarted = true;
	_tcpWasUp = tcp.connect();

	// Send HELLO over TCP after connect so hub learns hostname reliably
	if (_tcpWasUp) {
		const char* name = _cfg.hostname ? _cfg.hostname : "tinzr";
		char hello[96];
		snprintf(hello, sizeof(hello), "HELLO %s", name);
		tcp.sendDPU(1, (const uint8_t*)hello, strlen(hello), false);
	}

	Serial.print("Hub IP (initial): ");
	Serial.println(hub);
#else
	Serial.println("TinZrNode: WiFi disabled at build time");
	_netStarted = false;
#endif
}



void TinZrWiFiCom::handle() {
	TinZr.handle();

#if TINZR_ENABLE_WIFI
	// Drive WiFi state machine (includes mcast + tcp helpers)
	TinZrWiFi.handle();

	if (!_netStarted) return;

	TinZrWiFiMcast& mcast = TinZrWiFi.mcast();
	TinZrWiFiTCP&   tcp   = TinZrWiFi.tcp();

	// If discovering hub, let mcast run and update TCP hub when found
	if (_cfg.mcast_enable && _cfg.hub_ip == IPAddress(0,0,0,0)) {
		// TinZrWiFi.handle() already calls mcast.handle(), but calling it again is harmless
		// If you want, you can remove this next line entirely.
		mcast.handle();

		if (mcast.hasHub()) {
			IPAddress hub = mcast.hubIP();
			if (tcp.hubIP() != hub) {
				Serial.print("Discovered hub: ");
				Serial.println(hub);
				tcp.setHub(hub, _cfg.tcp_port);

				// Optional: force reconnect to new hub
				tcp.disconnect();
			}
		}
	}

	// TCP connect/handle
	bool tcpNow = tcp.connected();
	if (!tcpNow) {
		tcp.connect();
		tcpNow = tcp.connected();
	}
	tcp.handle();

	// Connection state prints
	if (_tcpWasUp && !tcpNow) {
		Serial.println("TCP dropped");
		_tcpWasUp = false;
	} else if (!_tcpWasUp && tcpNow) {
		Serial.println("TCP connected");
		_tcpWasUp = true;

		// ✅ Send HELLO on every (re)connect so hub learns the name
		const char* name = _cfg.hostname ? _cfg.hostname : "tinzr";
		char hello[96];
		snprintf(hello, sizeof(hello), "HELLO %s", name);
		tcp.sendDPU(1, (const uint8_t*)hello, strlen(hello), false);
	}

	_handleButtonToHub();
#endif
}



void TinZrWiFiCom::_handleButtonToHub() {
	// Preserve your logic: on rising edge send a BTN LED message.
	// PB_PIN is defined by TinZrCore (your board mapping).
	bool pressed = (digitalRead(PB_PIN) == LOW);

	if (pressed && !_lastButtonPressed) {
		char buf[64];
		snprintf(buf, sizeof(buf), "BTN LED %u %u %u %u",
			(unsigned)_curR,
			(unsigned)_curG,
			(unsigned)_curB,
			(unsigned)_curBr);

		Serial.print("Button press -> ");
		Serial.println(buf);

		// DPU type=1 is "text" in this example
		TinZrWiFiTCP& tcp = TinZrWiFi.tcp();
		tcp.sendDPU(1, (const uint8_t*)buf, strlen(buf), false);
	}

	_lastButtonPressed = pressed;
}

void TinZrWiFiCom::_onTcpFrame(IPAddress from, uint16_t type, const uint8_t* payload, size_t len) {
	if (!_self) return;
	if (type == 1) {
		_self->_handleHubText(from, payload, len);
	}
}

void TinZrWiFiCom::_handleHubText(IPAddress from, const uint8_t* data, size_t len) {
	if (!data || len == 0) return;

	// Copy to null-terminated buffer
	char buf[256];
	size_t n = (len < (sizeof(buf) - 1)) ? len : (sizeof(buf) - 1);
	memcpy(buf, data, n);
	buf[n] = '\0';

	String s(buf);
	s.trim();
	if (s.length() == 0) return;

	// Helper to respond (text frames use DPU type=1 in your code)
	auto sendText = [&](const char* msg) {
		TinZrWiFiTCP& tcp = TinZrWiFi.tcp();
		tcp.sendDPU(1, (const uint8_t*)msg, strlen(msg), false);
	};

	// ----------------------------
	// ON / OFF (soft power)
	// ----------------------------
	if (s.equalsIgnoreCase("ON") || s.equalsIgnoreCase("SOFTON")) {
		TinZr.softOn();
		sendText("OK ON");
		return;
	}

	if (s.equalsIgnoreCase("OFF") || s.equalsIgnoreCase("SOFTOFF")) {
		TinZr.softOff();
		sendText("OK OFF");
		return;
	}

	// ----------------------------
	// LED_OFF  (legacy)
	// ----------------------------
	if (s.equalsIgnoreCase("LED_OFF")) {
		_curR = _curG = _curB = 0;
		// keep brightness as-is
		TinZrLED.setColor(_curR, _curG, _curB, _curBr);
		sendText("OK LED_OFF");
		return;
	}
	
	
	// ------------------------------------
	// LED_FLASH r g b [br] [nbr_flashes]
	// ------------------------------------
	if (s.startsWith("LED_FLASH")) {
		uint32_t r=0,g=0,b=0,br=0,n_flashes=0;
		int got = sscanf(s.c_str(), "LED_FLASH %lu %lu %lu %lu %lu", &r, &g, &b, &br, &n_flashes);

		// Need at least r g b
		if (got >= 3) {
			_curR = (uint8_t)r;
			_curG = (uint8_t)g;
			_curB = (uint8_t)b;

			// Optional brightness
			if (got >= 4) {
				_curBr = (uint8_t)br;
			}
			// Optional number of flashes
			if (got >= 5) {
				_curNflashes = (uint8_t)n_flashes;
			} else {
				// Default flashes if not provided
				_curNflashes = 5;
			}

			TinZrLED.flashColor(_curR, _curG, _curB, _curBr, _curNflashes);

			char resp[96];
			snprintf(resp, sizeof(resp),
				"OK LED_FLASH %u %u %u %u %u",
				_curR, _curG, _curB, _curBr, _curNflashes
			);
			sendText(resp);
		} else {
			sendText("ERR LED_FLASH");
		}
		return;
	}
	
	
	// ----------------------------
	// LED r g b [br]
	// ----------------------------
	if (s.startsWith("LED")) {
		uint32_t r=0,g=0,b=0,br=0;
		int got = sscanf(s.c_str(), "LED %lu %lu %lu %lu", &r, &g, &b, &br);
		if (got >= 3) {
			_curR  = (uint8_t)r;
			_curG  = (uint8_t)g;
			_curB  = (uint8_t)b;
			_curBr = (got >= 4) ? (uint8_t)br : _curBr;

			TinZrLED.setColor(_curR, _curG, _curB, _curBr);

			char resp[96];
			snprintf(resp, sizeof(resp), "OK LED %u %u %u %u", _curR, _curG, _curB, _curBr);
			sendText(resp);
		} else {
			sendText("ERR LED");
		}
		return;
	}
	
	
	// ----------------------------
	// PING
	// ----------------------------
	if (s.equalsIgnoreCase("PING")) {
		sendText("PONG");
		return;
	}

	// ----------------------------
	// BAT (legacy)
	// ----------------------------
	if (s.equalsIgnoreCase("BAT") || s.equalsIgnoreCase("BAT?")) {
		int   perc = TinZr.readBatteryPercent();
		float volt = TinZr.readBatteryVoltage();

		char resp[96];
		snprintf(resp, sizeof(resp), "BAT %d %% %.2f V", perc, volt);
		sendText(resp);
		return;
	}

	// ----------------------------
	// DIG <pin> <0/1>
	// Example: "DIG 5 1"
	// Replies: "DIG <pin> <val>"
	// ----------------------------
	if (s.startsWith("DIG ")) {
		int pin = -1;
		int val = -1;
		int got = sscanf(s.c_str(), "DIG %d %d", &pin, &val);
		if (got == 2 && pin >= 0 && (val == 0 || val == 1)) {
			pinMode(pin, OUTPUT);
			digitalWrite(pin, val ? HIGH : LOW);

			char resp[64];
			snprintf(resp, sizeof(resp), "DIG %d %d", pin, val);
			sendText(resp);
		} else {
			sendText("ERR DIG");
		}
		return;
	}

	// ----------------------------
	// ANA <pin>            -> read
	// ANA <pin> <value>    -> write (best-effort analogWrite if available)
	//
	// Example read:  "ANA 1"
	// Reply:         "ANA 1 <value>"
	//
	// Example write: "ANA 25 128"
	// Reply:         "ANA 25 128"
	// ----------------------------
	if (s.startsWith("ANA ")) {
		int pin = -1;
		int val = -1;
		int got = sscanf(s.c_str(), "ANA %d %d", &pin, &val);

		if (got >= 1 && pin >= 0) {
			if (got == 1) {
				// READ
				int aval = analogRead(pin);

				char resp[96];
				snprintf(resp, sizeof(resp), "ANA %d %d", pin, aval);
				sendText(resp);
			} else {
				// WRITE (best-effort)
				// NOTE: On ESP32-C3, analogWrite may or may not be provided in your core.
				// If not, you'll need LEDC. For now we do best-effort analogWrite.
				#ifdef analogWrite
					pinMode(pin, OUTPUT);
					analogWrite(pin, val);
				#else
					// If your core doesn't support analogWrite, you can still acknowledge
					// and implement LEDC later.
				#endif

				char resp[96];
				snprintf(resp, sizeof(resp), "ANA %d %d", pin, val);
				sendText(resp);
			}
		} else {
			sendText("ERR ANA");
		}
		return;
	}

	// Unknown command
	Serial.print("Hub msg from ");
	Serial.print(from);
	Serial.print(": ");
	Serial.println(s);
}
