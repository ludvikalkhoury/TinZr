#include <Adafruit_NeoPixel.h>

#define NUM_LEDS     1
#define BRIGHTNESS 40    // 0–255
#define FRAME_DELAY 20    // ms between color updates

Adafruit_NeoPixel strip(NUM_LEDS, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

// Produce rainbow colors across 0–255
uint32_t Wheel(uint8_t pos) {
  if (pos < 85) {
    return strip.Color(pos * 3, 255 - pos * 3, 0);
  } else if (pos < 170) {
    pos -= 85;
    return strip.Color(255 - pos * 3, 0, pos * 3);
  } else {
    pos -= 170;
    return strip.Color(0, pos * 3, 255 - pos * 3);
  }
}

void setup() {
  strip.begin();
  strip.setBrightness(BRIGHTNESS);
  strip.show();  // all off
}

void loop() {
  static uint8_t hue = 0;
  strip.setPixelColor(0, Wheel(hue));
  strip.show();
  hue++;               // move to the next color
  delay(FRAME_DELAY);  // adjust for speed/smoothness
}
