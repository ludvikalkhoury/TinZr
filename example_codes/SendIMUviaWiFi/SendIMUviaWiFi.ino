#include <WiFi.h>
#include <WebServer.h>
#include <Adafruit_NeoPixel.h>
#include <Wire.h>
#include <Adafruit_LSM6DS3TRC.h>
#include <Adafruit_Sensor.h>

// WiFi credentials
const char* ssid     = "Ludvik";
const char* password = "Lud12345";

// Static IP configuration
IPAddress local_IP(192, 168, 1, 40);
IPAddress gateway(192, 168, 1, 1);
IPAddress subnet(255, 255, 255, 0);
IPAddress primaryDNS(8, 8, 8, 8);   // optional
IPAddress secondaryDNS(8, 8, 4, 4); // optional

// LED setup
#define NUM_PIXELS 1
Adafruit_NeoPixel pixels(NUM_PIXELS, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

// IMU setup
#define LSM_ADDR 0x6A
Adafruit_LSM6DS3TRC imu;

// Web server
WebServer server(80);

// Variables for sensor data
sensors_event_t accel, gyro, temp;

void setColor(uint8_t r, uint8_t g, uint8_t b) {
	pixels.setPixelColor(0, pixels.Color(r, g, b));
	pixels.show();
}

void handleRoot() {
	String html = "<html><head><meta http-equiv='refresh' content='1'/><style>body{font-family:monospace;}</style></head><body>";
	html += "<h2>📊 IMU Data</h2>";
	html += "<b>Accelerometer (m/s²):</b><br>";
	html += "X: " + String(accel.acceleration.x, 3) + "<br>";
	html += "Y: " + String(accel.acceleration.y, 3) + "<br>";
	html += "Z: " + String(accel.acceleration.z, 3) + "<br><br>";

	html += "<b>Gyroscope (°/s):</b><br>";
	html += "X: " + String(gyro.gyro.x, 2) + "<br>";
	html += "Y: " + String(gyro.gyro.y, 2) + "<br>";
	html += "Z: " + String(gyro.gyro.z, 2) + "<br><br>";

	html += "<b>Temperature (°C):</b><br>";
	html += String(temp.temperature, 1) + "<br>";
	html += "</body></html>";

	server.sendHeader("Content-Type", "text/html; charset=utf-8");
server.send(200, "text/html", html);
}

void setup() {
	Serial.begin(115200);
	delay(300);
	Serial.println("🚀 Booting...");

	pixels.begin();

	// Init WiFi
	WiFi.mode(WIFI_STA);

	// Set static IP: NOT WITH PHONE WIFI
	//if (!WiFi.config(local_IP, gateway, subnet, primaryDNS, secondaryDNS)) {
	//	Serial.println("⚠ Failed to configure static IP");
	//}

	WiFi.begin(ssid, password);
	WiFi.setTxPower(WIFI_POWER_8_5dBm);

	while (WiFi.status() != WL_CONNECTED) {
		setColor(64, 0, 0);  // Red
		Serial.print("⏳ Connecting... Status: ");
		Serial.println(WiFi.status());
		delay(300);
		setColor(0, 0, 0);
		delay(500);
	}

	Serial.println("✅ WiFi connected!");
	Serial.print("📡 IP address: ");
	Serial.println(WiFi.localIP());
	setColor(0, 0, 255);  // Blue

	// Setup I2C and IMU
	Wire.begin();
	Wire.setClock(400000);
	delay(50);
	if (!imu.begin_I2C(LSM_ADDR)) {
		Serial.println("✖ IMU not found!");
		setColor(255, 0, 0);  // Red solid
		while (1) delay(10);
	}
	Serial.println("✔ IMU initialized.");

	// Setup web server
	server.on("/", handleRoot);
	server.onNotFound([]() {
					server.send(404, "text/plain", "Not found");
					});
	server.begin();
	Serial.println("🌐 Web server started");
}

void loop() {
	// Read IMU data
	if (imu.getEvent(&accel, &gyro, &temp)) {
		// success
	} else {
		Serial.println("⚠ IMU read failed!");
	}

	// Flash LED: white ↔ blue
	static bool flash = false;
	static unsigned long lastToggle = 0;
	if (millis() - lastToggle > 500) {
		lastToggle = millis();
		flash = !flash;
		setColor(flash ? 255 : 0, flash ? 255 : 255, flash ? 255 : 0); // white ↔ green

	}

	server.handleClient();  // Serve web page
}
