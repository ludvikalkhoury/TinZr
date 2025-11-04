/*
===============================================================================
TinZr — Serial Control for SSID / PASS / Static Mode / Hostname (+ Flash Save)
===============================================================================

WHAT THIS DOES
--------------
- OTA + serial console (WIFI autosave+reboot, STATIC, HOST, SAVE/LOAD/SHOW, etc.)
- Uses a NeoPixel (Adafruit_NeoPixel) for the RGB LED
- Pushbutton cycles MAGENTA brightness: 0 → 50 → 100 → 150 → 200 → 0 ...
===============================================================================
*/

#include <TinZrOTA.h>
#include <TinZrConsole.h>
#include <Adafruit_NeoPixel.h>


// ========== OTA SETTINGS STARTS ==========
TinZrConsoleDefaults DEF = {
  .ssid       = "Ludvik",
  .pass       = "Lud12345",
  .hostname   = "esp32c3-ota",
  .use_static = false
};
TinZrConsole Console;
// ========== OTA SETTINGS ENDS ==========



// ========== MAIN CODE SETTINGS STARTS ==========
//  NeoPixel 
#define USER_NUM_LEDS 1
Adafruit_NeoPixel strip(USER_NUM_LEDS, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

// Brightness steps (0..200). We'll set NeoPixel global brightness to these.
static const uint8_t BR_STEPS[] = {0, 50, 100, 150, 200};
static const uint8_t N_STEPS    = sizeof(BR_STEPS) / sizeof(BR_STEPS[0]);
static uint8_t br_idx           = 0;

// Button debounce
static bool btn_prev = true;           // INPUT_PULLUP → idle HIGH
static unsigned long last_edge_ms = 0;

// Set LED to magenta at current global brightness
static inline void show_magenta() {
  strip.setPixelColor(0, strip.Color(255, 0, 255));  // magenta
  strip.show();
}

// Set brightness (0..200) → apply to strip and refresh magenta
static inline void set_brightness_0_200(uint8_t b) {
  // Adafruit_NeoPixel brightness is 0..255; your steps are 0..200. Map linearly.
  uint16_t neo_brightness = (uint16_t)b * 255 / 200;  // 0..255
  strip.setBrightness((uint8_t)neo_brightness);
  show_magenta();
}

// ========== MAIN CODE SETTINGS ENDS ==========




void setup() {
  Serial.begin(115200);
  delay(200);

  // Button
  pinMode(PB_PIN, INPUT_PULLUP);

  // NeoPixel init
  strip.begin();
  strip.clear();
  strip.setBrightness(0);   // start at first step (0)
  show_magenta();
  set_brightness_0_200(BR_STEPS[br_idx]);
  Serial.println("🔘 Pushbutton cycles MAGENTA brightness: 0 → 50 → 100 → 150 → 200 → 0 …");
  Serial.printf("   Current brightness: %u\n", BR_STEPS[br_idx]);

  // TinZr console + OTA
  Console.begin(DEF);

}

void loop() {
    
  // Keep OTA + console alive
  Console.handle();





  // ============== MAIN CODE STARTS ============== 
  // Button (falling-edge detect with debounce)
  bool btn = digitalRead(PB_PIN); // HIGH=idle, LOW=pressed
  unsigned long now = millis();

  if (btn_prev && !btn && (now - last_edge_ms) > 25) { // 25 ms debounce
    last_edge_ms = now;

    // Next brightness step
    br_idx = (br_idx + 1) % N_STEPS;
    set_brightness_0_200(BR_STEPS[br_idx]);

    Serial.printf("🎛️ Brightness → %u (NeoPixel brightness=%u/255)\n",
                  BR_STEPS[br_idx],
                  (unsigned)((uint16_t)BR_STEPS[br_idx] * 255 / 200));
  }
  btn_prev = btn;
  // ============== MAIN CODE ENDS ============== 


}
