#pragma once

#include "TinZrConfig.h"   // feature flags: TINZR_ENABLE_BLE, etc.

#if TINZR_ENABLE_BLE      // 👈 only compile this file if BLE is enabled

#include <Arduino.h>
#include <IPAddress.h>
#include "TinZrLink.h"

// Classic Arduino-ESP32 BLE stack (the one you're already using)
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// You can change these if you want,
// but they must match on the hub side.
#define TINZR_BLE_SERVICE_UUID   "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
// Separate RX/TX characteristics so we can have full duplex
#define TINZR_BLE_RX_CHAR_UUID   "beb5483e-36e1-4688-b7f5-ea07361b26a8"
#define TINZR_BLE_TX_CHAR_UUID   "beb5483e-36e1-4688-b7f5-ea07361b26a9"

class TinZrBleConnect : public TinZrLink {
public:
	using MsgHandler = TinZrLink::MsgHandler;

	TinZrBleConnect() = default;

	// Start BLE advertising / service
	bool start() override;

	// Pump any internal state if needed (BLE is mostly event-driven)
	void handle() override;

	// Send best-effort message to connected central (notify)
	void sendUDP(const uint8_t* data, size_t len) override;

	// For BLE, sendTCP is same as sendUDP; we just report "1 peer"
	int sendTCP(const uint8_t* data, size_t len,
	            uint32_t timeoutMs = 200) override;

	// Register inbound message handler (called when central writes to RX)
	void onMessage(MsgHandler cb) override { _onMsg = cb; }
	
	void setName(const char* n) { _bleName = n; }
	
private:
	MsgHandler         _onMsg           = nullptr;
	BLEServer*         _server          = nullptr;
	BLECharacteristic* _rxChr           = nullptr;  // central writes commands here
	BLECharacteristic* _txChr           = nullptr;  // node notifies replies here
	bool               _deviceConnected = false;

	static TinZrBleConnect* _self;
	
	const char* _bleName = "TinZrBLE";
	
	// Internal event handlers
	void _handleConnect(BLEServer* s);
	void _handleDisconnect(BLEServer* s);
	void _handleRx(const uint8_t* data, size_t len);

	// Static trampolines for BLE callbacks
	static void _onRxWriteStatic(BLECharacteristic* chr);

	// Small callback classes that forward to TinZrBleConnect
	class ServerCallbacks : public BLEServerCallbacks {
	public:
		void onConnect(BLEServer* server) override;
		void onDisconnect(BLEServer* server) override;
	};

	class RxCallbacks : public BLECharacteristicCallbacks {
	public:
		void onWrite(BLECharacteristic* chr) override;
	};
};

#endif // TINZR_ENABLE_BLE
