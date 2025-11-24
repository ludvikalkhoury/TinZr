#pragma once

#include "TinZrConfig.h"

#include <Arduino.h>
#include "TinZrCore.h"
#include "TinZrConsole.h"
#include "TinZrHubCommands.h"
#include "TinZrLink.h"
#include "TinZrStatusLED.h"   

#if TINZR_ENABLE_WIFI
  #include <WiFi.h>
  #include "TinZrConnect.h"
#endif

#if TINZR_ENABLE_BLE
  #include "TinZrBleConnect.h"
#endif

// High-level config for the node.
// All user-facing settings live here (no TinZrConsoleDefaults inside).
struct TinZrNodeConfig {
	// --- Wi-Fi / identity (used only if TINZR_ENABLE_WIFI == 1) ---
	const char* ssid       = "";
	const char* pass       = "";
	const char* hostname   = "TinZrNode";
	bool        use_static = false;

	// --- Hub discovery / ports (Wi-Fi path only) ---
	uint16_t  hubTcpPort   = 4211;
	uint16_t  hubUdpPort   = 4210;
	IPAddress hubMcastGrp  = IPAddress(239, 1, 1, 1);

#if TINZR_ENABLE_WIFI
	IPAddress hubIP        = IPAddress(172, 20, 10, 4);
#endif
};

class TinZrNode {
public:
	TinZrNode();

	// Call from Arduino setup()
	void begin(const TinZrNodeConfig& cfg);

	// Call from Arduino loop()
	void handle();

	TinZrConsole&     console() { return _console; }
#if TINZR_ENABLE_WIFI
	TinZrConnect&     net()     { return _net; }
#endif
	TinZrHubCommands& hubCmd()  { return _hubCmd; }

private:
	TinZrNodeConfig _cfg{};
	bool _netStarted = false;

	TinZrConsole    _console;

#if TINZR_ENABLE_WIFI
	TinZrConnect    _net;   // Wi-Fi link
#endif

#if TINZR_ENABLE_BLE
	TinZrBleConnect _ble;   // BLE link
#endif

	TinZrLink*      _link = nullptr;   // active link (Wi-Fi, BLE, or null)

	TinZrHubCommands _hubCmd;
    TinZrStatusLED   _statusLED;  
	
	bool _lastButtonPressed = false;
	void _handleButtonToHub();
};
