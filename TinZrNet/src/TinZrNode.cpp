#include "TinZrNode.h"
#include "TinZrConfig.h"
#include <string.h>   // for strlen

// TinZrCore singleton is defined in TinZrCore.cpp
// extern TinZrCore TinZr;   // already provided by TinZrCore.h

TinZrNode::TinZrNode()
	: _hubCmd(&TinZr, nullptr)   // we'll re-init with the chosen link in begin()
{
}

void TinZrNode::begin(const TinZrNodeConfig& cfg) {
	_cfg = cfg;

	Serial.begin(115200);
	delay(200);

	Serial.println();
	Serial.println("===== TinZr Com Center Node (TinZrNode) =====");

	// --- Core hardware: button, battery, onboard NeoPixel ---
	TinZr.begin();

	// Status LED engine (works for Wi-Fi, BLE, etc.)
	_statusLED.begin(25);   // brightness 0–255

	// Always attach core to console (LED / battery commands, etc.)
	_console.attachCore(&TinZr);

	_link       = nullptr;
	_netStarted = false;

	// =========================
	// Compile-time mode select
	// =========================

#if TINZR_ENABLE_WIFI
	// -------- Wi-Fi path --------
	Serial.println("🌐 TinZrNode: Wi-Fi mode");

	// Build console defaults from cfg
	TinZrConsoleDefaults def;
	def.ssid       = _cfg.ssid;
	def.pass       = _cfg.pass;
	def.hostname   = _cfg.hostname;
	def.use_static = _cfg.use_static;

	_console.attachNet(&_net);

	// LED: searching while Wi-Fi comes up
	_statusLED.setMode(TinZrStatusLED::Mode::WIFI_SEARCH);

	_console.begin(def);   // internally handles Wi-Fi + (optional) OTA

	// Wait until console reports ready (Wi-Fi up — with or without OTA)
	while (!_console.ready()) {
		_console.handle();
		TinZr.handle();
		_statusLED.handle();   // keep animation smooth
		delay(20);
	}

	Serial.println("🌐 Wi-Fi connected.");
	Serial.print("IP: ");
	Serial.println(_console.ip());   // abstracts OTA vs no-OTA internally

	// Start TinZrConnect
	if (!_net.start(_cfg.hubTcpPort, _cfg.hubUdpPort, _cfg.hubMcastGrp)) {
		Serial.println("❌ TinZrConnect.start() failed (Wi-Fi not connected?)");
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

		// Wi-Fi + hub link OK → solid green
		_statusLED.setMode(TinZrStatusLED::Mode::WIFI_OK);
	}

#elif TINZR_ENABLE_BLE
	// -------- BLE path --------
	Serial.println("🔵 TinZrNode: BLE mode");

	// Name for advertising: prefer cfg.hostname, fallback default
	const char* name =
		(_cfg.hostname && _cfg.hostname[0] != '\0')
			? _cfg.hostname
			: "TinZrBLE";

	_ble.setName(name);

	// LED: BLE advertising = rainbow
	_statusLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);

	_netStarted = _ble.start();
	if (!_netStarted) {
		Serial.println("❌ TinZrBleConnect.start() failed");
		_link = nullptr;
		// re-use WIFI_FAIL mode for "generic failure" = red blink
		_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
	} else {
		Serial.print("🔵 TinZrBleConnect started, name = ");
		Serial.println(name);
		_link = &_ble;

		// BLE connected → solid green
		_statusLED.setMode(TinZrStatusLED::Mode::BLE_CONNECTED);
	}
#else
	// -------- No networking --------
	Serial.println("🚫 TinZrNode: no networking (TINZR_ENABLE_WIFI=0, TINZR_ENABLE_BLE=0)");
	_netStarted = false;
	_link       = nullptr;
	_statusLED.setMode(TinZrStatusLED::Mode::OFF);
#endif

	// Initialize HubCommands only if we actually have a link
	if (_link) {
		new (&_hubCmd) TinZrHubCommands(&TinZr, _link);
	}

	_lastButtonPressed = false;
}


void TinZrNode::handle() {
	// Always let Core handle the power button / long-press soft power
	TinZr.handle();

	// LED animations
	_statusLED.handle();

	// If we're in soft-off state, do nothing else
	if (!TinZr.isSoftOn()) {
		return;
	}

	// Console state machine (Wi-Fi/OTA) — harmless in BLE/none if stubs
	_console.handle();

	bool linkReady = _netStarted && (_link != nullptr);

	if (linkReady) {
		// Pump networking
		_link->handle();

		// Button → BTN LED ... to hub via selected link
		_handleButtonToHub();
	}
}

void TinZrNode::_handleButtonToHub() {
	if (!_link) {
		return;
	}

	// active-low button
	bool pressed = (digitalRead(PB_PIN) == LOW);

	if (pressed && !_lastButtonPressed) {
		// Falling edge: button just pressed
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
