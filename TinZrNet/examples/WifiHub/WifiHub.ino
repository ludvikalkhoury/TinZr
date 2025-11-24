#include <Arduino.h>
#include "TinZrNode.h"  

TinZrNode Node;

TinZrNodeConfig cfg = {
  .ssid       = "Ludvik",
  .pass       = "Lud12345",
  .hostname   = "TinZrNode1",  // or nullptr to reuse saved hostname
  .use_static = false,
  .hubTcpPort  = 4211,
  .hubUdpPort  = 4210,
  .hubMcastGrp = IPAddress(239, 1, 1, 1),
  .hubIP       = IPAddress(172, 20, 10, 4),  // PC IP
};

void setup() {
  Node.begin(cfg);
}

void loop() {
  Node.handle();
  
}
