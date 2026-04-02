import pymysql
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class DatabaseHandler:
    def __init__(self, host='127.0.0.1', user='root', password='ekthf123', db_name='ds_db', port=3306):
        """MariaDB 연결 초기화"""
        self.db_config = {
            'host': host,
            'user': user,
            'password': password,
            'database': db_name,
            'port': port,
            'charset': 'utf8mb4',
            'cursorclass': pymysql.cursors.DictCursor,
            'autocommit': True  # 자동 커밋 활성화 (INSERT 즉시 반영)
        }

    def _get_connection(self):
        """요청 시마다 DB 커넥션 생성 (연결 끊김 방지)"""
        return pymysql.connect(**self.db_config)

    def _parse_to_mysql_time(self, time_val):
        """ISO 8601 문자열이나 Unix Timestamp를 MySQL DATETIME(YYYY-MM-DD HH:MM:SS) 형식으로 변환"""
        try:
            if isinstance(time_val, (int, float)):
                dt = datetime.fromtimestamp(time_val)
            else:
                dt = datetime.fromisoformat(time_val)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # ==========================================
    # 1. 구독 목록 추출 (코어 엔진 -> DB)
    # ==========================================
    def get_registered_sensors(self):
        """sensor 테이블에서 유효한 센서 이름과 MQTT 토픽을 가져옵니다."""
        query = "SELECT sen_name, mqtt_topic, sensor_type FROM sensor"
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query)
                    return cursor.fetchall()
        except Exception as e:
            logging.error(f"DB 구독 목록 조회 실패: {e}")
            return []

    # ==========================================
    # 2. 센서 원본 데이터 저장 (코어 엔진 -> DB)
    # ==========================================
    def save_raw_data(self, data_type, payload):
        """온습도(th_trans) 및 심박수(hb_trans) 테이블에 원본 데이터 INSERT"""
        sen_name = payload.get("sen_name")
        mysql_time = self._parse_to_mysql_time(payload.get("time"))

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    if data_type == "TYPE_TH":
                        query = """
                            INSERT INTO th_trans (sen_id, temp, humid, time)
                            VALUES (
                                (SELECT sen_id FROM sensor WHERE sen_name = %s LIMIT 1),
                                %s, %s, %s
                            )
                        """
                        cursor.execute(query, (sen_name, payload.get('temp'), payload.get('humid'), mysql_time))

                    elif data_type == "TYPE_VITAL":
                        query = """
                            INSERT INTO hb_trans (sen_id, hr, time)
                            VALUES (
                                (SELECT sen_id FROM sensor WHERE sen_name = %s LIMIT 1),
                                %s, %s
                            )
                        """
                        cursor.execute(query, (sen_name, payload.get('hr'), mysql_time))
        except Exception as e:
            logging.error(f"DB 원본 데이터 저장 실패 ({data_type}): {e}")

    # ==========================================
    # 3. 룰 엔진 위험 이벤트 저장 (코어 엔진 -> DB)
    # ==========================================
    def save_event_log(self, db_payload):
        """사건 터졌을 때 event 테이블에 저장하고, 생성된 event_id 반환"""
        query = """
            INSERT INTO event (ev_code_id, sen_id, message, detected_value, time)
            VALUES (
                %s,
                (SELECT sen_id FROM sensor WHERE sen_name = %s LIMIT 1),
                %s, %s, %s
            )
        """
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (
                        db_payload['ev_code_id'],
                        db_payload['sen_name'],
                        db_payload['message'],
                        db_payload['detected_value'],
                        db_payload['time']
                    ))
                    event_id = cursor.lastrowid
                    return {"event_id": event_id}
        except Exception as e:
            logging.error(f"DB 이벤트 로깅 실패: {e}")
            return {"event_id": int(datetime.now().timestamp() * 1000)}

    # ==========================================
    # 4. AI 카메라 이벤트 처리 (코어 엔진 -> DB)
    # ==========================================
    def process_ai_event(self, req_payload):
        """
        카메라 위험 이벤트를 저장하고, 카메라 구역에 있는 밴드의 sen_name과 event_id를 찾아 반환
        """
        camera_name = req_payload['camera_name']
        ev_code_name = req_payload['ev_code_name']
        mysql_time = self._parse_to_mysql_time(req_payload['time'])

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    # 1. 카메라 ID를 통해 연동된 밴드(sen_id, sen_name) 찾기
                    find_sensor_query = """
                        SELECT s.sen_id, s.sen_name
                        FROM sensor s
                        JOIN camera_info c ON s.sen_id = c.sen_id
                        WHERE c.camera_id = %s LIMIT 1
                    """
                    cursor.execute(find_sensor_query, (camera_name,))
                    sensor_info = cursor.fetchone()

                    target_sen_id = sensor_info['sen_id'] if sensor_info else None
                    target_sen_name = sensor_info['sen_name'] if sensor_info else "unknown_band"

                    # 2. 이벤트 코드명으로 ID 찾기
                    find_code_query = "SELECT ev_code_id FROM event_code WHERE ev_code_name = %s LIMIT 1"
                    cursor.execute(find_code_query, (ev_code_name,))
                    code_info = cursor.fetchone()
                    ev_code_id = code_info['ev_code_id'] if code_info else 0

                    # 3. event 테이블에 INSERT
                    insert_query = """
                        INSERT INTO event (ev_code_id, sen_id, message, detected_value, time)
                        VALUES (%s, %s, %s, 'AI_VISION_DETECTION', %s)
                    """
                    cursor.execute(insert_query, (
                        ev_code_id,
                        target_sen_id,
                        req_payload['message'],
                        mysql_time
                    ))

                    event_id = cursor.lastrowid

                    return {
                        "event_id": event_id,
                        "sen_name": target_sen_name
                    }
        except Exception as e:
            logging.error(f"DB AI 이벤트 처리 실패: {e}")
            return {"event_id": int(datetime.now().timestamp() * 1000), "sen_name": "unknown_band"}

    # ==========================================
    # 5. 센서 등록 (API 모듈 -> DB -> 코어 엔진 갱신)
    # ==========================================
    def register_sensors(self, api_payload):
        """
        [호출처] API 모듈 (POST /api/sensors/register)
        전달받은 JSON 페이로드를 파싱하여 sensor 테이블에 등록합니다.
        """
        sensors = api_payload.get("selected_sensors", [])

        # 등록 시점의 젯슨 기준 시간 생성 (MySQL의 DATE/DATETIME 타입에 호환되는 포맷)
        register_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    for s in sensors:
                        # 🎯 단일 젯슨 환경이므로, 젯슨 ID는 DB에 등록된 첫 번째 젯슨을 가리키도록 서브쿼리 사용
                        # 만약 API에서 넘어오는 "jetson-01"을 파싱해서 쓰고 싶다면 별도 처리 가능
                        query = """
                            INSERT INTO sensor (
                                jetson_id, sensor_type, sen_name, sen_locate, mqtt_topic, register_date
                            )
                            VALUES (
                                (SELECT jetson_id FROM jetson LIMIT 1),
                                %s, %s, %s, %s, %s
                            )
                        """
                        cursor.execute(query, (
                            s.get("sensor_type"),
                            s.get("sen_name"),
                            s.get("sen_locate"),
                            s.get("mqtt_topic"),
                            register_date
                        ))

            logging.info(f"✅ DB 센서 등록 완료: 총 {len(sensors)}개 센서가 추가되었습니다.")
            return True

        except Exception as e:
            logging.error(f"❌ DB 센서 등록 실패: {e}")
            return False

        # ==========================================
    # 5. API 직접 호출용 함수 (SQLAlchemy 완벽 대체)
    # ==========================================
    def create_jetson(self, jetson_data: dict):
        """1. 젯슨 장비 등록 (AUTO_INCREMENT 적용)"""
        # 🎯 jetson_id는 DB가 알아서 만들도록 쿼리에서 뺐습니다.
        query = """
            INSERT INTO jetson (jetson_wp, jetson_loc, jetson_status, ip_addr, port)
            VALUES (%s, %s, %s, %s, %s)
        """
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (
                        jetson_data.get('jetson_wp'),
                        jetson_data.get('jetson_loc'),
                        jetson_data.get('jetson_status', False),
                        jetson_data.get('ip_addr'),
                        jetson_data.get('port')
                    ))
                    # 🪄 DB가 방금 뱉어낸 따끈따끈한 jetson_id 줍기 (db.refresh 효과!)
                    new_id = cursor.lastrowid

            # API로 돌려줄 딕셔너리에 새 ID 꽂아주기
            jetson_data['jetson_id'] = new_id
            return jetson_data

        except Exception as e:
            logging.error(f"create_jetson 에러: {e}")
            raise e

    def create_sensor(self, sensor_data: dict):
        """2. 센서 등록 (AUTO_INCREMENT 적용)"""
        # 🎯 sen_id 빼고 INSERT!
        query = """
            INSERT INTO sensor (jetson_id, sensor_type, sen_name, sen_locate, mqtt_topic, register_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        try:
            reg_date = sensor_data.get("register_date") or datetime.now().strftime('%Y-%m-%d')
            sensor_data['register_date'] = reg_date

            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (
                        sensor_data.get('jetson_id'),
                        sensor_data.get('sensor_type'),
                        sensor_data.get('sen_name'),
                        sensor_data.get('sen_locate'),
                        sensor_data.get('mqtt_topic'),
                        reg_date
                    ))
                    # 🪄 방금 발급된 sen_id 줍기
                    new_id = cursor.lastrowid

            sensor_data['sen_id'] = new_id
            return sensor_data

        except Exception as e:
            logging.error(f"create_sensor 에러: {e}")
            raise e

    def get_sensors_by_jetson(self, jetson_id: int):
        """3. 특정 젯슨에 연결된 센서 목록 조회"""
        query = "SELECT * FROM sensor WHERE jetson_id = %s"
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (jetson_id,))
                    return cursor.fetchall()
        except Exception as e:
            logging.error(f"get_sensors_by_jetson 에러: {e}")
            return []

    def create_camera(self, cam_data: dict):
        """4. 카메라 등록 (AUTO_INCREMENT 연결의 정수)"""
        query_sensor = """
            INSERT INTO sensor (jetson_id, sensor_type, sen_name, sen_locate, register_date)
            VALUES (%s, %s, %s, %s, %s)
        """
        query_camera = """
            INSERT INTO camera_info (sen_id, ip_address, camera_id, camera_pw)
            VALUES (%s, %s, %s, %s)
        """
        try:
            reg_date = cam_data.get("register_date") or datetime.now().strftime('%Y-%m-%d')

            with self._get_connection() as conn:
                conn.begin() # 트랜잭션 개시
                with conn.cursor() as cursor:
                    # (1) sensor 테이블에 먼저 넣기 (sen_id는 DB가 알아서 만듦)
                    cursor.execute(query_sensor, (
                        cam_data.get('jetson_id'),
                        cam_data.get('sensor_type', 'camera'),
                        cam_data.get('sen_name'),
                        cam_data.get('sen_locate'),
                        reg_date
                    ))

                    # 🪄 (2) 방금 생성된 sen_id를 낚아채서!
                    new_sen_id = cursor.lastrowid

                    # (3) camera_info 테이블의 외래키(sen_id)로 바로 써먹기!
                    cursor.execute(query_camera, (
                        new_sen_id,
                        cam_data.get('ip_address'),
                        cam_data.get('camera_id'),
                        cam_data.get('camera_pw')
                    ))
                conn.commit()

            # 최종 리턴 데이터에 발급된 ID 추가해서 뱉어줌
            cam_data['sen_id'] = new_sen_id
            cam_data['register_date'] = reg_date
            return cam_data

        except Exception as e:
            logging.error(f"create_camera 에러: {e}")
            raise e

        # ==========================================
    # 🌟 API 완벽 호환용 통합 CRUD 모듈 (SQLAlchemy 대체)
    # ==========================================

    # [1단계] 젯슨 장비 및 앱 연결 로직
    def init_jetson_info(self, jetson_data: dict):
        """서버 부팅 시 젯슨 초기 정보 세팅 (있으면 IP/PORT만 업데이트)"""
        check_query = "SELECT jetson_id FROM jetson LIMIT 1"
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(check_query)
                    existing = cursor.fetchone()

                    if not existing:
                        # 없으면 새로 삽입
                        insert_query = """
                            INSERT INTO jetson (jetson_wp, jetson_loc, situ_state, ip_addr, port)
                            VALUES (%s, %s, %s, %s, %s)
                        """
                        cursor.execute(insert_query, (
                            jetson_data.get('jetson_wp'), jetson_data.get('jetson_loc'),
                            jetson_data.get('jetson_status', True),
                            jetson_data.get('ip_addr'), jetson_data.get('port')
                        ))
                    else:
                        # 있으면 IP와 PORT만 업데이트
                        update_query = "UPDATE jetson SET ip_addr = %s, port = %s WHERE jetson_id = %s"
                        cursor.execute(update_query, (
                            jetson_data.get('ip_addr'), jetson_data.get('port'), existing['jetson_id']
                        ))
            return True
        except Exception as e:
            logging.error(f"init_jetson_info 에러: {e}")
            return False

    def register_jetson_connection(self, dept_id: int, app_id: str):
        """앱 연동 시 connect 테이블에 기록하고 젯슨 정보 반환"""
        get_jetson_query = "SELECT * FROM jetson LIMIT 1"
        insert_connect_query = "INSERT INTO connect (dept_id, jetson_id, app_id) VALUES (%s, %s, %s)"
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(get_jetson_query)
                    jetson = cursor.fetchone()

                    if not jetson:
                        return None

                    cursor.execute(insert_connect_query, (dept_id, jetson['jetson_id'], app_id))
            return jetson
        except Exception as e:
            logging.error(f"register_jetson_connection 에러: {e}")
            return None

    # [2단계] 센서 다중 등록 로직
    def register_multiple_sensors(self, jetson_id: int, sensors: list):
        """배열로 들어온 센서들을 한방에 DB에 저장"""
        query = """
            INSERT INTO sensor (jetson_id, sensor_type, sen_name, sen_locate, mqtt_topic, register_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        reg_date = datetime.now().strftime('%Y-%m-%d')
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    for s in sensors:
                        # Pydantic 모델일 경우 dict로 변환해서 사용, 이미 dict면 그냥 사용
                        s_dict = s.model_dump() if hasattr(s, 'model_dump') else s
                        cursor.execute(query, (
                            jetson_id,
                            s_dict.get('sensor_type'),
                            s_dict.get('sen_name'),
                            s_dict.get('sen_locate'),
                            s_dict.get('mqtt_topic'),
                            reg_date
                        ))
            return True
        except Exception as e:
            logging.error(f"register_multiple_sensors 에러: {e}")
            return False

    # [3단계] 카메라 2단계 등록 및 자동 이름 생성 로직
    def register_camera_info(self, ip_address: str, camera_id: str, camera_pw: str):
        """중복 검사 후 센서와 카메라 테이블에 자동 생성 데이터와 함께 삽입"""
        check_query = "SELECT 1 FROM camera_info WHERE ip_address = %s LIMIT 1"
        get_jetson_query = "SELECT jetson_id, jetson_loc FROM jetson LIMIT 1"

        insert_sensor_query = """
            INSERT INTO sensor (jetson_id, sensor_type, sen_name, sen_locate, register_date)
            VALUES (%s, %s, %s, %s, %s)
        """
        insert_camera_query = """
            INSERT INTO camera_info (sen_id, ip_address, camera_id, camera_pw)
            VALUES (%s, %s, %s, %s)
        """
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    # 1. 중복 IP 검사
                    cursor.execute(check_query, (ip_address,))
                    if cursor.fetchone():
                        return None # 이미 등록됨

                    # 2. 젯슨 정보 가져오기
                    cursor.execute(get_jetson_query)
                    jetson = cursor.fetchone()
                    if not jetson:
                        return False # 젯슨 없음

                    conn.begin() # 트랜잭션 시작

                    # 3. 센서 테이블에 삽입 (이름, 위치 자동 생성)
                    auto_name = f"CAM_{ip_address.split('.')[-1]}"
                    reg_date = datetime.now().strftime('%Y-%m-%d')

                    cursor.execute(insert_sensor_query, (
                        jetson['jetson_id'], "camera", auto_name, jetson['jetson_loc'], reg_date
                    ))
                    new_sen_id = cursor.lastrowid

                    # 4. 카메라 테이블에 삽입
                    cursor.execute(insert_camera_query, (new_sen_id, ip_address, camera_id, camera_pw))
                conn.commit()

            return {"ip_address": ip_address, "camera_id": camera_id, "camera_pw": camera_pw}
        except Exception as e:
            logging.error(f"register_camera_info 에러: {e}")
            return False

    # [조회] 카메라 / 일반 센서 목록
    def get_cctv_list(self):
        query = """
            SELECT c.ip_address, s.sen_name, s.sen_locate
            FROM camera_info c
            JOIN sensor s ON c.sen_id = s.sen_id
        """
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query)
                    return cursor.fetchall()
        except Exception as e:
            return []

    def get_sensor_list(self):
        query = "SELECT sen_name, sensor_type, sen_locate FROM sensor WHERE sensor_type != 'camera'"
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query)
                    return cursor.fetchall()
        except Exception as e:
            return []
~