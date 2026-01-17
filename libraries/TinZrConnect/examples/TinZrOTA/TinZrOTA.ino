/*
 * ================================================================
 *  TinZr → Wi-Fi + OTA Bring-Up with Non-Blocking LED Blink
 * ================================================================
 *
 * This Arduino sketch demonstrates basic TinZr system bring-up
 * with Wi-Fi connectivity and Over-The-Air (OTA) firmware update
 * support, while running a simple non-blocking application task.
 *
 * The example shows the minimal structure required to keep OTA
 * servicing alive in the main loop while executing user logic.
 *
 * ---------------------------------------------------------------
 * Features
 * ---------------------------------------------------------------
 *  - Initializes the TinZr core system
 *  - Connects to a Wi-Fi network using TinZrWiFi
 *  - Enables OTA firmware updates via TinZrOTA
 *  - Demonstrates a cooperative, non-blocking main loop
 *  - Uses the onboard RGB LED as a visual indicator
 *
 * ---------------------------------------------------------------
 * Behavior
 * ---------------------------------------------------------------
 * 1. On startup:
 *    - Serial output is initialized for debugging
 *    - TinZr core system is initialized
 *    - Wi-Fi credentials are configured and a connection is attempted
 *    - OTA service is initialized with a device identifier
 *
 * 2. Runtime operation:
 *    - The main loop continuously services:
 *        • TinZrWiFi.handle()  → maintains network connectivity
 *        • TinZrOTA.handle()   → processes OTA update requests
 *
 *    - A non-blocking LED blink task runs in parallel:
 *        • This piece represents the main program
 *	      • LED toggles every 250 ms
 *        • Color alternates between magenta and off
 *
 * ---------------------------------------------------------------
 * OTA Servicing Requirement
 * ---------------------------------------------------------------
 *  - TinZrWiFi.handle() and TinZrOTA.handle() MUST be called
 *    repeatedly inside loop()
 *  - Removing or blocking these calls will prevent OTA updates
 *  - User application code must remain cooperative and non-blocking
 *
 * ---------------------------------------------------------------
 * System Timing
 * ---------------------------------------------------------------
 *  - No delay() is used inside loop()
 *  - Timing is managed using millis()-based scheduling
 *  - OTA and Wi-Fi servicing occurs continuously in the background
 *
 * ---------------------------------------------------------------
 * Dependencies
 * ---------------------------------------------------------------
 * - TinZrCore
 *     Provides:
 *       - Core system initialization
 *
 * - TinZrWiFi
 *     Provides:
 *       - Wi-Fi configuration and connection management
 *       - Background network servicing
 *
 * - TinZrOTA
 *     Provides:
 *       - Over-The-Air firmware update capability
 *       - OTA state handling and update processing
 *
 * - TinZrLED
 *     Provides:
 *       - RGB LED control for visual feedback
 *
 * ---------------------------------------------------------------
 * Notes
 * ---------------------------------------------------------------
 * - OTA is intended for development and controlled environments
 * - No authentication is enabled in this example
 * - LED blink serves as a heartbeat to indicate the main loop
 *   remains responsive during OTA servicing
 * - Ideal for:
 *     • Wi-Fi bring-up validation
 *     • OTA workflow testing
 *     • Reference structure for OTA-enabled applications
 *
 * TinZr Platform — Wi-Fi + OTA Bring-Up Example
 * ================================================================
 */
 

#include <Arduino.h>
#include "TinZrCore.h"
#include "TinZrWiFi.h"
#include "TinZrOTA.h"
#include "TinZrLED.h"


void setup() {
	Serial.begin(115200);
	delay(200);

	TinZr.begin();

	TinZrWiFiConfig wifi_cfg;
	wifi_cfg.ssid = "Ludvik";
	wifi_cfg.pass = "Lud12345";
	wifi_cfg.hostname = "tinzr-wifi";

	TinZrWiFi.begin(wifi_cfg);
	TinZrWiFi.connect(15000);     // time-out in 15 seconds

	TinZrOTA.begin("tinzr-ota");
	
}

void loop() {

	// *******************************************************************
	// ****** This piece should stay in the code for continuing OTA ******
	// *******************************************************************
	TinZrWiFi.handle();
	TinZrOTA.handle();
	// *******************************************************************
	// ******** Ends here ************************************************
	// *******************************************************************


	// This is the main code; non-blocking blink
	static uint32_t last = 0;
	static bool on = false;

	if (millis() - last >= 250) {
		last = millis();
		on = !on;
		if (on) TinZrLED.setColor(255, 0, 200); 
		else    TinZrLED.setColor(0, 0, 0);
	}



}
