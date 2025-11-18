#pragma once
#include <Arduino.h>

// TinZrCore: basic board services
// - Battery read using 220k/150k divider on A1
// - Long-press PB (5 s) toggles soft ON/OFF
// - Soft OFF: WiFi off, LED off, app should idle

class TinZrCore {
public:
	// Call once in setup()
	void begin();

	// Call frequently in loop()
	void handle();

	// Battery helpers
	float readBatteryVoltage();  // in Volts
	int   batteryPercent();      // 0–100 (simple linear map)

	// Soft power state
	bool isSoftOn() const { return _softOn; }

	// Force soft-off or soft-on from user code if you want
	void softOff();
	void softOn();

	// ----- LED CONTROL API -----
	void setLED(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness = 25);
	void ledOff();
	void flashColor(uint8_t r, uint8_t g, uint8_t b,
	                uint8_t times, uint16_t delayMs = 120);

private:
	// ==== HARDWARE CONSTANTS ====

	// ADC pin for VBAT divider (A1 = 1 from Pins_Arduino_h)
	static constexpr int PIN_BAT = A1;

	// Button logic level: true if pressed = LOW (with pull-up)
	static constexpr bool BUTTON_ACTIVE_LOW = true;

	// Long-press duration in ms
	static constexpr uint32_t LONG_PRESS_MS = 5000; // 5 seconds

	// ADC / divider (from your working code)
	static constexpr float VREF    = 3.3f;      // board supply / ADC ref
	static constexpr float ADC_MAX = 4095.0f;   // 12-bit on ESP32-C3

	// Divider: top=220k (VBAT->ADC), bottom=150k (ADC->GND)
	// DIVIDER_RATIO = Vadc / Vbat
	static constexpr float DIVIDER_RATIO =
		150000.0f / (220000.0f + 150000.0f);    // ≈ 0.405

	// Simple % mapping (tweak as you like)
	static constexpr float VBAT_MIN = 3.3f;     // ~0%
	static constexpr float VBAT_MAX = 4.2f;     // ~100%

	// ==== RUNTIME STATE ====
	bool     _buttonLast       = false;
	uint32_t _pressStart       = 0;
	bool     _longPressLatched = false;
	bool     _softOn           = true;  // start ON after boot

	// Helpers
	bool readButtonRaw() const;
};

extern TinZrCore TinZr;
