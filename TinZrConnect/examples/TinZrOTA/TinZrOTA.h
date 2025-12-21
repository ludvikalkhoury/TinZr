#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoOTA.h>

struct TinZrOTAConfig {
	uint16_t    port     = 3232;
	const char* password = nullptr; // nullptr = no password
	bool        enabled  = true;
};

class TinZrOTAConnect {
public:
	TinZrOTAConnect() = default;

	// Convenience: start with defaults
	void begin(const char* hostname) {
		begin(hostname, TinZrOTAConfig{});
	}

	// Full control
	void begin(const char* hostname, const TinZrOTAConfig& cfg);
	
	void handle();

	bool ready() const { return _ready; }
	bool connected() const { return WiFi.status() == WL_CONNECTED; }
	IPAddress ip() const { return WiFi.localIP(); }

private:
	TinZrOTAConfig _cfg{};
	bool _ready = false;
};

extern TinZrOTAConnect TinZrOTA;
