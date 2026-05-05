from typing import List
from fastapi import APIRouter, HTTPException, Response, status, BackgroundTasks, WebSocket, WebSocketDisconnect, Request
from pydantic import BaseModel
from datetime import datetime

# 🌟 SQLAlchemy (models, crud, database) 의존성 완전 제거!
from app import schemas
from app.db.db_handler import DatabaseHandler  # 통합된 만능 DB 핸들러

router = APIRouter(prefix="/api", tags=["API 호출 모듈"])

# DB 모듈 전역 연결
db_module = DatabaseHandler(host='127.0.0.1', user='myuser', password='mypassword', db_name='mydb')


class AppCameraReq(BaseModel):
    ip_address: str
    camera_id: str
    camera_pw: str
    
    
# 조치 사항 입력용 Pydantic 모델
class EventMeasuresReq(BaseModel):
    event_id: int
    measures: str

class SensorUnregisterReq(BaseModel):
	sensor_id: str

@router.post("/jetson/register", response_model=schemas.JetsonRegisterRes, summary="젯슨 등록 및 앱 연동")
def register_jetson(req: schemas.JetsonRegisterReq):
    # crud.register_jetson_connection 완전 대체
    jetson = db_module.register_jetson_connection(req.dept_id, req.app_id)

    if not jetson:
        raise HTTPException(status_code=404, detail="DB에 젯슨 초기 정보가 없습니다.")

    return schemas.JetsonRegisterRes(
        jetson_id=f"jetson-{jetson['jetson_id']:02d}",
        register_status="success",
        api_base_url=f"http://{jetson['ip_addr']}:{jetson['port']}",
        ws_url=f"ws://{jetson['ip_addr']}:{jetson['port']}/ws/alerts"
    )


# DY
@router.get("/sensors/discovered", summary="mDNS 감지 센서 목록 조회")
def get_discovered_sensors(request: Request):
	try:
		sensors = request.app.state.mdns_sensor_service.get_discovered_sensors()
		return {
			"status" : "success",
			"data": sensors
		}
	except Exception as e:
		raise HTTPException(status_code = 500, detail = f"failed: {e}")
	
@router.get("/sensors", summary="등록된 센서 목록 조회")
def get_sensors():
	try:
		sensor_list = db_module.get_registered_sensor_rows()
		return{
			"status" : "success",
			"data" : sensor_list
		}
	except Exception as e:
		raise HTTPException(status_code = 500, detail=f"failed: {e}")


@router.post("/sensors/register", summary="센서 다중 등록")
def register_sensors(req: schemas.SensorRegisterReq, request: Request):
    """
    방법 A
    - 앱이 선택한 발견 센서를 DB에 INSERT
    - MQTT register 명령 전송
    
    """
    try:
        jetson_id = int(req.jetson_id.split("-")[1])
    except Exception:
        jetson_id = 1

    if not req.selected_sensors:
        raise HTTPException(status_code=400, detail="선택된 센서가 없습니다.")

    selected = []
    for s in req.selected_sensors:
        s_dict = s.model_dump() if hasattr(s, "model_dump") else s

        sensor_id = s_dict.get("sensor_id")
        if not sensor_id:
            raise HTTPException(status_code=400, detail="sensor_id가 없는 센서가 포함되어 있습니다.")
            print(req)

        selected.append({
            "sensor_id": sensor_id,
            "sensor_type": s_dict.get("sensor_type"),
            "sen_name": s_dict.get("sen_name"),
            "sen_locate": s_dict.get("sen_locate"),
            "model": s_dict.get("model", "unknown"),
            "mqtt_topic": s_dict.get("mqtt_topic"),
            "mdns_hostname": s_dict.get("mdns_hostname"),
            "ip_addr": s_dict.get("ip_addr"),
            "last_seen_at": s_dict.get("last_seen_at") or datetime.now()
        })

    ok = db_module.register_discovered_sensors(jetson_id, selected)
    if not ok:
        raise HTTPException(status_code=500, detail="센서 DB 등록 실패")

    mqtt_service = request.app.state.mqtt_sensor_service

    for sensor in selected:
        mqtt_service.publish_register(
            sensor_id=sensor["sensor_id"],
            site_id=f"jetson-{jetson_id:02d}",
            interval_ms=5000
        )

    return {
        "status": "success",
        "message": "Sensors registered successfully"
    }


