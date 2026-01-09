#include <Arduino.h>
#include "TinZrWearable.h"

TinZrWearableConfig cfg = {
	.hostname           = "TinZrBlack",
	.sample_interval_ms = 4    // ≈ 250 Hz target
};

void setup() {

	TinZrWearable.begin(cfg);

	//pinMode(20, OUTPUT);
	//pinMode(21, OUTPUT);
	//digitalWrite(20, LOW);
	//digitalWrite(21, LOW);

	delay(10);

}

void loop() {
	TinZrWearable.handle();
}
