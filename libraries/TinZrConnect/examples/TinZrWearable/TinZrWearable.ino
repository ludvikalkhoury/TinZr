#include <Arduino.h>
#include "TinZrWearable.h"

TinZrWearableConfig cfg = {
	.hostname           = "TinZrBlue",
	.sample_interval_ms = 4    // ≈ 250 Hz target
};

void setup() {
	TinZrWearable.begin(cfg);
	delay(10);
}

void loop() {
	TinZrWearable.handle();
}
