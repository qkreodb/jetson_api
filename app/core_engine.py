import time
import math
import threading
import copy
import logging
from datetime import datetime

from rich import print

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# 프로젝트 상수
LAW_WINDOW_SAMPLES = 5       # 🚨 테스트 빨리 보려고 5번으로 줄였습니다! (원래대로 하려면 10 등 수정)
HI_THRESHOLD = 33.0
HS_STREAK_SAMPLES = 36
HS_HR_DELTA = 30.0
TIMEOUT_WORKING_DOWN_SEC = 30.0
TIMEOUT_RESET_SEC = 600.0

EVENT_CODE_MAP = {
    "LAW_REST": 1,          # 법정 휴식
    "EMERGENCY_REST": 3,    # 열 스트레스 위험
    "FALL_DETECTED": 4,     # 낙상 감지
    "FIRE_DETECTED": 5,      # 화재 감지
    "SMOKE_DETECTED" : 6,   #yeongi gam zi
    "RISK_DETECTED" : 7    #risk gam zi
}

STATE_REST_START = "LAW_REST"
STATE_EMERGENCY_REST = "EMERGENCY_REST"
STATE_AI_EMERGENCY = "AI_EMERGENCY"


class SafetyDetectionModule:
    def __init__(self, db_module, transmission_module):
        self.db = db_module
        self.transmission = transmission_module
        self.g_mtx = threading.Lock()

        self.g_env = {'temp': 0.0, 'humid': 0.0, 'time': 0.0}
        self.g_watch = {}
        self.active_names = set()
        self.registered_sensors = set()

        self.timer_thread = threading.Thread(target=self._internal_timer_loop, daemon=True)
        self.timer_thread.start()

    def update_and_get_subscriptions(self):
        if not self.db:
            return []

        sensor_list = self.db.get_registered_sensors()
        mqtt_topics = []

        with self.g_mtx:
            self.registered_sensors.clear()
            for s in sensor_list:
                sen_name = s.get("sen_name")
                topic = s.get("mqtt_topic")

                if sen_name:
                    self.registered_sensors.add(sen_name)
                if topic:
                    mqtt_topics.append(topic)

        logging.info(f"📋 코어 엔진 갱신: {len(self.registered_sensors)}개 센서 감시 시작")
        return mqtt_topics

    def receive_sensor_data(self, data_type, payload: dict):
        try:
            sen_name = payload.get("sen_name")
            msg_time = self._parse_iso_time(payload.get("time"))
            
            if self.transmission:
            	if data_type == "TYPE_VITAL" and hasattr(self.transmission, 'send_vital_data'):
            		self.transmission.send_vital_data(payload)
            	elif data_type == "TYPE_TH" and hasattr(self.transmission, 'send_th_data'):
            		self.transmission.send_th_data(payload)

            if self.db:
                self.db.save_raw_data(data_type, copy.deepcopy(payload))

            if data_type == "TYPE_TH":
                with self.g_mtx:
                    self.g_env['temp'] = payload.get("temp", 0.0)
                    self.g_env['humid'] = payload.get("humid", 0.0)
                    self.g_env['time'] = msg_time

            elif data_type == "TYPE_VITAL":
                if sen_name in self.registered_sensors:
                    self._evaluate_safety_rules(payload, sen_name, msg_time)

        except Exception as e:
            logging.error(f"센서 데이터 처리 오류: {e}")

    def receive_ai_event(self, payload: dict):
        try:
            ip_address = payload.get("ip_address")
            ev_code_name = payload.get("ev_code_name")
            risk_text = payload.get("risk_text", payload.get("message"))
            msg_time = self._parse_iso_time(payload.get("time"))

            db_request_payload = {
                "ip_address": ip_address,
                "ev_code_name": ev_code_name,
                "message": risk_text,
                "time": msg_time
            }

            db_result = {}
            if self.db:
                # 🌟 이제 단일 딕셔너리로 카메라 정보를 돌려받음!
                db_result = self.db.process_ai_event(db_request_payload)

            event_id = db_result.get("event_id", 0)
            camera_name = db_result.get("camera_name", "unknown_camera")  # 🌟 카메라 품명
            camera_loc = db_result.get("camera_loc", "알 수 없는 위치") 
            # 🌟 원본 시간 가져오기 (앱에서 파싱하기 좋게 원본 문자열 그대로 전송)
            original_time = payload.get("time") 

            # 🌟 관리자 스마트폰 앱으로 직통 전파!
            api_payload = {
                "event_id": event_id,
                "target_topic": f"app/{camera_name}/alerts",  # 👈 관리자 공통 구독 토픽
                "type": "app_alert",
                "alert": True,  # 어떤 카메라에서 발생했는지 전달
                "camera_name": camera_name,
                "camera_loc": camera_loc,       # 👈 추가: 카메라 위치 (예: "제1공장")
                "ev_code_name": ev_code_name,   # 👈 추가: 이벤트 코드 (예: "FALL_DOWN")
                "event_time": original_time,    # 👈 추가: 발생 시간 (예: "20260403013000")
                "message": risk_text,
                "color": "red",
                "vibration": True,
                "led": True,
                "duration_ms": 5000,
                "reset_after_ms": 15000
            }

            if self.transmission:
                self.transmission.send_push_notification(api_payload)

            logging.info(f"🚨 [관리자 호출] {ev_code_name} 발생! -> 앱 알람 전송 (발생지: {camera_name})")

        except Exception as e:
            logging.error(f"AI 이벤트 처리 오류: {e}")

    def _evaluate_safety_rules(self, wd, sen_name, msg_time):
        trigger_rest = False
        trigger_emergency = False
        current_hr = wd.get("hr", 0)

        with self.g_mtx:
            if sen_name not in self.g_watch:
                self.g_watch[sen_name] = {
                    'working': 0, 'baseline_hr': 0.0,
                    'count_hi': 0, 'sum_hi': 0.0, 'hs_streak': 0,
                    'last_seen_sec': 0.0, 'last_rx_time': 0.0
                }

            ws = self.g_watch[sen_name]
            ws['last_seen_sec'] = msg_time
            ws['last_rx_time'] = time.time()
            self.active_names.add(sen_name)

            if ws['working'] == 0:
                ws.update({'working': 1, 'baseline_hr': current_hr, 'count_hi': 0, 'sum_hi': 0.0, 'hs_streak': 0})

            temp, humid = self.g_env['temp'], self.g_env['humid']
            if temp == 0.0 and humid == 0.0: return

            hi = self._calc_heat_index(temp, humid)

            # ==========================================
            #   규칙 1: 법정 휴식 (로그 추가)
            # ==========================================
            ws['sum_hi'] += hi
            ws['count_hi'] += 1

            print(f"[법정 휴식 틱] {sen_name} 샘플 수집: {ws['count_hi']}/{LAW_WINDOW_SAMPLES} (HI: {hi})")

            if ws['count_hi'] >= LAW_WINDOW_SAMPLES:
                avg_hi = ws['sum_hi'] / LAW_WINDOW_SAMPLES
                print(f"[리셋] {sen_name} 평균 HI: {avg_hi:.2f} (기준: {HI_THRESHOLD})")

                if avg_hi >= HI_THRESHOLD:
                    trigger_rest = True
                    logging.info("📢 [판정] 평균 열지수 초과! 법정 휴식 발동!")

                ws['sum_hi'], ws['count_hi'] = 0.0, 0

            # 규칙 2: 열 스트레스
            if current_hr >= ws['baseline_hr'] + HS_HR_DELTA and hi >= HI_THRESHOLD:
                ws['hs_streak'] += 1
                if ws['hs_streak'] >= HS_STREAK_SAMPLES: trigger_emergency = True
            else:
                ws['hs_streak'] = 0

            if trigger_rest or trigger_emergency:
                ws['working'], ws['hs_streak'] = 0, 0
                state_code = STATE_EMERGENCY_REST if trigger_emergency else STATE_REST_START

        # DB 저장 및 알람 발송 호출
        if trigger_rest or trigger_emergency:
            self._trigger_event(sen_name, state_code, current_hr, hi, msg_time)

    def _trigger_event(self, sen_name, state_code, hr_val, hi_val, msg_time, custom_msg=None):
        if custom_msg:
            message_desc = custom_msg
        elif state_code == STATE_EMERGENCY_REST:
            message_desc = "열 스트레스 위험! 즉시 휴식하세요."
        else:
            message_desc = "법정 휴식 시간입니다."

        dt_string = datetime.fromtimestamp(msg_time).strftime('%Y-%m-%d %H:%M:%S')

        with self.g_mtx:
            curr_temp = self.g_env.get('temp', 0.0)
            curr_humid = self.g_env.get('humid', 0.0)

        integrated_values = f"T:{curr_temp:.1f}, H:{curr_humid:.1f}, HR:{hr_val:.1f}, HI:{hi_val:.2f}"

        db_payload = {
            "ev_code_id": EVENT_CODE_MAP.get(state_code, 0),
            "sen_name": sen_name,
            "message": message_desc,
            "detected_value": integrated_values,
            "time": dt_string
        }

        db_result = {}
        if self.db:
            db_result = self.db.save_event_log(db_payload)

        generated_event_id = db_result.get("event_id", int(time.time()))

        api_payload = {
            "event_id": generated_event_id,
            "target_topic": f"sensor/{sen_name}/control",
            "type": "sensor_alert",
            "alert": True,
            "message": message_desc,
            "color": "red" if state_code == STATE_EMERGENCY_REST else "yellow",
            "vibration": True,
            "led": True,
            "duration_ms": 5000 if state_code == STATE_EMERGENCY_REST else 3000,
            "reset_after_ms": 15000
        }

        if self.transmission:
            self.transmission.send_push_notification(api_payload)

        logging.info(f"🚨 [사건 발생] {state_code} | 값: {integrated_values} | 대상: {sen_name}")

    def _parse_iso_time(self, time_str):
        try:
            if time_str:
                return datetime.fromisoformat(time_str).timestamp()
        except (ValueError, TypeError):
            pass
        return time.time()

    def _internal_timer_loop(self):
        while True:
            time.sleep(5)
            self._check_timeouts()

    def _check_timeouts(self):
        now = time.time()
        with self.g_mtx:
            for sen_name in list(self.active_names):
                ws = self.g_watch.get(sen_name)

                if not ws:
                    self.active_names.discard(sen_name)
                    continue

                gap = now - ws['last_rx_time']

                if gap >= TIMEOUT_RESET_SEC:
                    del self.g_watch[sen_name]
                    self.active_names.discard(sen_name)
                    logging.info(f"[TIMEOUT] {sen_name} 600초 무응답. 상태 완전 초기화.")

                elif gap >= TIMEOUT_WORKING_DOWN_SEC and ws['working'] == 1:
                    ws['working'] = 0
                    ws['hs_streak'] = 0
                    logging.info(f"[TIMEOUT] {sen_name} 30초 무응답. 근무 상태 해제 (누적값 유지).")

    def _calc_heat_index(self, ta, rh):
        t1 = ta * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        t2 = math.atan(ta + rh)
        t3 = math.atan(rh - 1.67633)
        t4 = 0.00391838 * (rh ** 1.5) * math.atan(0.023101 * rh)
        tw = t1 + t2 - t3 + t4 - 4.686035
        hi = -0.2442 + (0.55399 * tw) + (0.45535 * ta) - (0.0022 * tw ** 2) + (0.00278 * tw * ta) + 3.0
        return round(hi, 2)

