import socket
from contextlib import asynccontextmanager
from fastapi import FastAPI
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf  # ★ 변경 1: 비동기(Async) 모듈을 가져옵니다.

from app.db.database import Base, engine
from app.routers import api_module

Base.metadata.create_all(bind=engine)

def get_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

# 전역 변수 이름도 헷갈리지 않게 변경
aiozc = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global aiozc
    ip = get_ip_address()
    
    info = ServiceInfo(
        "_jetsonhub._tcp.local.",
        "DS_Safer_Jetson._jetsonhub._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=8000,
        properties={'desc': 'DS-Safer Jetson Hub'}
    )
    
    # ★ 변경 2: AsyncZeroconf를 사용하고, 앞에 반드시 'await'를 붙여줍니다!
    aiozc = AsyncZeroconf()
    await aiozc.async_register_service(info)
    print(f"📢 [mDNS] 젯슨 방송 시작! IP: {ip}, Port: 8000")
    
    yield 
    
    # ★ 변경 3: 끌 때도 비동기 전용 함수로 꺼줍니다.
    if aiozc:
        await aiozc.async_unregister_service(info)
        await aiozc.async_close()
        print("🔇 [mDNS] 젯슨 방송 종료")


app = FastAPI(
    title="Jetson API Calling Module",
    description="관리자 앱 통신 및 젯슨 내부 위험 정보 중계 시스템",
    version="3.0.0",
    lifespan=lifespan
)

app.include_router(api_module.router)

@app.get("/")
def root():
    return {"message": "Jetson API Module is running!"}
