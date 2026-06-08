#pragma once

#include <Arduino.h>

#ifndef TINZR_ENABLE_BLE
	#define TINZR_ENABLE_BLE 1
#endif

#if TINZR_ENABLE_BLE
	#include <BLEDevice.h>
	#include <BLEServer.h>
	#include <BLEUtils.h>
	#include <BLE2902.h>
#endif

// ============================================================
// TinZrBLEState
// ============================================================
enum class TinZrBLEState : uint8_t {
	OFF = 0,
	STARTING,
	ADVERTISING,
	CONNECTED,
	FAILED
};

// ============================================================
// TinZrBLEConfig
// ============================================================
struct TinZrBLEConfig {
	const char* device_name  = "TinZr";
	const char* service_uuid = "4fafc201-1fb5-459e-8fcc-c5c9c331914b";
	const char* rx_uuid      = "beb5483e-36e1-4688-b7f5-ea07361b26a8"; // WRITE (hub -> device)
	const char* tx_uuid      = "beb5483e-36e1-4688-b7f5-ea07361b26a9"; // NOTIFY (device -> hub)

	bool     auto_restart_advertising = true;
	bool     scan_response            = true;
	// If MTU allows, set this to MTU - 3 so each application packet fits
	// in one notification. Otherwise TinZrBLEGatt::notify() chunks safely.
	uint16_t max_notify_chunk         = 220;
	uint16_t preferred_mtu            = 0; // 0 = do not call setMTU()
};

// ============================================================
// TinZrBLEGatt
// ============================================================
class TinZrBLEGatt
#if TINZR_ENABLE_BLE
	: public BLEServerCallbacks,
	  public BLECharacteristicCallbacks
#endif
{
public:
	using WriteHandler = void (*)(const uint8_t* data, size_t len);

	TinZrBLEGatt();

	bool begin(const TinZrBLEConfig& cfg);
	void end();
	void handle();

	bool advertising() const { return _state == TinZrBLEState::ADVERTISING; }
	bool connected()   const { return _state == TinZrBLEState::CONNECTED; }
	TinZrBLEState state() const { return _state; }

	void onWrite(WriteHandler cb) { _onWrite = cb; }
	bool notify(const uint8_t* data, size_t len);

private:
	TinZrBLEConfig _cfg{};
	TinZrBLEState  _state = TinZrBLEState::OFF;
	WriteHandler   _onWrite = nullptr;

#if TINZR_ENABLE_BLE
	BLEServer*         _server  = nullptr;
	BLEService*        _service = nullptr;
	BLECharacteristic* _rx      = nullptr;
	BLECharacteristic* _tx      = nullptr;
	BLEAdvertising*    _adv     = nullptr;

	void onConnect(BLEServer* server) override;
	void onDisconnect(BLEServer* server) override;
	void onWrite(BLECharacteristic* ch) override;
#endif

	bool _startAdvertising();
};

// ============================================================
// TinZrBLEConnect
// ============================================================
class TinZrBLEConnect {
public:
	TinZrBLEConnect() = default;

	bool begin(const TinZrBLEConfig& cfg);
	void end();
	void handle();

	bool ready() const { return _gatt.connected(); }
	bool connected() const { return _gatt.connected(); }
	TinZrBLEState state() const { return _gatt.state(); }

	bool sendNotify(const uint8_t* data, size_t len) { return _gatt.notify(data, len); }
	void onWrite(TinZrBLEGatt::WriteHandler cb) { _gatt.onWrite(cb); }

	TinZrBLEGatt& gatt() { return _gatt; }
	const TinZrBLEGatt& gatt() const { return _gatt; }

private:
	TinZrBLEGatt _gatt;
};

extern TinZrBLEConnect TinZrBLE;
