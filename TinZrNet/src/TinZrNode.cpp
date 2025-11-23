#include "TinZrNode.h"

// TinZrCore singleton is defined in TinZrCore.cpp
// extern TinZrCore TinZr;   // already provided by TinZrCore.h

TinZrNode::TinZrNode()
  : _hubCmd(&TinZr, &_net)   // wire HubCommands to Core + Net
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

  // --- Wire Console to Core + Net ---
  _console.attachCore(&TinZr);
  _console.attachNet(&_net);

  // Bring up WiFi + OTA using defaults/NVS logic in TinZrConsole::begin
  _console.begin(_cfg.console);  // :contentReference[oaicite:0]{index=0}

  // Wait until Wi-Fi is connected AND LED is in ready state
  while (!_console.ready()) {
    _console.handle();
    TinZr.handle();
    delay(20);
  }

  Serial.println("🌐 Wi-Fi connected.");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());

  // --- Start TinZrConnect with hub-compatible ports / multicast ---
  if (!_net.start(_cfg.hubTcpPort, _cfg.hubUdpPort, _cfg.hubMcastGrp)) {
    Serial.println("❌ TinZrConnect.start() failed (Wi-Fi not connected?)");
    _netStarted = false;
  } else {
    Serial.println("🚀 TinZrConnect started");
    _net.attachConsole(&_console);   // so HELLO uses hostname
    _net.setHubIP(_cfg.hubIP);
    _net.sendDiscovery();           // send HELLO <hostname>
    _netStarted = true;
    // HubCmd is already registered as message callback in its ctor :contentReference[oaicite:1]{index=1}
  }

  _lastButtonPressed = false;
}

void TinZrNode::handle() {
  // Always let Core handle the power button / long-press soft power
  TinZr.handle();  // :contentReference[oaicite:2]{index=2}

  // If we're in soft-off state, do nothing else
  if (!TinZr.isSoftOn()) {
    return;
  }

  // Console: OTA + Wi-Fi state machine
  _console.handle();  // :contentReference[oaicite:3]{index=3}

  // Only when Wi-Fi + LED are “ready”
  if (_console.ready() && _netStarted) {
    // Pump networking (RX only; HELLO / control-plane handled inside)
    _net.handle();

    // Your application-level behavior: button → BTN LED ... to hub
    _handleButtonToHub();
  }
}

void TinZrNode::_handleButtonToHub() {
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
    _net.sendTCP((const uint8_t*)buf, strlen(buf));
  }

  _lastButtonPressed = pressed;
}
