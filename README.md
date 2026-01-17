# TinZr ESP32-C3 Arduino Board Manager Package

This repository contains the **TinZr**, an ESP32-C3-based custom board definition for the Arduino IDE.  
It provides a clean, Boards-Manager-installable package so anyone can use your board with one simple URL.

---
## Pinout
![TinZr ESP32-C3 Pinout](https://github.com/ludvikalkhoury/TinZr/blob/main/docs/TinZr_Pinout.png?raw=true)




## 🚀 Quick Install

### 1️⃣ Install Espressif ESP32
Open the Arduino IDE and go to:
- Go to **Tools → Board → Boards Manager**
- Find ``esp32 by Espressif Systems``
- Install *only* version `3.3.4`

**⚠️ IMPORTANT: This project is tested and supported exclusively with ESP32 core version `3.3.4`.**  
**Do NOT use any other version — builds may fail or behave incorrectly.**


### 2️⃣ Add the Boards Manager URL
Open the Arduino IDE and go to:

**File → Preferences → Additional Boards Manager URLs**, then paste:

```
https://ludvikalkhoury.github.io/TinZr/package_tinzr_index.json
```

Click **OK**.

### 3️⃣ Install the board package
In Arduino IDE:
- Go to **Tools → Board → Boards Manager**
- Search for **TinZr**
- Click **Install**

### 4️⃣ Select your board
Go to:

**Tools → Board → TinZr Boards → TinZr ESP32-C3 Rev4**

---

## 💡 Example Sketch (NeoPixel Test)

```cpp
#include <Arduino.h>
#include "TinZrCore.h" // this library declares TinZrCore TinZr
#include "TinZrLED.h"  // this library declares TinZrStatusLED TinZrLED


void setup() {
    TinZr.begin(25); //LED brightness is 25
}

void loop() {
    TinZrLED.setColor(255,0,0); delay(500);
    TinZrLED.setColor(0,255,0); delay(500);
    TinZrLED.setColor(0,0,255); delay(500);
}
```











