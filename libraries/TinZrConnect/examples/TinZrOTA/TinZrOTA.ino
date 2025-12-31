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
