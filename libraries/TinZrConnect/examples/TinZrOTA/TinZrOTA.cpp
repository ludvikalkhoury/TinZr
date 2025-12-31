#include "TinZrOTA.h"
#include "TinZrWiFi.h"
#include "TinZrLED.h"


TinZrOTAConnect TinZrOTA;

void TinZrOTAConnect::begin(const char* hostname, const TinZrOTAConfig& cfg) {
		
	_cfg = cfg;
	_ready = false;

	if (!_cfg.enabled) {
		Serial.println("OTA: disabled");
		return;
	}

	// You can call begin() before WiFi is connected; OTA will only work once WiFi is up.
	if (hostname && hostname[0] != '\0') {
		ArduinoOTA.setHostname(hostname);
	}

	ArduinoOTA.setPort(_cfg.port);

	if (_cfg.password && _cfg.password[0] != '\0') {
		ArduinoOTA.setPassword(_cfg.password);
	}

	ArduinoOTA
		.onStart([]() {
			// Keep handlers lightweight; OTA is timing sensitive.
			Serial.println("OTA: start");
		})
		.onEnd([]() {
			Serial.println("OTA: end");
		})
		.onProgress([](unsigned int progress, unsigned int total) {
			// Throttle prints to avoid spamming / slowing OTA
			static uint32_t lastPrint = 0;
			uint32_t now = millis();
			if (now - lastPrint < 500) return;
			lastPrint = now;

			uint32_t pct = 0;
			if (total > 0) pct = (progress * 100UL) / total;

			Serial.printf("OTA: %lu %%\n", (unsigned long)pct);
		})
		.onError([](ota_error_t error) {
			Serial.printf("OTA: error %u\n", (unsigned)error);
		});

	ArduinoOTA.begin();
	_ready = true;

	Serial.print("OTA: ready (port=");
	Serial.print(_cfg.port);
	Serial.println(")");
}

void TinZrOTAConnect::handle() {
	TinZrWiFi.handle();
	
	if (!_cfg.enabled) return;
	if (!_ready) return;

	// OTA only works when WiFi is connected
	if (WiFi.status() != WL_CONNECTED) return;

	ArduinoOTA.handle();
}
