#include <Wire.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"

MAX30105 particleSensor;

const unsigned long sampleInterval = 25; // 25 ms = 40Hz sampling
const long NO_FINGER_THRESHOLD = 50000;

unsigned long lastSampleTime = 0;

void setup() {
	Serial.begin(115200);
	delay(500);

  Wire.begin();
	// Initialize the MAX30105 sensor
	if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
		Serial.println("MAX30105 not found. Check wiring.");
		while (1);
	}

	particleSensor.setup();
	particleSensor.setPulseAmplitudeRed(0x0A); // Low amplitude
	particleSensor.setPulseAmplitudeGreen(0);   // Turn off Green LED
}

void loop() {
	unsigned long currentTime = millis();
	if (currentTime - lastSampleTime >= sampleInterval) {
		lastSampleTime = currentTime;

		uint32_t irValue = particleSensor.getIR();
		uint32_t redValue = particleSensor.getRed();

		if (irValue < NO_FINGER_THRESHOLD) {
			Serial.println("No finger detected");
			return;
		}

		// Send IR and RED values over Serial
		Serial.printf("Data:%lu,%lu\n", irValue, redValue);
	}
}
