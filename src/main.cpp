#include <M5Stack.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

// ===== WiFi =====
const char* ssid = "jikei-open-air";
const char* password = "open-wifi";

// ===== MQTT =====
const char* mqtt_server = "broker.hivemq.com";
const int mqtt_port = 1883;

const char* topic_sub = "m5stack/device_02/servo";
const char* topic_pub = "m5stack/device_02/status";
// ===== MQTT =====
WiFiClient espClient;
PubSubClient client(espClient);

// ===== Servo =====
Servo myServo;
int servoPin = 21;

// ===== State Detect =====
const int SW_UNLOCK = 22;
const int SW_LOCK   = 19;

int lastState = -1;

// ===== MQTT受信 =====
void callback(char* topic, byte* payload, unsigned int length) {

    String json = "";

    for (int i = 0; i < length; i++) {
        json += (char)payload[i];
    }

    Serial.println("受信:");
    Serial.println(json);

    DynamicJsonDocument doc(256);

    DeserializationError error = deserializeJson(doc, json);

    if (error) {
        Serial.println("JSON解析失敗");
        return;
    }

    int angle = doc["angle"];
    int duration = doc["duration"];

    Serial.print("angle: ");
    Serial.println(angle);

    Serial.print("duration: ");
    Serial.println(duration);

    // サーボ制御
    myServo.write(angle);

    delay(duration);

    myServo.write(90);

    M5.Lcd.fillScreen(BLACK);

    M5.Lcd.setCursor(20, 20);

    M5.Lcd.printf("Angle: %d\n", angle);
    M5.Lcd.printf("Time: %d ms\n", duration);
}

// ===== MQTT再接続 =====
void reconnect() {

    while (!client.connected()) {

        Serial.println("MQTT connecting...");

        String clientId = "M5Client-";
        clientId += String(random(0xffff), HEX);

        if (client.connect(clientId.c_str())) {

            Serial.println("MQTT connected");

            client.subscribe(topic_sub);

        } else {

            Serial.print("failed: ");
            Serial.println(client.state());

            delay(5000);
        }
    }
}

// ===== 鍵状態判定 =====
void checkLockState() {

    bool unlockSW = (digitalRead(SW_UNLOCK) == LOW);
    bool lockSW   = (digitalRead(SW_LOCK) == LOW);

    int state;

    if (unlockSW && !lockSW) {
        state = 0; // 開錠
    }
    else if (!unlockSW && lockSW) {
        state = 1; // 施錠
    }
    else if (!unlockSW && !lockSW) {
        state = 2; // 中間
    }
    else {
        state = 3; // 異常
    }

    if (state != lastState) {

        String sendMsg = "";

        switch (state) {
            
            case 0:
                Serial.println("UNLOCK");
                M5.Lcd.fillScreen(BLACK);
                M5.Lcd.setCursor(20, 20);
                M5.Lcd.println("UNLOCK");
                sendMsg = "2,1";
                break;

            case 1:
                Serial.println("LOCK");
                M5.Lcd.fillScreen(BLACK);
                M5.Lcd.setCursor(20, 20);
                M5.Lcd.println("LOCK");
                sendMsg = "2,2";
                break;

            case 2:
                Serial.println("MIDDLE");
                M5.Lcd.fillScreen(BLACK);
                M5.Lcd.setCursor(20, 20);
                M5.Lcd.println("MIDDLE");
                sendMsg = "2,3";
                break;

            case 3:
                Serial.println("ERROR");
                M5.Lcd.fillScreen(BLACK);
                M5.Lcd.setCursor(20, 20);
                M5.Lcd.println("ERROR");
                sendMsg = "2,4";
                break;
        }
        client.publish(topic_pub, sendMsg.c_str());
        Serial.print("MQTT送信: ");
        Serial.println(sendMsg);

        lastState = state;
    }
}

// ===== setup =====
void setup() {

    M5.begin();

    Serial.begin(115200);

    M5.Lcd.setTextSize(2);

    // Servo
    myServo.attach(servoPin);
    myServo.write(90);

    // Switch
    pinMode(SW_UNLOCK, INPUT_PULLUP);
    pinMode(SW_LOCK, INPUT_PULLUP);

    // WiFi
    WiFi.begin(ssid, password);

    M5.Lcd.println("WiFi connecting...");

    while (WiFi.status() != WL_CONNECTED) {

        delay(500);
        Serial.print(".");
    }

    M5.Lcd.println("WiFi connected");

    // MQTT
    client.setServer(mqtt_server, mqtt_port);
    client.setCallback(callback);
}

// ===== loop =====
void loop() {

    if (!client.connected()) {
        reconnect();
    }

    client.loop();

    // 状態監視
    checkLockState();

    delay(20);
}