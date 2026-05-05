import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional

import paho.mqtt.client as mqtt


class MqttSensorService:
    """
    센서 MQTT 메시지 처리 서비스

    역할:
    - sensors/+/status 구독
    - sensors/+/telemetry 구독
    - register / unregister / set_interval 명령 publish
    - 등록된 센서만 DB 반영
    """

    def __init__(self, db_handler, broker_host="127.0.0.1", broker_port=1883):
        self.db_handler = db_handler
        self.broker_host = broker_host
        self.broker_port = broker_port

        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self._thread: Optional[threading.Thread] = None
        self._running = False

        self.hr_windows = defaultdict(lambda: deque(maxlen=10))
        self.high_hr_start_times = {}

    def start(self):
        if self._running:
            return

        try:
            self.client.connect(self.broker_host, self.broker_port, 60)
            self._thread = threading.Thread(target=self.client.loop_forever, daemon=True)
            self._thread.start()
            self._running = True
            print("[MQTT Sensor] service started")
        except Exception as e:
            self._running = False
            print(f"[MQTT Sensor] service start failed: {e}")

    def stop(self):
        self._running = False
        try:
            self.client.disconnect()
        except Exception:
            pass
        print("[MQTT Sensor] service stopped")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("[MQTT Sensor] connected")
            client.subscribe("sensors/+/status")
            client.subscribe("sensors/+/telemetry")
        else:
            print(f"[MQTT Sensor] connect failed: {rc}")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
        except Exception as e:
            print(f"[MQTT Sensor] invalid json on {topic}: {e}")
            return

        if topic.endswith("/status"):
            self._handle_status(payload)
        elif topic.endswith("/telemetry"):
            self._handle_telemetry(payload)

    def _handle_status(self, payload: dict):
        sensor_id = payload.get("sensor_id")
        if not sensor_id:
            return

        if not self.db_handler.is_registered_sensor(sensor_id):
            print(f"[MQTT Sensor] skip unregistered sensor status: {sensor_id}")
            return

        print(f"[MQTT Sensor] status: {sensor_id}")

        try:
            self.db_handler.update_sensor_online(
                sensor_id=sensor_id,
                is_online=True,
                last_seen_at=datetime.now()
            )
        except Exception as e:
            print(f"[MQTT Sensor] status DB update failed: {e}")

    def _handle_telemetry(self, payload: dict):
        sensor_id = payload.get("sensor_id")
        if not sensor_id:
            return

        if not self.db_handler.is_registered_sensor(sensor_id):
            return

        sensor_type = payload.get("sensor_type", "unknown")

        try:
            self.db_handler.update_sensor_online(
                sensor_id=sensor_id,
                is_online=True,
                last_seen_at=datetime.now()
            )
        except Exception as e:
            print(f"[MQTT Sensor] online update failed: {e}")

        if sensor_type == "temp_humidity":
            self._handle_temp_humidity_telemetry(sensor_id, payload)

        elif sensor_type == "heart_band":
            self._handle_heart_band_telemetry(sensor_id, payload)

        else:
            print(f"[MQTT Sensor] unknown telemetry type: {sensor_id}, type={sensor_type}")

    def _handle_temp_humidity_telemetry(self, sensor_id: str, payload: dict):
        temperature = payload.get("temperature")
        humidity = payload.get("humidity")

        print(f"[MQTT Sensor] temp/humidity: {sensor_id} T={temperature} H={humidity}")

        try:
            self.db_handler.save_sensor_telemetry(
                sensor_id=sensor_id,
                temperature=temperature,
                humidity=humidity,
                ts=datetime.now()
            )
        except Exception as e:
            print(f"[MQTT Sensor] temp/humidity save failed: {e}")

    def _handle_heart_band_telemetry(self, sensor_id: str, payload: dict):
        hr = payload.get("hr")

        if hr is None:
            print(f"[MQTT Sensor] heart_band telemetry missing hr: {sensor_id}")
            return

        try:
            hr = float(hr)
        except Exception:
            print(f"[MQTT Sensor] invalid hr value: {sensor_id}, hr={hr}")
            return
            
        if hr <= 0:
            return

        print(f"[MQTT Sensor] heart_band: {sensor_id} HR={hr}")

        # DB 저장: 이 함수는 DB Handler에 새로 만들거나 기존 telemetry 테이블 구조에 맞춰 수정 필요
        try:
            if hasattr(self.db_handler, "save_heart_rate_telemetry"):
                self.db_handler.save_heart_rate_telemetry(
                    sensor_id=sensor_id,
                    hr=hr,
                    ts=datetime.now()
                )
        except Exception as e:
            print(f"[MQTT Sensor] heart rate save failed: {e}")

        self._check_heart_rate_alert(sensor_id, hr)

    def _check_heart_rate_alert(self, sensor_id: str, hr: float):
        window = self.hr_windows[sensor_id]
        window.append(hr)

        avg_hr = sum(window) / len(window)

        print(f"[MQTT Sensor] HR avg: {sensor_id} current={hr:.1f}, avg={avg_hr:.1f}")

        if avg_hr >= 130:
            if sensor_id not in self.high_hr_start_times:
                self.high_hr_start_times[sensor_id] = time.time()

            duration = time.time() - self.high_hr_start_times[sensor_id]
            print(f"[MQTT Sensor] high HR 유지 중: {sensor_id}, {duration:.1f}s")

            if duration >= 5:
                self.publish_alert(
                    sensor_id=sensor_id,
                    color="red",
                    vibration=True,
                    led=True,
                    duration_ms=5000,
                    reset_after_ms=10000
                )

                # 알람 후 타이머 리셋
                self.high_hr_start_times.pop(sensor_id, None)

        else:
            self.high_hr_start_times.pop(sensor_id, None)

    def publish_register(self, sensor_id: str, site_id: str, interval_ms: int = 5000):
        topic = f"sensors/{sensor_id}/cmd"
        payload = {
            "cmd": "register",
            "site_id": site_id,
            "interval_ms": interval_ms
        }
        self.client.publish(topic, json.dumps(payload))
        print(f"[MQTT Sensor] register -> {topic}")

    def publish_unregister(self, sensor_id: str):
        topic = f"sensors/{sensor_id}/cmd"
        payload = {
            "cmd": "unregister"
        }
        self.client.publish(topic, json.dumps(payload))
        print(f"[MQTT Sensor] unregister -> {topic}")

    def publish_set_interval(self, sensor_id: str, interval_ms: int):
        topic = f"sensors/{sensor_id}/cmd"
        payload = {
            "cmd": "set_interval",
            "interval_ms": interval_ms
        }
        self.client.publish(topic, json.dumps(payload))
        print(f"[MQTT Sensor] set_interval -> {topic}")

    def publish_alert(
        self,
        sensor_id: str,
        color: str = "red",
        vibration: bool = True,
        led: bool = True,
        duration_ms: int = 5000,
        reset_after_ms: int = 10000
    ):
        topic = f"sensors/{sensor_id}/alert"
        payload = {
            "command": "alert_on",
            "color": color,
            "vibration": vibration,
            "led": led,
            "duration_ms": duration_ms,
            "reset_after_ms": reset_after_ms
        }

        self.client.publish(topic, json.dumps(payload))
        print(f"[MQTT Sensor] alert -> {topic}")
