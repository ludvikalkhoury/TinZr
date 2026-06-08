#include "TinZrBLE.h"
#include "TinZrLED.h"   // uses TinZrLED.setMode(BLE_ADVERTISING / BLE_CONNECTED)

TinZrBLEConnect TinZrBLE;

// ============================================================
// TinZrBLEGatt
// ============================================================
TinZrBLEGatt::TinZrBLEGatt() {}

bool TinZrBLEGatt::begin(const TinZrBLEConfig& cfg) {
	_cfg = cfg;
	_state = TinZrBLEState::STARTING;

#if !TINZR_ENABLE_BLE
	_state = TinZrBLEState::FAILED;
	TinZrLED.setMode(TinZrStatusLED::Mode::FAIL_BLINK);
	return false;
#else
	BLEDevice::init(_cfg.device_name ? _cfg.device_name : "TinZr");
	BLEDevice::setPower(ESP_PWR_LVL_P9);
		
	
	if (_cfg.preferred_mtu > 0) {
		BLEDevice::setMTU(_cfg.preferred_mtu);
	}

	_server = BLEDevice::createServer();
	_server->setCallbacks(this);

	_service = _server->createService(_cfg.service_uuid);

	_rx = _service->createCharacteristic(
		_cfg.rx_uuid,
		BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
	);
	_rx->setCallbacks(this);

	_tx = _service->createCharacteristic(
		_cfg.tx_uuid,
		BLECharacteristic::PROPERTY_NOTIFY
	);
	_tx->addDescriptor(new BLE2902());

	_service->start();

	_adv = BLEDevice::getAdvertising();
	_adv->addServiceUUID(_cfg.service_uuid);
	_adv->setScanResponse(_cfg.scan_response);

	// Common iOS trick
	_adv->setMinPreferred(0x06);
	_adv->setMinPreferred(0x12);

	return _startAdvertising();
#endif
}

void TinZrBLEGatt::end() {
#if TINZR_ENABLE_BLE
	if (_adv) _adv->stop();
#endif

	_server  = nullptr;
	_service = nullptr;
	_rx      = nullptr;
	_tx      = nullptr;
	_adv     = nullptr;

	_state = TinZrBLEState::OFF;
	TinZrLED.setMode(TinZrStatusLED::Mode::OFF);
}

void TinZrBLEGatt::handle() {
	// Nothing required; kept for symmetry with TinZrWiFi
}

bool TinZrBLEGatt::_startAdvertising() {
#if !TINZR_ENABLE_BLE
	_state = TinZrBLEState::FAILED;
	TinZrLED.setMode(TinZrStatusLED::Mode::FAIL_BLINK);
	return false;
#else
	if (!_adv) {
		_state = TinZrBLEState::FAILED;
		TinZrLED.setMode(TinZrStatusLED::Mode::FAIL_BLINK);
		return false;
	}

	_adv->start();
	_state = TinZrBLEState::ADVERTISING;

	// LED: flashing while advertising
	TinZrLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);

	return true;
#endif
}

bool TinZrBLEGatt::notify(const uint8_t* data, size_t len) {
#if !TINZR_ENABLE_BLE
	(void)data;
	(void)len;
	return false;
#else
	if (!_tx || _state != TinZrBLEState::CONNECTED) return false;
	if (!data || len == 0) return true;

	size_t maxChunk = _cfg.max_notify_chunk;
	if (maxChunk == 0) maxChunk = len;

	size_t off = 0;
	while (off < len) {
		size_t n = len - off;
		if (n > maxChunk) n = maxChunk;

		_tx->setValue((uint8_t*)(data + off), n);
		_tx->notify();

		off += n;
		delay(0);
	}
	return true;
#endif
}

#if TINZR_ENABLE_BLE
void TinZrBLEGatt::onConnect(BLEServer* server) {
	if (server != nullptr) {
		server->updateConnParams(server->getConnId(), 6, 12, 0, 400);
	}
	_state = TinZrBLEState::CONNECTED;

	// LED: solid when connected
	TinZrLED.setMode(TinZrStatusLED::Mode::BLE_CONNECTED);
}

void TinZrBLEGatt::onDisconnect(BLEServer* server) {
	(void)server;

	// We’re no longer connected
	_state = TinZrBLEState::ADVERTISING;

	if (_cfg.auto_restart_advertising) {
		_startAdvertising(); // also sets LED to BLE_ADVERTISING
	} else {
		TinZrLED.setMode(TinZrStatusLED::Mode::OFF);
	}
}

void TinZrBLEGatt::onWrite(BLECharacteristic* ch) {
	if (!ch) return;

	// IMPORTANT:
	// Some ESP32 BLE builds return Arduino String here (not std::string).
	String s = ch->getValue();
	if (s.length() == 0) return;

	if (_onWrite) {
		_onWrite(
			reinterpret_cast<const uint8_t*>(s.c_str()),
			(size_t)s.length()
		);
	}
}
#endif

// ============================================================
// TinZrBLEConnect
// ============================================================
bool TinZrBLEConnect::begin(const TinZrBLEConfig& cfg) {
	return _gatt.begin(cfg);
}

void TinZrBLEConnect::end() {
	_gatt.end();
}

void TinZrBLEConnect::handle() {
	_gatt.handle();
}
