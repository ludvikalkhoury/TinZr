#include "TinZrBleConnect.h"
#include "TinZrConfig.h"

#if TINZR_ENABLE_BLE   // 👈 only compile this file when BLE is enabled

TinZrBleConnect* TinZrBleConnect::_self = nullptr;

// -------- ServerCallbacks --------

void TinZrBleConnect::ServerCallbacks::onConnect(BLEServer* server) {
	if (TinZrBleConnect::_self) {
		TinZrBleConnect::_self->_handleConnect(server);
	}
}

void TinZrBleConnect::ServerCallbacks::onDisconnect(BLEServer* server) {
	if (TinZrBleConnect::_self) {
		TinZrBleConnect::_self->_handleDisconnect(server);
	}
}

// -------- RxCallbacks --------

void TinZrBleConnect::RxCallbacks::onWrite(BLECharacteristic* chr) {
	if (TinZrBleConnect::_self) {
		TinZrBleConnect::_self->_onRxWriteStatic(chr);
	}
}

// -------- TinZrBleConnect implementation --------

bool TinZrBleConnect::start() {
	_self = this;

	BLEDevice::init(_bleName ? _bleName : "TinZrBLE");


	_server = BLEDevice::createServer();
	if (!_server) {
		Serial.println("❌ TinZrBleConnect: createServer() failed");
		return false;
	}
	_server->setCallbacks(new ServerCallbacks());

	BLEService* svc = _server->createService(TINZR_BLE_SERVICE_UUID);
	if (!svc) {
		Serial.println("❌ TinZrBleConnect: createService() failed");
		return false;
	}

	_rxChr = svc->createCharacteristic(
	    TINZR_BLE_RX_CHAR_UUID,
	    BLECharacteristic::PROPERTY_WRITE |
	    BLECharacteristic::PROPERTY_WRITE_NR
	);
	if (!_rxChr) {
		Serial.println("❌ TinZrBleConnect: create RX characteristic failed");
		return false;
	}
	_rxChr->setCallbacks(new RxCallbacks());

	_txChr = svc->createCharacteristic(
	    TINZR_BLE_TX_CHAR_UUID,
	    BLECharacteristic::PROPERTY_NOTIFY
	);
	if (!_txChr) {
		Serial.println("❌ TinZrBleConnect: create TX characteristic failed");
		return false;
	}
	_txChr->addDescriptor(new BLE2902());

	svc->start();

	BLEAdvertising* adv = BLEDevice::getAdvertising();
	adv->addServiceUUID(TINZR_BLE_SERVICE_UUID);
	adv->setScanResponse(true);
	adv->setMinPreferred(0x06);
	adv->setMinPreferred(0x00);
	adv->start();

	Serial.println("🔵 TinZrBleConnect: advertising BLE service");
	return true;
}

void TinZrBleConnect::handle() {
	// event-driven; nothing to do for now
}

void TinZrBleConnect::sendUDP(const uint8_t* data, size_t len) {
	if (!_txChr || !_deviceConnected || !data || !len) return;

	_txChr->setValue((uint8_t*)data, len);
	_txChr->notify();
}

int TinZrBleConnect::sendTCP(const uint8_t* data, size_t len, uint32_t /*timeoutMs*/) {
	sendUDP(data, len);
	return _deviceConnected ? 1 : 0;
}

void TinZrBleConnect::_handleConnect(BLEServer* /*s*/) {
	_deviceConnected = true;
	Serial.println("🔵 TinZrBleConnect: central connected");
}

void TinZrBleConnect::_handleDisconnect(BLEServer* s) {
	_deviceConnected = false;
	Serial.println("🔵 TinZrBleConnect: central disconnected, restarting advertising");
	if (s) {
		s->getAdvertising()->start();
	}
}

void TinZrBleConnect::_onRxWriteStatic(BLECharacteristic* chr) {
	if (!chr || !_self) return;

	String val = chr->getValue();
	if (!val.length()) return;

	_self->_handleRx((const uint8_t*)val.c_str(), (size_t)val.length());
}

void TinZrBleConnect::_handleRx(const uint8_t* data, size_t len) {
	if (!_onMsg || !data || !len) return;

	IPAddress dummy(0, 0, 0, 0);
	_onMsg(dummy, data, len);
}

#endif // TINZR_ENABLE_BLE
