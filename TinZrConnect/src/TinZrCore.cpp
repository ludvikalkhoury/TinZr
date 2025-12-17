#include "TinZrCore.h"
#include <Adafruit_NeoPixel.h>
#include <WiFi.h>
#include <Wire.h>
#include <SPI.h>

// Expect PIN_RGB_LED and PB_PIN to be defined in variant or config
#ifndef PIN_RGB_LED
#define PIN_RGB_LED 8
#endif

#ifndef PB_PIN
#define PB_PIN 9
#endif

TinZrCore TinZr;

// NeoPixel owned by TinZrCore
static Adafruit_NeoPixel _tinzrPixel(1, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

// --------- internal helpers ---------
bool TinZrCore::readButtonRaw() const {
    int level = digitalRead(PB_PIN);
    if (BUTTON_ACTIVE_LOW) {
        return (level == LOW);
    } else {
        return (level == HIGH);
    }
}

// --------- public API ---------
void TinZrCore::begin() {
    // Button
    if (BUTTON_ACTIVE_LOW) {
        pinMode(PB_PIN, INPUT_PULLUP);
    } else {
        pinMode(PB_PIN, INPUT_PULLDOWN);
    }
    _buttonLast       = readButtonRaw();
    _pressStart       = 0;
    _longPressLatched = false;
    _softOn           = true;

    // Battery
    pinMode(PIN_BAT, INPUT);

    // LED
    _tinzrPixel.begin();
    _tinzrPixel.setBrightness(25);
    _tinzrPixel.clear();
    _tinzrPixel.show();

    float vbat = readBatteryVoltage();
    int   pct  = batteryPercent();

    Serial.println("==== TinZrCore boot ====");
    Serial.print("Battery: ");
    Serial.print(vbat, 3);
    Serial.print(" V (");
    Serial.print(pct);
    Serial.println(" %)");

    flashColor(255, 255, 255, 5);
    setLED(255, 255, 255);
}

void TinZrCore::setLED(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness) {
    _tinzrPixel.setBrightness(brightness);
    _tinzrPixel.setPixelColor(0, _tinzrPixel.Color(r, g, b));
    _tinzrPixel.show();
}

void TinZrCore::ledOff() {
    _tinzrPixel.setPixelColor(0, 0);
    _tinzrPixel.show();
}

void TinZrCore::flashColor(uint8_t r, uint8_t g, uint8_t b,
                           uint8_t times, uint16_t delayMs) {
    for (uint8_t i = 0; i < times; ++i) {
        _tinzrPixel.setPixelColor(0, _tinzrPixel.Color(r, g, b));
        _tinzrPixel.show();
        delay(delayMs);
        _tinzrPixel.setPixelColor(0, 0);
        _tinzrPixel.show();
        delay(delayMs);
    }
}

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

void TinZrCore::softOff() {
    if (!_softOn) return;

    Serial.println("TinZrCore: SOFT OFF");
    flashColor(255,255,255,5);

    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);

    Serial.end();
    pinMode(TX, INPUT);
    pinMode(RX, INPUT);

    Wire.end();
    pinMode(SDA, INPUT);
    pinMode(SCL, INPUT);

    SPI.end();
    pinMode(MOSI, INPUT);
    pinMode(MISO, INPUT);
    pinMode(SCK,  INPUT);
    pinMode(SS,   INPUT);

    ledOff();
    _softOn = false;
}

void TinZrCore::softOn() {
    if (_softOn) return;
    Serial.println("TinZrCore: SOFT ON");
    flashColor(255,255,255,5);
    setLED(255,255,255);
    _softOn = true;
    delay(150);
    ESP.restart();
}

void TinZrCore::handle() {
    bool pressed = readButtonRaw();
    uint32_t now = millis();

    if (pressed && !_buttonLast) {
        _pressStart       = now;
        _longPressLatched = false;
    } else if (!pressed && _buttonLast) {
        _pressStart       = 0;
        _longPressLatched = false;
    }

    if (pressed && !_longPressLatched && _pressStart != 0) {
        uint32_t held = now - _pressStart;
        if (held >= LONG_PRESS_MS) {
            _longPressLatched = true;
            if (_softOn) softOff();
            else         softOn();
        }
    }

    _buttonLast = pressed;
}
