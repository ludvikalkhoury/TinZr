#include <Arduino.h>
#include "TinZrWearable.h"

TinZrWearable Wearable;

TinZrWearableConfig cfg = {
	.hostname           = "TinZrWearable",
	.sample_interval_ms = 4    // ≈ 250 Hz target
};

void setup() {
	Wearable.begin(cfg);
}

void loop() {
	Wearable.handle();
}
