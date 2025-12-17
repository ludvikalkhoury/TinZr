#pragma once
#include <Arduino.h>

// =============================
// TinZrCore  (battery, button, soft power, base LED)
// =============================
class TinZrCore {
public:
    void begin();   // call from setup()
    void handle();  // call from loop()

    // Battery helpers
    float readBatteryVoltage();  // Volts
    int   batteryPercent();      // 0–100

    // Soft power
    bool isSoftOn() const { return _softOn; }
    void softOff();
    void softOn();

    // LED base control (single NeoPixel on PIN_RGB_LED)
    void setLED(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness = 25);
    void ledOff();
    void flashColor(uint8_t r, uint8_t g, uint8_t b,
                    uint8_t times, uint16_t delayMs = 120);

private:
    // Hardware constants
    static constexpr int   PIN_BAT = A1;       // VBAT divider
    static constexpr bool  BUTTON_ACTIVE_LOW = true;
    static constexpr uint32_t LONG_PRESS_MS = 5000;  // 5s

    static constexpr float VREF    = 3.3f;
    static constexpr float ADC_MAX = 4095.0f;

    // Divider 220k (top) / 150k (bottom)
    static constexpr float DIVIDER_RATIO =
        150000.0f / (220000.0f + 150000.0f);

    static constexpr float VBAT_MIN = 3.3f;
    static constexpr float VBAT_MAX = 4.2f;

    bool     _buttonLast       = false;
    uint32_t _pressStart       = 0;
    bool     _longPressLatched = false;
    bool     _softOn           = true;

    bool readButtonRaw() const;
};

extern TinZrCore TinZr;
