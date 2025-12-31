#include <Arduino.h>
#include "TinZrWiFi.h"
#include "TinZrWiFiCom.h"

void setup() {
  TinZrWiFiConfig wifi_cfg;
  wifi_cfg.ssid          = "Ludvik";
  wifi_cfg.pass          = "Lud12345";
  wifi_cfg.hostname      = "TinZrWiFi2";
  wifi_cfg.mcast_enable  = true;
  wifi_cfg.mcast_group   = IPAddress(239,1,1,1);
  wifi_cfg.mcast_port    = 4210;
  wifi_cfg.udp_port      = 4210;
  wifi_cfg.tcp_enable    = true;
  wifi_cfg.hub_ip        = IPAddress(172,20,10,4);
  wifi_cfg.tcp_port      = 4211;

	WiFiCom.begin(wifi_cfg);

}

void loop() {
	WiFiCom.handle();
}
