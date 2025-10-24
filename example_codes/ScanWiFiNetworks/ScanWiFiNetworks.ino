#include <WiFi.h>

unsigned long lastScanTime = 0;
const unsigned long scanInterval = 1000; // 1 second

void setup() {
	Serial.begin(115200);
	delay(500);

	Serial.println("🔁 Repeated WiFi Scanner");
	WiFi.mode(WIFI_STA);  // Ensure WiFi in station mode
	WiFi.disconnect(true); // Disconnect from any AP
	delay(1000);
}

void loop() {
	if (millis() - lastScanTime >= scanInterval) {
		lastScanTime = millis();

		// Clear screen
		//Serial.print("\033[2J\033[H"); // ANSI escape codes to clear screen and reset cursor

		int n = WiFi.scanNetworks();
		Serial.println("\r🔍 Scanning for WiFi networks...");
		Serial.println("✅ Scan complete.");

		if (n == 0) {
			Serial.println("❌ No networks found.");
		} else {
			Serial.printf("📶 %d network(s) found:\n\n", n);
			for (int i = 0; i < n; ++i) {
				String ssid = WiFi.SSID(i);
				int rssi = WiFi.RSSI(i);
				int channel = WiFi.channel(i);
				wifi_auth_mode_t encryption = WiFi.encryptionType(i);
				const char* band = (channel <= 14) ? "2.4 GHz" : "5 GHz";

				Serial.printf("%2d: %-25s RSSI: %4d dBm  Encryption: %-10s Channel: %-2d Band: %s\n",
					i + 1,
					ssid.c_str(),
					rssi,
					getEncryptionType(encryption),
					channel,
					band
				);
				delay(5);
			}
		}
	}
}

const char* getEncryptionType(wifi_auth_mode_t type) {
	switch (type) {
		case WIFI_AUTH_OPEN: return "Open";
		case WIFI_AUTH_WEP: return "WEP";
		case WIFI_AUTH_WPA_PSK: return "WPA";
		case WIFI_AUTH_WPA2_PSK: return "WPA2";
		case WIFI_AUTH_WPA_WPA2_PSK: return "WPA/WPA2";
		case WIFI_AUTH_WPA2_ENTERPRISE: return "WPA2-Ent";
		case WIFI_AUTH_WPA3_PSK: return "WPA3";
		case WIFI_AUTH_WPA2_WPA3_PSK: return "WPA2/WPA3";
		default: return "Unknown";
	}
}
