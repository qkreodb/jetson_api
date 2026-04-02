from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime, date

# --- 1. 젯슨 등록 관련 (/api/jetson/register) ---
class JetsonRegisterReq(BaseModel):
    dept_id: int   # 로그인 한 사번
    app_id: str    # 앱 이름

class JetsonRegisterRes(BaseModel):
    jetson_id: str          # 예: "jetson-01" 
    register_status: str    # "success"
    api_base_url: str       # "http://192.168.0.10:8000"
    ws_url: str             # "ws://192.168.0.10:8000/ws/alerts"

# --- 2. 센서 조회 및 등록 관련 (/api/sensors/...) ---
class SensorItem(BaseModel):
    sen_name: str
    sensor_type: str
    mqtt_topic: str
    sen_locate: str

class DiscoveredSensorsRes(BaseModel):
    jetson_id: str
    discovered_sensors: List[SensorItem]

class SensorRegisterReq(BaseModel):
    jetson_id: str
    selected_sensors: List[SensorItem]

# --- 3. 카메라 등록 관련 (/api/cameras/...) ---
# 1단계: 카메라 센서 등록 (DB에서 sen_id를 발급받기 위함)
class CameraSensorReq(BaseModel):
    sensor_type: str = "camera"
    sen_name: str
    sen_locate: str

# 2단계: 카메라 정보 등록 (발급받은 sen_id와 함께 저장)
class CameraInfoReq(BaseModel):
    sen_id: int
    ip_address: str
    camera_id: str
    camera_pw: str
    rtsp_port: int = 554       # DB에는 없지만 통신 시 받아서 사용
    rtsp_path: str = "/stream1" # DB에는 없지만 통신 시 받아서 사용

# --- 4. VLM AI 모듈 통신 관련 (/api/internal/vlm-analysis) ---
class VlmAnalysisReq(BaseModel):
    ip_address: str # 예: "cam-01"
    ev_code_name : str
    risk_text: str   # 예: "1구역 cam-01에서 낙상 위험이 감지되었습니다."
    time: str        

# --- 5. 웹소켓 Push 알림 규격 ---
class WsAlertPayload(BaseModel):
    type: str = "danger_alert"
    event_id: int
    event_code: str  
    message: str

# --- 4. VLM AI 모듈 통신 관련 (/api/internal/vlm-analysis) ---
class VlmAnalysisReq(BaseModel):
    camera_name: str 
    ev_code_name: str  # 🌟 이 줄 추가! (ex: "FALL_DETECTED")
    risk_text: str   
    time: str
