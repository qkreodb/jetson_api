from typing import List
from fastapi import APIRouter, HTTPException, Response, status, BackgroundTasks, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

# 🌟 SQLAlchemy (models, crud, database) 의존성 완전 제거!
from app import schemas
from app.db.db_handler import DatabaseHandler  # 통합된 만능 DB 핸들러

router = APIRouter(prefix="/api", tags=["API 호출 모듈"])

# DB 모듈 전역 연결
db_module = DatabaseHandler(host='127.0.0.1', user='root', password='your_password', db_name='safety_system')


class AppCameraReq(BaseModel):
    ip_address: str
    camera_id: str
    camera_pw: str


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


@router.get("/sensors/discovered", response_model=schemas.DiscoveredSensorsRes, summary="mDNS 감지 센서 목록 조회")
def get_discovered_sensors():
    dummy_sensors = [
        schemas.SensorItem(sen_name="손목밴드1", sensor_type="heart_rate", mqtt_topic="sensor/band-01/heart_rate",
                           sen_locate="locate1"),
        schemas.SensorItem(sen_name="온습도계1", sensor_type="temperature_humidity", mqtt_topic="sensor/temp-01/data",
                           sen_locate="locate1")
    ]
    return schemas.DiscoveredSensorsRes(jetson_id="jetson-01", discovered_sensors=dummy_sensors)


@router.post("/sensors/register", summary="센서 다중 등록")
def register_sensors(req: schemas.SensorRegisterReq):
    try:
        j_id = int(req.jetson_id.split("-")[1])
    except:
        j_id = 1

        # crud.register_multiple_sensors 대체
    db_module.register_multiple_sensors(j_id, req.selected_sensors)
    return {"status": "success", "message": "Sensors registered successfully"}


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


@router.get("/sensors", summary="등록된 일반 센서 목록 조회")
def get_sensors():
    sensor_list = db_module.get_sensor_list()
    # DictCursor를 쓰기 때문에 바로 json 변환 가능
    return {"status": "success", "data": sensor_list}


@router.post("/internal/vlm-analysis", summary="위험 감지 데이터 안전 감지 모듈로 전달")
async def receive_vlm_analysis(req: schemas.VlmAnalysisReq, background_tasks: BackgroundTasks):
    print(f"📡 [API 모듈] VLM 데이터 수신 및 내부 전달: {req.camera_name}")
    return Response(status_code=status.HTTP_200_OK)


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