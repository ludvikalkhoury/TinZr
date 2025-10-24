#include <Arduino.h>
#include <Wire.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include "esp_private/periph_ctrl.h"
#include <Adafruit_LSM6DS3TRC.h>
#include "MAX30105.h"

// UUIDs
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"

// BLE Globals
BLECharacteristic *pCharacteristic;
BLEServer* pServer;
bool deviceConnected = false;

// IMU + PPG Sensor Objects
Adafruit_LSM6DS3TRC imu;
MAX30105 particleSensor;
const unsigned long sampleInterval = 100; // 10 Hz
unsigned long lastSampleTime = 0;
const long NO_FINGER_THRESHOLD = 30000;

// BLE Callbacks
class MyServerCallbacks : public BLEServerCallbacks {
	void onConnect(BLEServer* server) override {
		deviceConnected = true;
	}
	void onDisconnect(BLEServer* server) override {
		deviceConnected = false;
		server->getAdvertising()->start();
	}
};

void setup() {
	periph_module_disable(PERIPH_USB_DEVICE_MODULE);

	Wire.begin();

	// Init LSM6DS3TR-C
	if (!imu.begin_I2C()) {
		while (1);  // Hang if IMU not found
	}

	// Init MAX30105
	if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
		while (1);  // Hang if PPG not found
	}
	particleSensor.setup();                      // Default settings
	particleSensor.setPulseAmplitudeRed(0x0A);   // Low RED
	particleSensor.setPulseAmplitudeGreen(0);    // Turn off green

	// Init BLE
	BLEDevice::init("ESP32C3_ACC_PPG");
	pServer = BLEDevice::createServer();
	pServer->setCallbacks(new MyServerCallbacks());

	BLEService *pService = pServer->createService(SERVICE_UUID);
	pCharacteristic = pService->createCharacteristic(
		CHARACTERISTIC_UUID,
		BLECharacteristic::PROPERTY_NOTIFY
	);
	pCharacteristic->addDescriptor(new BLE2902());
	pService->start();
	pServer->getAdvertising()->start();
	delay(100);
}

void loop() {
	unsigned long now = millis();
	if (!deviceConnected || (now - lastSampleTime < sampleInterval)) return;
	lastSampleTime = now;

	// Read accelerometer
	sensors_event_t a, g, temp;
	imu.getEvent(&a, &g, &temp);

	// Read PPG
	uint32_t ir = particleSensor.getIR();
	uint32_t red = particleSensor.getRed();

	// Optionally skip if no finger
	//if (ir < NO_FINGER_THRESHOLD) return;

	// Format string: ax,ay,az,ir,red
	char buffer[128];
	snprintf(buffer, sizeof(buffer), "%.2f,%.2f,%.2f,%lu,%lu",
	         a.acceleration.x, a.acceleration.y, a.acceleration.z,
	         ir, red);

	pCharacteristic->setValue(buffer);
	pCharacteristic->notify();
}
