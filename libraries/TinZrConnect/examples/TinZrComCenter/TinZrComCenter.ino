/*
 * ================================================================
 *  TinZr → Wi-Fi Hub Communication Firmware (PC-Controlled)
 * ================================================================
 *
 * This Arduino sketch implements the TinZr-side firmware required
 * for bidirectional communication between one or more TinZr devices
 * and a PC-based control application acting as a central hub.
 *
 * The design supports scalable, multi-device control using a
 * combination of UDP multicast discovery and TCP-based command
 * channels, enabling a Python GUI (or other host software) to
 * discover, connect to, and control multiple TinZr units
 * simultaneously.
 *
 * ---------------------------------------------------------------
 * Features
 * ---------------------------------------------------------------
 *  - Wi-Fi initialization with configurable device hostname
 *  - UDP multicast support for discovery and broadcast messaging
 *  - TCP client support for reliable command/control traffic
 *  - Single firmware image usable across multiple TinZr devices
 *  - Fully non-blocking communication servicing model
 *
 * ---------------------------------------------------------------
 * System Architecture
 * ---------------------------------------------------------------
 *  PC (Hub)
 *    ├─ Python-based GUI / control software
 *    ├─ Receives multicast announcements from TinZr devices
 *    ├─ Sends control commands via TCP
 *    └─ Manages one or many TinZr nodes concurrently
 *   
 *  TinZr Device(s)
 *    ├─ Join Wi-Fi network
 *    ├─ Advertise presence via multicast UDP
 *    ├─ Establish TCP connection to hub
 *    └─ Exchange commands, status, and data
 *
 * ---------------------------------------------------------------
 * Behavior
 * ---------------------------------------------------------------
 * 1. On startup:
 *    - Wi-Fi configuration is populated via TinZrWiFiConfig
 *    - Multicast networking is enabled for discovery
 *    - TCP client parameters are configured for hub connection
 *    - WiFiCom subsystem is initialized
 *
 * 2. Runtime operation:
 *    - WiFiCom.handle() is called continuously in loop()
 *    - Internally:
 *        • Wi-Fi connectivity is maintained
 *        • Multicast packets are sent/received
 *        • TCP connection to the hub is established and serviced
 *        • Incoming commands are parsed and dispatched
 *
 * ---------------------------------------------------------------
 * Configuration Parameters
 * ---------------------------------------------------------------
 *  - ssid / pass
 *      Wi-Fi network credentials
 *
 *  - hostname
 *      Logical device identifier used by the hub
 *
 *  - mcast_enable
 *      Enables UDP multicast discovery
 *
 *  - mcast_group / mcast_port
 *      Multicast address and port for discovery/broadcast
 *
 *  - udp_port
 *      Local UDP port used by the device
 *
 *  - tcp_enable
 *      Enables TCP-based command/control channel
 *
 *  - hub_ip / tcp_port
 *      IP address and port of the PC hub
 *
 * ---------------------------------------------------------------
 * System Timing
 * ---------------------------------------------------------------
 *  - All communication is serviced cooperatively
 *  - No delay() calls are required
 *  - loop() remains responsive and non-blocking
 *
 * ---------------------------------------------------------------
 * Dependencies
 * ---------------------------------------------------------------
 * - TinZrWiFi
 *     Provides:
 *       - Wi-Fi configuration and connection management
 *
 * - TinZrWiFiCom
 *     Provides:
 *       - UDP multicast discovery
 *       - TCP client communication
 *       - Unified handle-based networking interface
 *
 * ---------------------------------------------------------------
 * Notes
 * ---------------------------------------------------------------
 * - This firmware is intended to be paired with a PC-based GUI
 * - Multiple TinZr devices may run this same firmware concurrently
 * - Hub-side software is responsible for device coordination
 * - Ideal for:
 *     • Multi-device wearable control
 *     • Distributed data acquisition
 *     • Interactive experiments and demos
 *
 * TinZr Platform — Wi-Fi Hub Communication Example
 * ================================================================
 */
 
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
