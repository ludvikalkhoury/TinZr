# TinZr ESP32-C3 Arduino Board Manager Package

This repository contains the **TinZr**, an ESP32-C3-based custom board definition for the Arduino IDE.  
It provides a clean, Boards-Manager-installable package so anyone can use your board with one simple URL.

---
## Pinout
![TinZr ESP32-C3 Pinout](https://github.com/ludvikalkhoury/TinZr/blob/main/docs/TinZr_Pinout.png?raw=true)




## 🚀 Quick Install

### 1️⃣ Add the Boards Manager URL
Open the Arduino IDE and go to:

**File → Preferences → Additional Boards Manager URLs**, then paste:

```
https://ludvikalkhoury.github.io/tinzr/package_tinzr_index.json
```


Click **OK**.

### 2️⃣ Install the board package
In Arduino IDE:
- Go to **Tools → Board → Boards Manager**
- Search for **TinZr Boards**
- Click **Install**

### 3️⃣ Select your board
Go to:

**Tools → Board → TinZr Boards → TinZr ESP32-C3 Rev4**

---

## 💡 Example Sketch (NeoPixel Test)

```cpp
#include <Adafruit_NeoPixel.h>

#define NUM_LEDS 1
Adafruit_NeoPixel strip(NUM_LEDS, RGB_BUILTIN, NEO_GRB + NEO_KHZ800);

void setup() {
  Serial.begin(115200);
  strip.begin();
  strip.setBrightness(128);
  strip.show();
}

void loop() {
  strip.setPixelColor(0, strip.Color(255, 0, 0)); strip.show(); delay(500);
  strip.setPixelColor(0, strip.Color(0, 255, 0)); strip.show(); delay(500);
  strip.setPixelColor(0, strip.Color(0, 0, 255)); strip.show(); delay(500);
}






