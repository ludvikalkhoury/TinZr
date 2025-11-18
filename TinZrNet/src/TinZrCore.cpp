#include "TinZrCore.h"
#include <Adafruit_NeoPixel.h>
#include <WiFi.h>
#include <Wire.h>
#include <SPI.h>

// From Pins_Arduino_h:
//   #define PIN_RGB_LED 8
//   #define PB_PIN      9

TinZrCore TinZr;

// Internal NeoPixel object for TinZrCore
static Adafruit_NeoPixel _tinzrPixel(1, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

// ----------------- Private helpers -----------------

bool TinZrCore::readButtonRaw() const {
	int level = digitalRead(PB_PIN);
	if (BUTTON_ACTIVE_LOW) {
		return (level == LOW);   // pressed
	} else {
		return (level == HIGH);  // pressed
	}
}

// ----------------- Public API -----------------

void TinZrCore::begin() {
	// Button setup
	if (BUTTON_ACTIVE_LOW) {
		pinMode(PB_PIN, INPUT_PULLUP);
	} else {
		pinMode(PB_PIN, INPUT_PULLDOWN);
	}
	_buttonLast       = readButtonRaw();
	_pressStart       = 0;
	_longPressLatched = false;
	_softOn           = true;  // start ON after boot

	// Battery ADC
	pinMode(PIN_BAT, INPUT);
	// analogReadResolution(12); // for ESP32-C3, if needed

	// Init NeoPixel
	_tinzrPixel.begin();
	_tinzrPixel.setBrightness(25);
	_tinzrPixel.clear();
	_tinzrPixel.show();

	// Initial battery measurement
	float vbat = readBatteryVoltage();
	int   pct  = batteryPercent();

	Serial.println("==== TinZrCore boot ====");
	Serial.print("Battery: ");
	Serial.print(vbat, 3);
	Serial.print(" V (");
	Serial.print(pct);
	Serial.println(" %)");

	// Power-on indication: flash white 5×
	flashColor(255, 255, 255, 5);

	// After ON, stay solid white
	setLED(255, 255, 255);
}

// ---------------------------------------------------------
// LED: Set solid color
// ---------------------------------------------------------
void TinZrCore::setLED(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness) {
	_tinzrPixel.setBrightness(brightness);
	_tinzrPixel.setPixelColor(0, _tinzrPixel.Color(r, g, b));
	_tinzrPixel.show();
}

// ---------------------------------------------------------
// LED: Turn completely off
// ---------------------------------------------------------
void TinZrCore::ledOff() {
	_tinzrPixel.setPixelColor(0, 0);
	_tinzrPixel.show();
}

// ---------------------------------------------------------
// LED: Flash any color any number of times
// ---------------------------------------------------------
void TinZrCore::flashColor(uint8_t r, uint8_t g, uint8_t b,
                           uint8_t times, uint16_t delayMs) {
	for (uint8_t i = 0; i < times; i++) {
		_tinzrPixel.setPixelColor(0, _tinzrPixel.Color(r, g, b));
		_tinzrPixel.show();
		delay(delayMs);

		_tinzrPixel.setPixelColor(0, 0);
		_tinzrPixel.show();
		delay(delayMs);
	}
}

// ---------------------------------------------------------
// Battery helpers
// ---------------------------------------------------------
float TinZrCore::readBatteryVoltage() {
	const int N = 16;
	uint32_t acc = 0;
	for (int i = 0; i < N; ++i) {
		acc += analogRead(PIN_BAT);
		delay(2);
	}
	float raw = acc / float(N);

	float v_div = raw * (VREF / ADC_MAX);
	float v_bat = v_div / DIVIDER_RATIO;

	return v_bat;
}

int TinZrCore::batteryPercent() {
	float v = readBatteryVoltage();

	if (v <= VBAT_MIN) return 0;
	if (v >= VBAT_MAX) return 100;

	float frac = (v - VBAT_MIN) / (VBAT_MAX - VBAT_MIN);
	int pct = int(frac * 100.0f + 0.5f);

	if (pct < 0)   pct = 0;
	if (pct > 100) pct = 100;
	return pct;
}

// ---------------------------------------------------------
// Soft power control
// ---------------------------------------------------------
void TinZrCore::softOff() {
	if (!_softOn) return;

	Serial.println("TinZrCore: SOFT OFF requested");

	// Flash white 5×
	flashColor(255, 255, 255, 5);

	// Turn off WiFi radio
	WiFi.disconnect(true);   // drop connection & clear runtime config
	WiFi.mode(WIFI_OFF);     // fully disable WiFi radio

	// ---- Turn off Serial (UART0 over USB-CDC) ----
	Serial.end();
	// Put TX/RX pins into high-Z inputs
	pinMode(TX, INPUT);
	pinMode(RX, INPUT);
	
	// ---- Turn off I2C (Wire) ----
	Wire.end();
	pinMode(SDA, INPUT);
	pinMode(SCL, INPUT);
	
	// ---- Turn off SPI ----
	SPI.end();
	pinMode(MOSI, INPUT);
	pinMode(MISO, INPUT);
	pinMode(SCK,  INPUT);
	pinMode(SS,   INPUT);
	
	
	// TODO: turn off BLE here if you use it

	// LED OFF
	ledOff();

	_softOn = false;
}

void TinZrCore::softOn() {
	if (_softOn) return;
	
	// At this point, all the buses are off and pins high-Z.
	// Your application is responsible for calling:
	//   Serial.begin(...)
	//   Wire.begin(...)
	//   SPI.begin(...)
	//   WiFi.mode(...)/WiFi.begin(...)
	// when it detects TinZr.isSoftOn() == true.
	
	Serial.println("TinZrCore: SOFT ON requested");

	// Flash white 5×
	flashColor(255, 255, 255, 5);

	// After turning ON, stay solid white
	setLED(255, 255, 255);

	// NOTE: we don't auto-enable WiFi here.
	// Your app / TinZrNet should re-init WiFi/BLE when isSoftOn() is true.

	_softOn = true;
}

// ---------------------------------------------------------
// Main handler – call from loop()
// ---------------------------------------------------------
void TinZrCore::handle() {
	bool pressed = readButtonRaw();
	uint32_t now = millis();

	// Edge detection
	if (pressed && !_buttonLast) {
		_pressStart       = now;
		_longPressLatched = false;
	}
	else if (!pressed && _buttonLast) {
		_pressStart       = 0;
		_longPressLatched = false;
	}

	// Long-press detection → toggle soft power
	if (pressed && !_longPressLatched && _pressStart != 0) {
		uint32_t heldMs = now - _pressStart;
		if (heldMs >= LONG_PRESS_MS) {
			_longPressLatched = true;

			if (_softOn) {
				softOff();
			} else {
				softOn();
			}
		}
	}

	_buttonLast = pressed;
}
