/*
 * ================================================================
 *  TinZr Button, LED, and Battery Demo
 * ================================================================
 *
 * This Arduino sketch demonstrates basic interaction with the
 * TinZr platform, including:
 *
 *  - Reading the onboard push button
 *  - Flashing the onboard status RGB LED
 *  - Reading battery voltage and percentage
 *
 * ---------------------------------------------------------------
 * Behavior
 * ---------------------------------------------------------------
 * 1. Initializes the TinZr core and status LED
 * 2. Continuously monitors the push button
 * 3. When the button is pressed:
 *    - Flashes the status LED green
 *    - Reads battery voltage and percentage
 *    - Prints results to the Serial Monitor
 *
 * The button is assumed to be active-low (pressed = LOW),
 * as defined in the TinZrCore library.
 *
 * ---------------------------------------------------------------
 * Dependencies
 * ---------------------------------------------------------------
 * - TinZrCore
 *     Provides:
 *       - Global TinZrCore object: `TinZr`
 *       - Button handling
 *       - Battery voltage and percentage reading
 *
 * - TinZrLED
 *     Provides:
 *       - Global TinZrStatusLED object: `TinZrLED`
 *       - RGB LED control and animations
 *
 * ---------------------------------------------------------------
 * Serial Output
 * ---------------------------------------------------------------
 * Baud rate: 115200
 *
 * Example output:
 *   Flashing 'green'. Battery Voltage: 3.92 V, Battery Percent: 87 %
 *
 * ---------------------------------------------------------------
 * Notes
 * ---------------------------------------------------------------
 * - LED flashing is intended for demo/testing use
 * - Battery percentage is derived from voltage thresholds
 * - This sketch is intended for bring-up, testing, and validation
 *
 * TinZr Platform — Demo / Example Sketch
 * ================================================================
 */


#include <Arduino.h>
#include "TinZrCore.h" // this library declares TinZrCore TinZr
#include "TinZrLED.h"  // this library declares TinZrStatusLED TinZrLED


void setup() {
    //Begin Serial
    Serial.begin(115200);
    delay(100);

    Serial.println("=== TinZr Button, LED, and Battery level reading Demo ===");

    //Initialize the TinZr
    TinZr.begin(25); //LED brightness is 25

}

void loop() {

    static bool lastButtonState = true;

    bool buttonState = TinZr.readButtonState();

    if (lastButtonState && !buttonState){
        TinZrLED.flashColor(0,255,0, 50, 5);
        float batteryVolt = TinZr.readBatteryVoltage();
        float batteryPerc = TinZr.readBatteryPercent();
        Serial.printf(
            "Flashing 'green'. Battery Voltage: %.2f V, Battery Percent: %.1f %%\n",
            batteryVolt,
            batteryPerc
        );
    }
    lastButtonState = buttonState;

    delay(10);

}