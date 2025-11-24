#pragma once
#include <Arduino.h>
#include <IPAddress.h>

// Generic transport interface for TinZr networking.
// Both Wi-Fi (TinZrConnect) and BLE (TinZrBleConnect) will implement this.
class TinZrLink {
public:
	// Callback for incoming messages
	// "from" is the sender IP (for BLE we can use 0.0.0.0)
	using MsgHandler = void (*)(IPAddress from, const uint8_t* data, size_t len);

	virtual ~TinZrLink() {}

	// Start the link (Wi-Fi or BLE). Return true on success.
	virtual bool start() = 0;

	// Pump the link state machine. Call this regularly from loop().
	virtual void handle() = 0;

	// Best-effort message (UDP-style, or BLE notify).
	virtual void sendUDP(const uint8_t* data, size_t len) = 0;

	// Reliable-ish message (TCP-style, or same as UDP for BLE).
	// Returns number of peers/targets we delivered to.
	virtual int sendTCP(const uint8_t* data, size_t len,
	                    uint32_t timeoutMs = 200) = 0;

	// Register inbound message callback.
	virtual void onMessage(MsgHandler cb) = 0;
};
