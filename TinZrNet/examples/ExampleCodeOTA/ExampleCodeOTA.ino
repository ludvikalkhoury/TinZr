/*
===============================================================================
TinZr — WiFi + WebServer test (no IMU)
===============================================================================

WHAT THIS DOES
--------------
- Stores SSID / PASS / HOSTNAME in TinZrConsoleDefaults DEF
  (so your Python flasher can still patch them)
- Connects to WiFi using DEF.ssid / DEF.pass
- Sets WiFi hostname from DEF.hostname
- Uses NeoPixel as connect status:
    - Blinks red while connecting
    - Solid blue when connected
- Starts a simple HTTP server on port 80:
    - GET /      → HTML page with TinZr title + shows IP / hostname
===============================================================================
*/

#include <TinZrOTA.h>
#include <TinZrConsole.h>      // Only for TinZrConsoleDefaults type (no Console.begin here)
#include <Adafruit_NeoPixel.h>

#include <WiFi.h>
#include <WebServer.h>

// ========== OTA-LIKE SETTINGS (FOR PATCHER) ==========
// IMPORTANT: keep these as string literals so your Python flasher can replace them!
TinZrConsoleDefaults DEF = {
  .ssid       = "Ludvik",
  .pass       = "Lud12345",
  .hostname   = "TinZr-ota",
  .use_static = false
};
// We don't actually use TinZrConsole/OTA in this sketch; DEF is just the
// canonical place for SSID/PASS/HOSTNAME so your GUI auto-patcher works.

// Convenience aliases for WiFi logic
const char* WIFI_SSID     = DEF.ssid;
const char* WIFI_PASSWORD = DEF.pass;
const char* WIFI_HOSTNAME = DEF.hostname;

// ========== LED (NeoPixel) SETUP ==========

#define NUM_PIXELS 1
Adafruit_NeoPixel pixels(NUM_PIXELS, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

static inline void setColor(uint8_t r, uint8_t g, uint8_t b) {
  pixels.setPixelColor(0, pixels.Color(r, g, b));
  pixels.show();
}

// ========== WEB SERVER ==========

WebServer server(80);

// Cache for IP / hostname (for HTML)
String g_hostname;
IPAddress g_ip;

// Root handler: simple HTML page with TinZr info
void handleRoot() {
  String html;
  html.reserve(512);

  html += F("<!DOCTYPE html><html><head>"
            "<meta charset='utf-8'/>"
            "<meta http-equiv='refresh' content='2'/>"
            "<title>TinZr Board</title>"
            "<style>body{font-family:monospace;background:#f7f7f7;padding:1rem;}h1{color:#333;}</style>"
            "</head><body>");

  html += F("<h1>TinZr Device</h1>");
  html += F("<p><b>Hostname:</b> ");
  html += g_hostname;
  html += F("</p><p><b>IP:</b> ");
  html += g_ip.toString();
  html += F("</p>");

  html += F("<p>This is a simple TinZr WiFi/WebServer test page.<br>"
            "The Python GUI can use this page to detect devices on the network.</p>");

  html += F("</body></html>");

  server.send(200, "text/html; charset=utf-8", html);
}

void handleNotFound() {
  server.send(404, "text/plain", "Not found");
}

// ========== SETUP ==========

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("🚀 TinZr WiFi + WebServer test booting...");

  // LED init
  pixels.begin();
  setColor(0, 0, 0);

  // ---- WiFi setup (IMU-style logic, no TinZrConsole.begin) ----
  WiFi.mode(WIFI_STA);

  // Hostname from DEF.hostname (patched by your flasher)
  if (WIFI_HOSTNAME && strlen(WIFI_HOSTNAME) > 0) {
    WiFi.setHostname(WIFI_HOSTNAME);
  }

  Serial.print("📶 Connecting to SSID: ");
  Serial.println(WIFI_SSID);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);  // Moderate TX power

  // Blink red while connecting
  while (WiFi.status() != WL_CONNECTED) {
    setColor(64, 0, 0);  // Red
    Serial.print("⏳ Connecting... Status: ");
    Serial.println(WiFi.status());
    delay(300);
    setColor(0, 0, 0);
    delay(500);
  }

  // Connected
  g_ip = WiFi.localIP();
  g_hostname = WIFI_HOSTNAME;

  Serial.println("✅ WiFi connected!");
  Serial.print("📡 IP address: ");
  Serial.println(g_ip);
  Serial.print("🧷 Hostname: ");
  Serial.println(g_hostname);

  setColor(0, 0, 255);  // Blue

  // ---- Web server setup ----
  server.on("/", handleRoot);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.println("🌐 Web server started on port 80");
}

// ========== LOOP ==========

void loop() {
  // Serve HTTP requests
  server.handleClient();

  // Optionally you can blink or animate LED here if you want.
  // For now, keep it solid blue when connected.
}
