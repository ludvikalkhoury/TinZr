#include <Arduino.h>
#include "TinZrWearableSD.h"

TinZrWearableSDConfig cfg = {
	.hostname           = "TinZrBlue",
	.sample_interval_ms = 4,      // ≈ 250 Hz
	.sd_log_dir         = "/TinZrLogs"
};

void setup() {
	TinZrWearableSD.begin(cfg);
	delay(10);
}

void loop() {
	TinZrWearableSD.handle();
}
