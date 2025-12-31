/*
 * ================================================================
 *  TinZr Button → Wi-Fi Web Counter
 * ================================================================
 *
 * This Arduino sketch demonstrates using the TinZr platform’s
 * onboard push button to drive a live counter that is exposed
 * over Wi-Fi through a lightweight HTTP web server.
 *
 * The counter increments on each button press and can be viewed
 * in real time from any device connected to the same network.
 *
 * ---------------------------------------------------------------
 * Features
 * ---------------------------------------------------------------
 *  - Initializes the TinZr core system (LED, button, power logic)
 *  - Connects the device to a Wi-Fi network
 *  - Starts an embedded HTTP web server on port 80
 *  - Uses the onboard push button to increment a counter
 *  - Displays the counter value via a web browser
 *  - Auto-refreshing web UI for near real-time updates
 *
 * ---------------------------------------------------------------
 * Behavior
 * ---------------------------------------------------------------
 * 1. On startup:
 *    - TinZr core system is initialized
 *    - Wi-Fi credentials are configured and connection is attempted
 *    - The assigned local IP address is printed to the Serial Monitor
 *    - An HTTP server is started on port 80
 *
 * 2. Button interaction:
 *    - Each rising-edge button press:
 *        • Increments a global counter variable
 *        • Prints the updated counter value to the Serial Monitor
 *
 *    - Button state is edge-detected to prevent repeat counts
 *      while the button is held down
 *
 * 3. Web interface:
 *    - Navigating to:
 *
 *        http://<device-ip>/
 *
 *      displays a simple HTML page showing the current counter
 *
 *    - The page auto-refreshes once per second to reflect updates
 *
 * ---------------------------------------------------------------
 * Web Page Contents
 * ---------------------------------------------------------------
 *  - Title: "TinZr Counter"
 *  - Header: "TinZr Button Counter"
 *  - Large numeric display of the current counter value
 *
 * ---------------------------------------------------------------
 * System Timing
 * ---------------------------------------------------------------
 *  - Button state is polled in the main loop
 *  - A short delay (~5 ms) limits polling rate and CPU usage
 *  - Wi-Fi and HTTP servicing is handled continuously via:
 *
 *        TinZrWiFi.handle()
 *        server.handleClient()
 *
 * ---------------------------------------------------------------
 * Dependencies
 * ---------------------------------------------------------------
 * - TinZrCore
 *     Provides:
 *       - Global TinZrCore object: `TinZr`
 *       - Button state reading
 *       - Core system initialization
 *
 * - TinZrWiFi
 *     Provides:
 *       - Wi-Fi configuration and connection handling
 *       - Non-blocking background Wi-Fi servicing
 *
 * - WebServer (ESP32 Arduino Core)
 *     Provides:
 *       - Lightweight HTTP server implementation
 *       - URL routing and HTML response handling
 *
 * ---------------------------------------------------------------
 * Notes
 * ---------------------------------------------------------------
 * - The counter variable is stored in RAM and resets on reboot
 * - No authentication is used (local network use only)
 * - Designed for simplicity and clarity over performance
 * - HTML is generated dynamically using Arduino String objects
 * - Intended for demos, testing, and Wi-Fi bring-up validation
 *
 * This sketch is intended for:
 *   - Button input validation
 *   - Wi-Fi connectivity testing
 *   - Embedded web server demonstrations
 *
 * TinZr Platform — Button + Wi-Fi Web Counter Example
 * ================================================================
 */

#include <Arduino.h>
#include <WebServer.h>
#include "TinZrCore.h"
#include "TinZrLED.h"
#include "TinZrWiFi.h"

WebServer server(80);
volatile uint32_t g_counter = 0;


void handleRoot() {
	String html;
	html.reserve(256);

	html += "<!DOCTYPE html><html><head>";
	html += "<meta http-equiv='refresh' content='1'>"; // auto-refresh every 1s
	html += "<title>TinZr Counter</title>";
	html += "</head><body>";
	html += "<h1>TinZr Button Counter</h1>";
	html += "<p>Counter value:</p>";
	html += "<h2>";
	html += g_counter;
	html += "</h2>";
	html += "</body></html>";

	server.send(200, "text/html", html);
}



void setup() {
	Serial.begin(115200);
	delay(200);

	TinZr.begin(50);

	TinZrWiFiConfig wifi_cfg;
	wifi_cfg.ssid = "Ludvik";
	wifi_cfg.pass = "Lud12345";
	wifi_cfg.hostname = "tinzr-wifi";

	TinZrWiFi.begin(wifi_cfg);
	TinZrWiFi.connect(15000);     // time-out in 15 seconds

	Serial.println("Connect your PC to the same WiFi.");
	Serial.print("Open browser at: http://");
	Serial.println(WiFi.localIP());

	// Web server routes
	server.on("/", handleRoot);
	server.begin();

	Serial.println("HTTP server started");
	

}

void loop() {
	
	TinZr.handle();
	TinZrWiFi.handle();
	server.handleClient();// HTTP requests

	static bool lastPressed = false;

	bool pressed = TinZr.readButtonState(); // true = pressed

	if (!lastPressed && pressed) {
		g_counter++;
		Serial.printf("Button pressed → counter = %lu\n",
			(unsigned long)g_counter);
	}

	lastPressed = pressed;

	delay(5);
}
