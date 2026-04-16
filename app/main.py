import socket
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

# 🌟 DB 핸들러 & 룰 엔진 임포트
from app.db.db_handler import DatabaseHandler
from app.core_engine import SafetyDetectionModule
from app.routers import api_module
from app.routers.api_module import manager
from app.sensor_listener import SensorDataCollector
from app.services.mdns_sensor_service import MdnsSensorService
from app.services.mqtt_sensor_service import MqttSensorService

import asyncio

# 🌟 전역 DB 핸들러 인스턴스 생성 (비밀번호 꼭 본인 걸로 수정하세요!)
db_module = DatabaseHandler(host='127.0.0.1', user='root', password='ekthf123', db_name='ds_db')

# 🌟 전역 룰 엔진 인스턴스 공간
safety_core = None

# new websocket for management(transmission list)
active_vital_websocket: List[WebSocket] = []
active_th_websocket: List[WebSocket] = []

# Real WebSocket
class RealTransmission:
	def __init__(self, loop):
		self.loop = loop
	
	def send_push_notification(self, payload):
		
		print(f"[Real Transmission] alarm to app: {payload.get('message')}")
		
		asyncio.run_coroutine_threadsafe(
		manager.broadcast(payload),
		self.loop
	)
	
	def send_vital_data(self, payload):
		async def broadcast():
			
			for ws in active_vital_websocket:
				try:
					await ws.send_json(payload)
				except Exception:
					pass
		asyncio.run_coroutine_threadsafe(broadcast(), self.loop)
		
	def send_th_data(self, payload):
		async def broadcast():
			for ws in active_th_websocket:
				try:
					await ws.send_json(payload)
				except Exception:
					pass
		asyncio.run_coroutine_threadsafe(broadcast(), self.loop)

def get_real_ip():
    """폐쇄망에서도 현재 할당된 진짜 로컬 IP를 찾아옵니다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        try:
            IP = socket.gethostbyname(socket.gethostname())
        except:
            IP = '127.0.0.1'
    finally:
        s.close()
    return IP


def startup_db_init(ip: str):
    """서버 부팅 시 현재 IP로 젯슨 테이블 정보를 갱신합니다."""
    jetson_info = {
        "jetson_wp": "제1공장",
        "jetson_loc": "컨베이어 벨트 앞",
        "jetson_status": True,
        "ip_addr": ip,
        "port": 8000
    }

    # DB 핸들러로 젯슨 정보 갱신
    success = db_module.init_jetson_info(jetson_info)
    if success:
        print(f"[DB] 젯슨 정보 업데이트 완료 | IP: {ip}")
    else:
        print(f"❌ [DB] 젯슨 정보 업데이트 실패!")


# 전역 mDNS 객체
aiozc = None


# ==========================================
# ⚙️ 여기가 바로 서버의 심장, lifespan 입니다!
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 부팅 시 IP 확인, DB 초기화, 룰 엔진 가동, mDNS 등록을 처리합니다."""
    global aiozc
    current_ip = get_real_ip()

    # 1. DB 초기화 세팅
    startup_db_init(current_ip)
    
    main_loop = asyncio.get_running_loop()
    trans_module = RealTransmission(main_loop)

    # 🌟 2. 룰 엔진(안전 감시 모듈) 백그라운드 가동!
    # trans_module = MockTransmission()
    
    app.state.safety_core = SafetyDetectionModule(db_module, trans_module)
    
    app.state.safety_core.update_and_get_subscriptions()  # DB에서 감시할 센서 목록 빨아오기
    print("[안전감지모듈] 백그라운드 가동 완료")

    sensor_collector = SensorDataCollector(app.state.safety_core)
    sensor_collector.start()
    
    
    # DY
    app.state.mdns_sensor_service = MdnsSensorService(db_module)
    await app.state.mdns_sensor_service.start()
    
    app.state.mqtt_sensor_service = MqttSensorService(
    	db_handler = db_module,
    	broker_host = "127.0.0.1",
    	broker_port = 1883
    )
    app.state.mqtt_sensor_service.start()
    # DY_mDNS, mqtt -> lifespan
    
    
    # 3. mDNS 서비스 등록 (스마트폰 앱 자동 감지용)
    info = ServiceInfo(
        "_jetsonhub._tcp.local.",
        "DS_Safer_Jetson._jetsonhub._tcp.local.",
        addresses=[socket.inet_aton(current_ip)],
        port=8000,
        properties={'desc': 'Industrial Safety Monitoring System'}
    )

    aiozc = AsyncZeroconf()
    await aiozc.async_register_service(info)
    print(f"[mDNS] 젯슨 방송 시작 (IP: {current_ip}, Port: 8000)")

    yield  # 🟢 서버 가동 중 (API 요청 처리 대기) ...
    
    if hasattr(app.state, "mdns_sensor_service"):
    	await app.state.mdns_sensor_service.stop()
    	
    if hasattr(app.state, "mqtt_sensor_service"):
    	app.state.mqtt_sensor_service.stop()

    # [SHUTDOWN] 종료 시 처리
    if aiozc:
        await aiozc.async_unregister_service(info)
        await aiozc.async_close()
        print("[mDNS] 젯슨 방송 종료 및 서버 종료")


app = FastAPI(
    title="Industrial Safety API Server",
    description="산업 안전 모듈 데이터 중계 및 관리 시스템 (DB 통합 & 룰 엔진 연동)",
    version="4.0.0",
    lifespan=lifespan  # <-- 위에서 정의한 lifespan을 FastAPI에 장착!
)



# API 라우터 포함
app.include_router(api_module.router)


# ==========================================
#   실시간 웹소켓 엔드포인트
# ==========================================
@app.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket):
    await manager.connect(websocket)
    print(f"🔗 [웹소켓] 새 기기 연결됨 (현재 연결 수: {len(manager.active_connections)})")

    try:
        while True:
            data = await websocket.receive_text()
            print(f"[앱 응답]: {data}")

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("[웹소켓] 기기 연결 종료")


@app.websocket("/ws/vital")
async def websocket_vital(websocket: WebSocket):
	await websocket.accept()
	active_vital_websocket.append(websocket)
	try:
		while True:
			await websocket.receive_text()
	except WebSocketDisconnect:
		active_vital_websocket.remove(websocket)
		
@app.websocket("/ws/th")
async def websocket_th(websocket:WebSocket):
	await websocket.accept()
	active_th_websocket.append(websocket)
	try:
		while True:
			await websocket.receive_text()
	except WebSocketDisconnect:
		active_th_websocket.remove(websocket)
		
@app.websocket("/reg/band")
async def websocket_band(websocket:WebSocket):
	await websocket.accpet()
	print("------------find band-----------")
	
	try:
		while True:
			data = await websocket.receive_text()
			print(f"[band log]: {data}")
	except WebSocketDisconnect:
		print("gggggggggggg")


@app.get("/")
def root():
    return {
        "status": "online",
        "ip_addr": get_real_ip(),
        "project": "Industrial Safety Monitoring"
    }