@router.post("/sensors/unregister", summary="센서 등록 해제")
def unregister_sensor(req: SensorUnregisterReq, request: Request):
    """
    방법 A
    - MQTT unregister 전송
    - DB에서 sensor row 삭제
    """
    mqtt_service = request.app.state.mqtt_sensor_service

    mqtt_service.publish_unregister(req.sensor_id)

    ok = db_module.unregister_sensor_by_sensor_id(req.sensor_id)
    if not ok:
        raise HTTPException(status_code=404, detail="해당 sensor_id를 가진 등록 센서가 없습니다.")

    return {
        "status": "success",
        "message": "Sensor unregistered successfully"
    }
    
# DY_db 구조 변경에 따른 api 수정 및 센서 등록 해제 api 추가


@router.post("/cameras/register", summary="카메라 등록 및 VLM 중계")
def register_camera(req: AppCameraReq):
    # crud.register_camera_info 대체
    camera_info = db_module.register_camera_info(req.ip_address, req.camera_id, req.camera_pw)

    if camera_info is None:
        raise HTTPException(status_code=400, detail="이미 등록된 카메라입니다.")
    elif camera_info is False:
        raise HTTPException(status_code=404, detail="젯슨 정보가 DB에 없습니다.")

    vlm_payload = {
        "ip_address": camera_info['ip_address'],
        "camera_id": camera_info['camera_id'],
        "camera_pw": camera_info['camera_pw'],
        "rtsp_port": 554,
        "rtsp_path": "/stream1"
    }
    print(f"📡 [VLM 서버로 전송됨 (Mock)]: {vlm_payload}")
    return {"status": "success", "message": "카메라가 성공적으로 등록되었습니다."}


@router.get("/cameras", summary="CCTV 목록 조회")
def get_cameras():
    cctv_list = db_module.get_cctv_list()
    result_data = []
    for cctv in cctv_list:
        result_data.append({
            "ip_address": cctv['ip_address'],
            "sen_name": cctv['sen_name'],
            "sen_locate": cctv['sen_locate'],
            "health": 1  # 🚨 DB에 health 컬럼이 없으므로 프론트엔드 호환성을 위해 더미값 1 고정 삽입
        })
    return {"status": "success", "data": result_data}




@router.get("/web/sensors/th", summary="th sensor")
def get_sensors():
    sensor_list = db_module.get_web_sensor_th()
    return {"status": "success", "data": sensor_list}
@router.get("/web/sensors/hb", summary="hb sensor")
def get_sensors():
    sensor_list = db_module.get_web_sensor_hb()
    return {"status": "success", "data": sensor_list}
    
@router.post("/internal/vlm-analysis", summary="위험 감지 데이터 안전 감지 모듈로 전달")
async def receive_vlm_analysis(req: schemas.VlmAnalysisReq,request: Request, background_tasks: BackgroundTasks):
    print(f"📡 [API 모듈] VLM 데이터 수신 및 내부 전달: {req.ip_address}")
    
    safety_core = request.app.state.safety_core
   
    if safety_core:
    	vlm_payload = req.model_dump()
    	
    	background_tasks.add_task(safety_core.receive_ai_event, vlm_payload)
    return Response(status_code=status.HTTP_200_OK)
    
    
   
# 1. 조치 사항 기록 API
@router.post("/event/measures", summary="사건 조치 사항 기록")
async def post_event_measures(req: EventMeasuresReq):
    success = db_module.update_event_measures(req.event_id, req.measures)
    if not success:
        raise HTTPException(status_code=400, detail="조치 사항 기록에 실패했습니다. ID를 확인하세요.")
    return {"status": "success", "message": "조치 사항이 성공적으로 기록되었습니다."}

# 2. 사번으로 이름 조회 API
@router.get("/worker", summary="사번으로 작업자 이름 조회")
async def get_worker_name(worker_id: str):
    name = db_module.get_worker_name_by_id(worker_id)
    if not name:
        raise HTTPException(status_code=404, detail="해당 사번을 가진 작업자가 없습니다.")
    return {"status": "success", "worker_name": name}
    


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            


    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass


manager = ConnectionManager()
