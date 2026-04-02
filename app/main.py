import socket
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

# 🌟 DB 핸들러 & 룰 엔진 임포트
from app.db.db_handler import DatabaseHandler
from core_engine import SafetyDetectionModule  # 뇌(룰 엔진) 출근!
from app.routers import api_module
from app.routers.api_module import manager

# 🌟 전역 DB 핸들러 인스턴스 생성 (비밀번호 꼭 본인 걸로 수정하세요!)
db_module = DatabaseHandler(host='127.0.0.1', user='root', password='ekthf12', db_name='ds_db')

# 🌟 전역 룰 엔진 인스턴스 공간
safety_core = None


# 알람 발송용 모의 모듈 (나중에는 실제 앱 푸시나 웹소켓 전송 로직으로 변경 가능)
class MockTransmission:
    def send_push_notification(self, payload):
        print("\n" + "💺" * 20)
        print(f"📡 [알람 발송] {payload['type'].upper()} 발생!")
        print(f"📍 대상 토픽: {payload['target_topic']}")
        print(f"📝 메시지: {payload.get('message')}")
        print("💺" * 20 + "\n")


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
        print(f"✅ [DB] 젯슨 정보 업데이트 완료! IP: {ip}")
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
    global aiozc, safety_core
    current_ip = get_real_ip()

    # 1. DB 초기화 세팅
    startup_db_init(current_ip)

    # 🌟 2. 룰 엔진(안전 감시 모듈) 백그라운드 가동!
    trans_module = MockTransmission()
    safety_core = SafetyDetectionModule(db_module, trans_module)
    safety_core.update_and_get_subscriptions()  # DB에서 감시할 센서 목록 빨아오기
    print("🧠 [룰 엔진] 백그라운드 안전 감시 시스템 가동 완료!")

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
    print(f"📢 [mDNS] 젯슨 방송 시작! (IP: {current_ip}, Port: 8000)")

    yield  # 🟢 서버 가동 중 (API 요청 처리 대기) ...

    # [SHUTDOWN] 종료 시 처리
    if aiozc:
        await aiozc.async_unregister_service(info)
        await aiozc.async_close()
        print("🔇 [mDNS] 젯슨 방송 종료 및 서버 종료")


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
            print(f"📱 [앱 응답]: {data}")

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("🔌 [웹소켓] 기기 연결 종료")


@app.get("/")
def root():
    return {
        "status": "online",
        "ip_addr": get_real_ip(),
        "project": "Industrial Safety Monitoring"
    }