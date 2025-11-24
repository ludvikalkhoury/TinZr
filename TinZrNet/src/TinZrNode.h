#pragma once
#include <Arduino.h>
#include <WiFi.h>

#include "TinZrConfig.h"
#include "TinZrCore.h"
#include "TinZrLED.h"
#include "TinZrConnections.h"
#include "TinZrConsole.h"

struct TinZrNodeConfig {
	const char* ssid       = "connect123";
	const char* pass       = "connect123";
	const char* hostname   = "TinZrNode1";
	bool        use_static = false;

	uint16_t    hubTcpPort  = 4211;
	uint16_t    hubUdpPort  = 4210;
	IPAddress   hubMcastGrp = IPAddress(239, 1, 1, 1);
	IPAddress   hubIP       = IPAddress(0, 0, 0, 0);
};

class TinZrNode {
public:
	TinZrNode();
	void begin(const TinZrNodeConfig& cfg);
	void handle();

private:
	TinZrNodeConfig   _cfg{};
	TinZrConsole      _console;
	TinZrConnect      _net;

#if TINZR_ENABLE_BLE
	TinZrBleConnect   _ble;
#endif

	TinZrStatusLED    _statusLED;
	TinZrHubCommands  _hubCmd;
	TinZrLink*        _link       = nullptr;

	bool              _netStarted = false;

	// 🔹 NEW: track last-known connection state
	bool              _wifiWasUp = false;
#if TINZR_ENABLE_BLE
	bool              _bleWasConnected = false;
#endif

	bool              _lastButtonPressed = false;

	void _handleButtonToHub();
};
