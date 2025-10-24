#include <Adafruit_NeoPixel.h>

#define NUM_LEDS        1
#define BRIGHTNESS      50        // 0–255

// Timing (ms)
const uint32_t FRAME_INTERVAL   = 20;         // LED animation step
const uint32_t BATTERY_INTERVAL = 5UL*60*1000; // 5 minutes

// ADC / divider
const float VREF = 3.3;             // ADC reference / board supply (adjust if different)
const float ADC_MAX = 4095.0;       // 12-bit on ESP32-C3; use 1023.0 for 10-bit boards

// Divider: top=220k (to VBAT), bottom=150k (to GND)
const float DIVIDER_RATIO = 150000.0 / (220000.0 + 150000.0);  // ≈ 0.405

Adafruit_NeoPixel strip(NUM_LEDS, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

// Rainbow helper
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
  Serial.begin(115200);
  Serial.println("\n→ Analog Read Test Ready");
  Serial.println("   Measuring VBAT via divider on A1 every 5 minutes...");

  strip.begin();
  strip.setBrightness(BRIGHTNESS);
  strip.show();  // all off

  // (Optional) for ESP32: analogReadResolution(12);
}

void loop() {
  static uint32_t lastFrameMs = 0;
  static uint32_t lastBatMs   = 0;
  static uint8_t  hue         = 0;

  uint32_t now = millis();

  // --- LED animation (every FRAME_INTERVAL ms) ---
  if (now - lastFrameMs >= FRAME_INTERVAL) {
    lastFrameMs = now;
    strip.setPixelColor(0, Wheel(hue++));
    strip.show();
  }

  // --- Battery read (every BATTERY_INTERVAL ms) ---
  if (now - lastBatMs >= BATTERY_INTERVAL) {
    lastBatMs = now;

    // Simple averaging to reduce noise
    const int N = 16;
    uint32_t acc = 0;
    for (int i = 0; i < N; ++i) {
      acc += analogRead(A1);
      delay(2);
    }
    float raw = acc / float(N);

    float v_div = raw * (VREF / ADC_MAX);        // voltage at divider node
    float v_bat = v_div / DIVIDER_RATIO;         // back-calculate VBAT

    Serial.print("A1 raw(avg): ");
    Serial.print(raw, 1);
    Serial.print("  | v_div: ");
    Serial.print(v_div, 3);
    Serial.print(" V  | VBAT: ");
    Serial.print(v_bat, 3);
    Serial.println(" V");
  }

  // No blocking delay here – both tasks are timed by millis()
}
