#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <esp_wifi.h>

#include "TinZrConfig.h"
#include "TinZrOTA.h"   // TinZrOTA handles Wi-Fi+OTA when TINZR_ENABLE_OTA=1, stubs when 0

#include "TinZrWiFi.h"  // new include

class TinZrCore;
class TinZrConnect;

// Optional knobs for default behavior
#ifndef TINZR_AUTOSAVE_WIFI_ON
#define TINZR_AUTOSAVE_WIFI_ON true   // true = WIFI command will save+reboot
#endif

struct TinZrConsoleDefaults {
	const char* ssid       = "Ludvik";
	const char* pass       = "Lud12345";
	const char* hostname   = "esp32c3-ota";
	bool        use_static = false;
};

class TinZrConsole {
public:
	TinZrConsole() : _autosave_wifi(TINZR_AUTOSAVE_WIFI_ON) {}

	// Initialize console, load saved settings (if any), bring up Wi-Fi (and OTA if enabled)
	void begin(const TinZrConsoleDefaults& def, uint32_t connect_timeout_ms = 15000);

	// Call regularly (from loop)
	void handle();

	// Runtime config
	void setAutosaveWifi(bool on) { _autosave_wifi = on; }

	// Network link
	void attachNet(TinZrConnect* net) { _net = net; }

	// Console basic info
	const String& getHostname() const { return _host; }

	// Connection helpers
	bool connected() const {
#if TINZR_ENABLE_OTA
		// When OTA is compiled in, delegate to TinZrOTA (it also owns Wi-Fi in that path)
		return _ota.connected();
#else
		// When OTA is off at compile time, just use raw Wi-Fi status
		return (WiFi.status() == WL_CONNECTED);
#endif
	}

	bool ready() const {
#if TINZR_ENABLE_OTA
		// "Ready" = Wi-Fi + OTA + LED state machine OK
		return _ota.ready();
#else
		// Without OTA, ready means Wi-Fi is connected
		return (WiFi.status() == WL_CONNECTED);
#endif
	}

	IPAddress ip() const {
#if TINZR_ENABLE_OTA
		return _ota.ip();
#else
		return WiFi.localIP();
#endif
	}

	// Core attachment (for LED / battery / soft power commands)
	void attachCore(TinZrCore* core) { _core = core; }

private:
	// ---- Members ----

    TinZrWiFi     _wifi;   // <--- new
    Preferences   _prefs;
	TinZrCore*    _core = nullptr;
	TinZrConnect* _net  = nullptr;

	String  _ssid;
	String  _pass;
	String  _host;
	bool    _use_static    = false;
	bool    _autosave_wifi = false;

	// ---- Internal flows ----
	void applyConfig(uint32_t connect_timeout_ms = 15000);
	void saveToNVS();
	bool loadFromNVS();
	void wipeNVS();
	void wipeWiFiDriverNVS();

	// ---- Serial UI ----
	void showConfig();
	void handleSerial();
	void printHelp(bool with_header = true);
};
