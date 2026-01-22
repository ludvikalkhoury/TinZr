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


### 5️⃣ Install `TinZrConnect` as a Zipped Arduino Library
#### A) Create the ZIP (IMPORTANT: zip the *library folder*, not the whole TinZr repo)
- Navigate to the `libraries` directory. There you will find the `TinZrConnect` folder (the one that contains `library.properties`).
- Create a zipped version of the `TinZrConnect` folder, and name it `TinZrConnect.zip`.

#### B) Install the ZIP in Arduino IDE
1. In Arduino IDE, go to:
   - **Sketch → Include Library → Add .ZIP Library…**
2. Select `TinZrConnect.zip`
3. Wait for the confirmation message (bottom status bar).

#### C) Verify Installation
1. Go to:
   - **File → Examples**
2. Scroll to find:
   - **TinZrConnect**
3. Open an example sketch and compile.

If you see the examples and the sketch compiles, the library is installed correctly ✅


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



Copyright (c) 2026 Ludvik Alkhoury

This software is licensed under the GNU General Public License v3.0.
Commercial licensing options are available upon request.











