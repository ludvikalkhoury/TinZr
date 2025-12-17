#include "TinZrNode.h"
#include "TinZrLED.h"
#include <string.h>

TinZrStatusLED* gStatusLED = nullptr;

TinZrNode::TinZrNode()
	: _hubCmd(&TinZr, nullptr)
{
}

void TinZrNode::begin(const TinZrNodeConfig& cfg) {
	_cfg = cfg;

	Serial.begin(115200);
	delay(200);

	Serial.println();
	Serial.println("===== TinZr Com Center Node (TinZrNode) =====");

	TinZr.begin();
	_statusLED.begin(25);
	gStatusLED = &_statusLED;

	_console.attachCore(&TinZr);

	_link         = nullptr;
	_netStarted   = false;
	_wifiWasUp    = false;
#if TINZR_ENABLE_BLE
	_bleWasConnected = false;
#endif

#if TINZR_ENABLE_WIFI
	Serial.println("🌐 TinZrNode: Wi-Fi mode");
	TinZrConsoleDefaults def;
	def.ssid       = _cfg.ssid;
	def.pass       = _cfg.pass;
	def.hostname   = _cfg.hostname;
	def.use_static = _cfg.use_static;

	_console.attachNet(&_net);

	// Blink green while trying to connect
	_statusLED.setMode(TinZrStatusLED::Mode::WIFI_SEARCH);
	_console.begin(def);

	// Block here until the console says Wi-Fi is ready
	while (!_console.ready()) {
		_console.handle();
		TinZr.handle();
		_statusLED.handle();
		delay(20);
	}

	// ---- success: we only reach here when connected ----
	Serial.println("🌐 Wi-Fi connected.");
	Serial.print("IP: ");
	Serial.println(_console.ip());

	_statusLED.setMode(TinZrStatusLED::Mode::WIFI_OK);
	_wifiWasUp = true;

	_net.setName(_cfg.hostname);

	if (!_net.start(_cfg.hubTcpPort, _cfg.hubUdpPort, _cfg.hubMcastGrp)) {
		Serial.println("❌ TinZrConnect.start() failed");
		_netStarted = false;
		_link       = nullptr;
		_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
	} else {
		Serial.println("🚀 TinZrConnect started");
		_net.attachConsole(&_console);
		_net.setHubIP(_cfg.hubIP);
		_net.sendDiscovery();
		_netStarted = true;
		_link       = &_net;

		// Wi-Fi up initially → solid green
		_statusLED.setMode(TinZrStatusLED::Mode::WIFI_OK);
		_wifiWasUp = (WiFi.status() == WL_CONNECTED);
	}



#elif TINZR_ENABLE_BLE
	Serial.println("🔵 TinZrNode: BLE mode");
	const char* name =
		(_cfg.hostname && _cfg.hostname[0] != '\0')
			? _cfg.hostname
			: "TinZrBLE";

	_ble.setName(name);

	// Start in "advertising" = flashing green (we’ll define this below)
	_statusLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);

	_netStarted = _ble.start();
	if (!_netStarted) {
		Serial.println("❌ TinZrBleConnect.start() failed");
		_link = nullptr;
		_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
	} else {
		Serial.print("🔵 TinZrBleConnect started, name = ");
		Serial.println(name);
		_link = &_ble;

		// At startup, not connected yet
		_bleWasConnected = _ble.isConnected();
	}
#else
	Serial.println("🚫 TinZrNode: no networking");
	_netStarted = false;
	_link       = nullptr;
	_statusLED.setMode(TinZrStatusLED::Mode::OFF);
#endif

	if (_link) {
		new (&_hubCmd) TinZrHubCommands(&TinZr, _link);
	}

	_lastButtonPressed = false;
}





void TinZrNode::handle() {
	TinZr.handle();
	_statusLED.handle();

	if (!TinZr.isSoftOn()) {
		return;
	}

	_console.handle();

	// -------- Wi-Fi connection monitoring (works with or without OTA) --------
#if TINZR_ENABLE_WIFI
	if (_link == &_net) {
		bool nowUp = (WiFi.status() == WL_CONNECTED);

		if (_wifiWasUp && !nowUp) {
			// just dropped
			Serial.println("📴 Wi-Fi dropped → red blink");
			_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
			_wifiWasUp = false;
		} else if (!_wifiWasUp && nowUp) {
			// just came back
			Serial.println("✅ Wi-Fi reconnected → solid green");
			_statusLED.setMode(TinZrStatusLED::Mode::WIFI_OK);
			_wifiWasUp = true;
		}
	}
#endif

	// -------- BLE connection monitoring --------
#if TINZR_ENABLE_BLE
	if (_link == &_ble) {
		bool nowConn = _ble.isConnected();

		if (_bleWasConnected && !nowConn) {
			// was connected, now disconnected → go back to flashing
			Serial.println("📡 BLE disconnected → flashing green");
			_statusLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
			_bleWasConnected = false;
		} else if (!_bleWasConnected && nowConn) {
			// was not connected, now connected → solid green
			Serial.println("🔵 BLE connected → solid green");
			_statusLED.setMode(TinZrStatusLED::Mode::BLE_CONNECTED);
			_bleWasConnected = true;
		}
	}
#endif

	bool linkReady = _netStarted && (_link != nullptr);
	if (linkReady) {
		_link->handle();
		_handleButtonToHub();
	}
}

void TinZrNode::_handleButtonToHub() {
	if (!_link) return;

	bool pressed = (digitalRead(PB_PIN) == LOW);

	if (pressed && !_lastButtonPressed) {
		char buf[64];
		snprintf(buf, sizeof(buf),
		         "BTN LED %u %u %u %u",
		         (unsigned)_hubCmd.ledR(),
		         (unsigned)_hubCmd.ledG(),
		         (unsigned)_hubCmd.ledB(),
		         (unsigned)_hubCmd.ledBr());
		Serial.print("📤 Button press → sending: ");
		Serial.println(buf);
		_link->sendTCP((const uint8_t*)buf, strlen(buf));
	}

	_lastButtonPressed = pressed;
}
