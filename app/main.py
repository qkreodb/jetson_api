import socket
from contextlib import asynccontextmanager
from fastapi import FastAPI
from zeroconf import ServiceInfo, Zeroconf

from app.db.database import Base, engine
from app.routers import api_module

# 시작할 때 DB 테이블 자동 생성
Base.metadata.create_all(bind=engine)

# ★ 추가됨: 내 IP 주소를 자동으로 찾아주는 함수
def get_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 외부로 나가는 연결을 만들어 로컬 IP를 알아냅니다.
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

# mDNS 방송을 관리하는 전역 변수
zeroconf_instance = None

# ★ 추가됨: FastAPI 서버가 켜지고 꺼질 때 실행할 동작 (Lifespan)
@asynccontextmanager
async def lifespan(app: FastAPI):
    global zeroconf_instance
    ip = get_ip_address()
    
    # 안드로이드 앱이 애타게 찾고 있는 바로 그 이름!
    info = ServiceInfo(
        "_jetsonhub._tcp.local.",
        "DS_Safer_Jetson._jetsonhub._tcp.local.", # 안드로이드에 뜰 기기 이름
        addresses=[socket.inet_aton(ip)],
        port=8000, # ★ uvicorn 실행 포트와 똑같아야 합니다!
        properties={'desc': 'DS-Safer Jetson Hub'}
    )
    
    zeroconf_instance = Zeroconf()
    zeroconf_instance.register_service(info)
    print(f"📢 [mDNS] 젯슨 방송 시작! IP: {ip}, Port: 8000")
    
    yield # --- 이 시점부터 서버가 쌩쌩하게 돌아갑니다 ---
    
    # 서버가 꺼질 때 방송도 깔끔하게 종료
    if zeroconf_instance:
        zeroconf_instance.unregister_service(info)
        zeroconf_instance.close()
        print("🔇 [mDNS] 젯슨 방송 종료")


app = FastAPI(
    title="Jetson API Calling Module",
    description="관리자 앱 통신 및 젯슨 내부 위험 정보 중계 시스템",
    version="3.0.0",
    lifespan=lifespan # ★ 추가됨: 확성기 기능을 FastAPI에 연결!
)

# API 호출 모듈 라우터 연결
app.include_router(api_module.router)

@app.get("/")
def root():
    return {"message": "Jetson API Module is running!"}
