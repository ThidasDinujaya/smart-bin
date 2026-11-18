// wokwi demo of the smartbin firmware
// wiring: TRIG 5, ECHO 18, lid switch 27 (slider left = closed)
#include <WiFi.h>
#include <HTTPClient.h>

// config
const char* WIFI_SSID = "Wokwi-GUEST";            // wokwi's fake wifi
const char* WIFI_PASS = "";                       // no password
const char* SERVER    = "http://YOUR_EC2_PUBLIC_IP";   // your server, port 80
const char* API_KEY   = "change-me-device-key"; // same as API_KEY in .env
const char* BIN_ID    = "Bin-WOKWI";
const int TRIG_PIN = 5, ECHO_PIN = 18, LID_PIN = 27;
const unsigned long SETTLE_MS    = 2000UL;          // demo value, real is 5000
const unsigned long MIN_GAP_MS   = 3000UL;          // demo value, real is 180000
const unsigned long HEARTBEAT_MS = 120000UL;        // demo value, real is 3600000
const int LONG_POLL_SECONDS = 3;                // demo value, real is 20

volatile bool lidChanged = false;
void IRAM_ATTR onLidChange() { lidChanged = true; }

bool lidClosed = true;            // LOW means closed
int lidOpens = 0;
bool pending = false;
unsigned long closedAt = 0, lastReport = 0;

float measureOnceCm() {
  digitalWrite(TRIG_PIN, LOW);  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long us = pulseIn(ECHO_PIN, HIGH, 30000UL);       // 30 ms timeout
  return us == 0 ? -1.0f : us * 0.0343f / 2.0f;
}

float measureMedianCm() {                            // median of 5 skips bad echoes
  float s[5];
  for (int i = 0; i < 5; i++) { s[i] = measureOnceCm(); delay(60); }
  for (int i = 0; i < 4; i++) for (int j = i + 1; j < 5; j++) if (s[j] < s[i]) { float t = s[i]; s[i] = s[j]; s[j] = t; }
  return s[2];
}

void connectWifi() {
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\nWi-Fi connected");
}

void sendReport(const char* trigger, long commandId) {
  float d = measureMedianCm();
  if (d < 0) d = 0;                                  // server treats 0 as a fault
  char body[220];
  if (commandId > 0)
    snprintf(body, sizeof(body), "{\"bin_id\":\"%s\",\"distance_cm\":%.1f,\"trigger\":\"%s\",\"lid_opens\":%d,\"command_id\":%ld}",
             BIN_ID, d, trigger, lidOpens, commandId);
  else
    snprintf(body, sizeof(body), "{\"bin_id\":\"%s\",\"distance_cm\":%.1f,\"trigger\":\"%s\",\"lid_opens\":%d}",
             BIN_ID, d, trigger, lidOpens);
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  HTTPClient http;
  http.begin(String(SERVER) + "/api/readings");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-Key", API_KEY);
  int code = http.POST((uint8_t*)body, strlen(body));
  Serial.printf("POST %s -> %d\n", body, code);
  http.end();
  if (code == 200) { lidOpens = 0; pending = false; lastReport = millis(); }   // keep data if send failed
}

long pollForCommand() {                              // returns command id or 0
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  HTTPClient http;
  http.setTimeout((LONG_POLL_SECONDS + 5) * 1000);
  http.begin(String(SERVER) + "/api/bins/" + BIN_ID + "/commands/next?wait=" + LONG_POLL_SECONDS);
  http.addHeader("X-API-Key", API_KEY);
  int code = http.GET();
  long id = 0;
  if (code == 200) {
    String r = http.getString();                     // reply has the command id or null
    int p = r.indexOf("\"id\":");
    if (p >= 0) id = r.substring(p + 5).toInt();
  }
  http.end();
  return id;
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT); pinMode(ECHO_PIN, INPUT);
  pinMode(LID_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(LID_PIN), onLidChange, CHANGE);
  lidClosed = digitalRead(LID_PIN) == LOW;
  connectWifi();
  sendReport("heartbeat", 0);                        // first report on boot
}

void loop() {
  if (lidChanged) {                                  // lid changed
    lidChanged = false;
    delay(30);                                       // debounce
    bool nowClosed = digitalRead(LID_PIN) == LOW;
    if (nowClosed != lidClosed) {
      lidClosed = nowClosed;
      if (!lidClosed) { lidOpens++; pending = true; }
      else closedAt = millis();
    }
  }
  unsigned long now = millis();
  if (lidClosed && pending && now - closedAt >= SETTLE_MS && now - lastReport >= MIN_GAP_MS) {
    sendReport("lid_close", 0);
  } else if (now - lastReport >= HEARTBEAT_MS) {
    sendReport("heartbeat", 0);
  } else if (lidClosed && !pending) {
    long cmd = pollForCommand();                     // blocks while polling
    if (cmd > 0) sendReport("refresh", cmd);
  } else {
    delay(100);
  }
}
