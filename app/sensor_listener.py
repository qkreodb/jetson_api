import socket
import json
import time
import threading
import logging
from pymodbus.client import ModbusTcpClient

from rich import print

class SensorDataCollector:
    def __init__(self, safety_core):
        self.safety_core = safety_core
        self.running = True

    def start(self):
        threading.Thread(target=self._vital_listener, daemon=True).start()
        threading.Thread(target=self._th_listener, daemon=True).start()
        #logging.info("🎧 [Sensor_Collector] 내부 센서 수집 모듈 가동 완료! (디버깅 모드)")

    def _vital_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('0.0.0.0', 5005))
        
        while self.running:
            try:
                data, addr = sock.recvfrom(1024)
                payload_str = data.decode('utf-8').strip()
                
                json_start = payload_str.find('{')
                if json_start != -1:
                	try:
                		parsed = json.loads(payload_str[json_start:])
                		
                		heart_rate = parsed.get('hr')
                		#print(f"[light][워치] 심박수: {heart_rate}[/light]")
                	except json.JSONDecodeError:
                		print("JSON parsing failed")

                json_start = payload_str.find('{')
                if json_start == -1: 
                    print("⚠️ [Vital 경고] JSON 형식이 아닙니다.")
                    continue
                
                parsed = json.loads(payload_str[json_start:])
                # 공백 16칸(4단위)으로 정확히 맞춤
                payload = {
                    "sen_id": 1386,
                    "sen_name": "wat1",
                    "hr": float(parsed.get("hr", 0)),
                    "time": time.time()
                }

                if self.safety_core:
                    self.safety_core.receive_sensor_data("TYPE_VITAL", payload)

            except Exception as e:
                logging.error(f"[Vital 수신 에러] {e}")

    def _th_listener(self):
        MODBUS_IP = "192.168.0.20" 
        MODBUS_PORT = 502

        while self.running:
            try:
                client = ModbusTcpClient(MODBUS_IP, port=MODBUS_PORT, timeout=2)
                if client.connect():
                    result = client.read_input_registers(address=0, count=2, device_id=1)
                    
                    if not result.isError():
                        temp = result.registers[0] / 10.0
                        humid = result.registers[1] / 10.0
                        #print(f"[light][온습도] 온도: {temp}°C, 습도: {humid}%[/light]")
                        
                        payload = {
                            "sen_id": 201,
                            "sen_name": "hum1",
                            "temp": temp,
                            "humid": humid,
                            "time": time.time()
                        }
            
                        # 🌟 이 부분의 들여쓰기를 위의 payload와 똑같이 맞췄습니다.
                        if self.safety_core:
                            self.safety_core.receive_sensor_data("TYPE_TH", payload)

                    client.close()
                else:
                    print(f"❌ [TH 에러] {MODBUS_IP} 연결 실패")
            except Exception as e:
                print(f"🚨 [TH 치명적 에러] {e}") 
                
            time.sleep(5)
