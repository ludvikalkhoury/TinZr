#pragma once
#include <Arduino.h>
#include <WiFi.h>

enum class TinZrWiFiState : uint8_t {
	OFF = 0,
	CONNECTING,
	CONNECTED,
	FAILED
};

struct TinZrWiFiConfig {
	const char* ssid              = nullptr;
	const char* pass              = nullptr;
	const char* hostname          = "tinzr";
	bool        use_static        = false;

	// Optional static configuration (only used if use_static=true)
	IPAddress   static_ip         = IPAddress(192,168,1,50);
	IPAddress   gateway           = IPAddress(192,168,1,1);
	IPAddress   subnet            = IPAddress(255,255,255,0);
	IPAddress   dns1              = IPAddress(8,8,8,8);
	IPAddress   dns2              = IPAddress(1,1,1,1);

	// Behavior
	bool        auto_reconnect    = true;
	uint32_t    reconnect_ms      = 3000;   // retry interval
	uint8_t     max_retries       = 0;      // 0 = infinite

	// RF tuning
	wifi_power_t tx_power         = WIFI_POWER_8_5dBm;

	// If true, force WiFi.config() even for DHCP path (some networks behave better)
	bool        force_dhcp_config = true;
};

class TinZrWiFiConnect {
public:
	TinZrWiFiConnect() = default;

	void begin(const TinZrWiFiConfig& cfg);
	bool connect(uint32_t timeout_ms = 15000);
	void disconnect(bool wipe_driver_nvs = false);

	void handle();

	bool ready() const { return _state == TinZrWiFiState::CONNECTED; }
	bool connected() const { return WiFi.status() == WL_CONNECTED; }

	TinZrWiFiState state() const { return _state; }
	IPAddress ip() const { return WiFi.localIP(); }
	int8_t rssi() const { return WiFi.RSSI(); }

	const char* hostname() const { return _cfg.hostname; }

private:
	TinZrWiFiConfig _cfg{};
	TinZrWiFiState  _state = TinZrWiFiState::OFF;

	uint32_t _last_attempt_ms = 0;
	uint8_t  _retries = 0;

	void _startConnect();
	void _applyConfig();
	void _applyLed(bool force = false);
};

extern TinZrWiFiConnect TinZrWiFi;
