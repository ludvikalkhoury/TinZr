#include <Arduino.h>
#include <WiFi.h>

#include "TinZrCore.h"
#include "TinZrConsole.h"
#include "TinZrConnect.h"
#include "TinZrHubCommands.h"

// -------------- PC Hub settings (must match tab_hub.py) --------------
static const uint16_t HUB_TCP_PORT   = 4211;
static const uint16_t HUB_UDP_PORT   = 4210;
static const IPAddress HUB_MCAST_GRP = IPAddress(239, 1, 1, 1);

// -------------- Globals from TinZrNet library --------------
// TinZrCore TinZr;   // <-- already defined in TinZrCore.cpp (DO NOT re-declare)

TinZrConsole Console;
TinZrConnect Net;
TinZrHubCommands HubCmd(&TinZr, &Net);

// Default Wi-Fi / hostname used by Console on first boot
TinZrConsoleDefaults DEF = {
	.ssid       = "Ludvik",
	.pass       = "Lud12345",
	.hostname   = "TinZrNode1",
	.use_static = false
};


// -------------- Arduino setup / loop --------------
void setup()
{
	Serial.begin(115200);
	delay(200);

	Serial.println();
	Serial.println("===== TinZr Com Center Node =====");

	// Core hardware services: button, battery, onboard NeoPixel
	TinZr.begin();

	// Link console to core AND net so it can control LED, battery, soft power, and TCP/UDP
	Console.attachCore(&TinZr);
	Console.attachNet(&Net);     // 👈👈 THIS is the missing piece
	Console.begin(DEF);          // brings up Wi-Fi + OTA using defaults or NVS


	// Wait until Wi-Fi is connected AND LED is in ready state
	while (!Console.ready()) {
		Console.handle();
		TinZr.handle();
		delay(20);
	}

	Serial.println("🌐 Wi-Fi connected.");
	Serial.print("IP: "); Serial.println(WiFi.localIP());

	// Start TinZrConnect with hub-compatible ports / multicast
	if (!Net.start(HUB_TCP_PORT, HUB_UDP_PORT, HUB_MCAST_GRP)) {
   		Serial.println("❌ TinZrConnect.start() failed (Wi-Fi not connected?)");
	} else {
			Serial.println("🚀 TinZrConnect started");
			Net.sendDiscovery();
			// HubCmd already registered its callback in its constructor
	}


}

void loop()
{
	// Core: handle button + soft power (long-press, soft-off, etc.)
	TinZr.handle();

	// Console: OTA + Wi-Fi state machine
	Console.handle();

	// Only when Wi-Fi + LED are “ready”
	if (Console.ready()) {
		// Pump networking (RX only; no periodic HELLO, no STATUS)
		Net.handle();

		// --- BUTTON → send LED state to hub on press edge ---
		static bool lastPressed = false;
		bool pressed = (digitalRead(PB_PIN) == LOW);   // active-low button

		if (pressed && !lastPressed) {
			// Falling edge: button just pressed
			char buf[64];
			snprintf(buf, sizeof(buf),
			        "BTN LED %u %u %u %u",
							(unsigned)HubCmd.ledR(),
							(unsigned)HubCmd.ledG(),
							(unsigned)HubCmd.ledB(),
							(unsigned)HubCmd.ledBr());

			Serial.print("📤 Button press → sending: ");
			Serial.println(buf);
			Net.sendTCP((const uint8_t*)buf, strlen(buf));
		}
		lastPressed = pressed;
	}
}
