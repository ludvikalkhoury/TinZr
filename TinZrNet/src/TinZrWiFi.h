// TinZrWiFi.h
#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>

#include "TinZrConfig.h"

// Common Wi-Fi config (very similar to TinZrCfg, but without OTA + LED stuff)
struct TinZrWiFiConfig {
	const char*  ssid       = "";
	const char*  pass       = "";
	bool        use_static  = false;

	IPAddress   ip   = IPAddress(192, 168, 1, 40);
	IPAddress   gw   = IPAddress(192, 168, 1, 1);
	IPAddress   mask = IPAddress(255, 255, 255, 0);
	IPAddress   dns1 = IPAddress(8, 8, 8, 8);
	IPAddress   dns2 = IPAddress(8, 8, 4, 4);

	wifi_power_t tx_power = WIFI_POWER_8_5dBm;
};

// Simple Wi-Fi manager used by both Console and OTA
class TinZrWiFi {
public:
	TinZrWiFi() = default;

	void begin(const TinZrWiFiConfig& cfg) {
		_cfg = cfg;
	}

	// Connect with a timeout; optional "tick" callback runs inside the wait loop
	// so OTA can do ArduinoOTA.handle() + LED updates.
	typedef void (*TickCallback)();

	bool connect(uint32_t timeout_ms, TickCallback tick = nullptr) {
		// ---- init Wi-Fi in RAM only, like you do now ----
		WiFi.persistent(false);
		esp_wifi_set_storage(WIFI_STORAGE_RAM);
		WiFi.setAutoReconnect(true);
		WiFi.mode(WIFI_STA);
		WiFi.setSleep(false);
		WiFi.setTxPower(_cfg.tx_power);
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
		while (WiFi.status() != WL_CONNECTED &&
		       (millis() - start) < timeout_ms) {
			delay(50);
			if (tick) {
				tick();  // OTA/LED can hook here
			}
		}

		if (WiFi.status() == WL_CONNECTED) {
			Serial.println("✅ Wi-Fi connected!");
			Serial.print("📡 IP: ");
			Serial.println(WiFi.localIP());
			return true;
		} else {
			Serial.println("❌ Wi-Fi connect timeout");
			return false;
		}
	}

	bool connected() const {
		return WiFi.status() == WL_CONNECTED;
	}

	IPAddress ip() const {
		return WiFi.localIP();
	}

private:
	TinZrWiFiConfig _cfg;
};
