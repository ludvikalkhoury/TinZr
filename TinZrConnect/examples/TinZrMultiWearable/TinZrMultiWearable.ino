#include <Arduino.h>
#include "TinZrMultiWearable.h"

TinZrWearable Wearable;

TinZrWearableConfig cfg = {
	.hostname           = "TinZrBlue",
	.sample_interval_ms = 4    // ≈ 250 Hz target
};

void setup() {

	Wearable.begin(cfg);

	pinMode(20, OUTPUT);
	pinMode(21, OUTPUT);
	digitalWrite(20, LOW);
	digitalWrite(21, LOW);

	delay(10);



}

void loop() {
	Wearable.handle();
}
