#ifndef Pins_Arduino_h
#define Pins_Arduino_h

#include <stdint.h>
#include "soc/soc_caps.h"

// ===== Built-in RGB (WS2812C) on GPIO8 =====
#define PIN_RGB_LED 8
// Use a "virtual" builtin index so rgbLedWrite() works (not digitalWrite)
static const uint8_t LED_BUILTIN = SOC_GPIO_PIN_COUNT + PIN_RGB_LED;
#define BUILTIN_LED  LED_BUILTIN
#define RGB_BUILTIN  LED_BUILTIN
#define RGB_BRIGHTNESS 128   // 0–255 default

// ===== UART0 defaults (USB-CDC) =====
static const uint8_t TX = 21;
static const uint8_t RX = 20;

// ===== I2C defaults (avoid strap pins 8/9) =====
static const uint8_t SDA = 4;
static const uint8_t SCL = 5;
#define PIN_WIRE_SDA SDA
#define PIN_WIRE_SCL SCL

// ===== SPI defaults =====
static const uint8_t SS   = 10;
static const uint8_t MOSI = 2;
static const uint8_t MISO = 7;
static const uint8_t SCK  = 6;
#define PIN_SPI_SS   SS
#define PIN_SPI_MOSI MOSI
#define PIN_SPI_MISO MISO
#define PIN_SPI_SCK  SCK

// ===== Analog aliases (C3: ADC1=GPIO0..4, ADC2=GPIO5) =====
static const uint8_t A0 = 0;
static const uint8_t A1 = 1;
static const uint8_t A2 = 2;
static const uint8_t A3 = 255; // not available on this board
static const uint8_t A4 = 4;
static const uint8_t A5 = 5;   // ADC2; OK but has caveats vs ADC1

#endif /* Pins_Arduino_h */
