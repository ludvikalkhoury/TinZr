#pragma once

#include <Arduino.h>
#include <WiFi.h>

#include "TinZrConfig.h"
#include "TinZrWiFi.h"
#include "TinZrCore.h"
#include "TinZrLED.h"



// ============================================================
// TinZrNodeConfig
// ------------------------------------------------------------
// Minimal Wi-Fi + hub connection parameters for the Com Center
// node example. Keeps your existing behavior but uses the new
// WiFi stack (TinZrWiFiConnect + TCP(DPU) + mcast discovery).
// ============================================================


class TinZrWiFiCom{
public:
	TinZrWiFiCom();
	void begin(const TinZrWiFiConfig& cfg);
	void handle();

private:
	
	TinZrWiFiConfig _cfg{};


	bool _netStarted = false;
	bool _tcpWasUp   = false;
	bool _lastButtonPressed = false;

	// Track last known LED values so BTN LED payload stays consistent
	uint8_t _curR  = 0;
	uint8_t _curG  = 0;
	uint8_t _curB  = 0;
	uint8_t _curBr = 0;
	uint8_t _curNflashes = 0;
	
	void _handleButtonToHub();
	void _handleHubText(IPAddress from, const uint8_t* data, size_t len);

	static void _onTcpFrame(IPAddress from, uint16_t type, const uint8_t* payload, size_t len);
	static TinZrWiFiCom* _self;
};

extern TinZrWiFiCom WiFiCom;