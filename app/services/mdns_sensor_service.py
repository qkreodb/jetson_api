import asyncio
import socket
from datetime import datetime
from typing import Dict, Any, Optional

from zeroconf import ServiceBrowser, ServiceStateChange
from zeroconf.asyncio import AsyncZeroconf


class MdnsSensorService:
    """
    _tempsensor._tcp.local. 센서를 탐색하는 서비스

    역할:
    - mDNS로 센서 발견
    - TXT 정보 파싱
    - 내부 캐시에 저장
    - 앱에서 조회할 발견 목록 제공
    """

    SERVICE_TYPE = "_tempsensor._tcp.local."

    def __init__(self, db_handler):
        self.db_handler = db_handler
        self.aiozc: Optional[AsyncZeroconf] = None
        self.browser = None
        self.discovered_sensors: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self.loop = None

    async def start(self):
        if self._running:
            return

        self.loop = asyncio.get_running_loop()
        self.aiozc = AsyncZeroconf()
        self.browser = ServiceBrowser(
            self.aiozc.zeroconf,
            self.SERVICE_TYPE,
            handlers=[self._on_service_state_change]
        )
        self._running = True
        print("[mDNS Sensor] scanner started")

    async def stop(self):
        self._running = False
        if self.aiozc:
            await self.aiozc.async_close()
            self.aiozc = None
        print("[mDNS Sensor] scanner stopped")

    def get_discovered_sensors(self):
        return list(self.discovered_sensors.values())

    def _on_service_state_change(self, zeroconf, service_type, name, state_change):
        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            if self.loop:
                asyncio.run_coroutine_threadsafe(
                    self._handle_service_upsert(name),
                    self.loop
                )
        elif state_change is ServiceStateChange.Removed:
            if self.loop:
                asyncio.run_coroutine_threadsafe(
                    self._handle_service_removed(name),
                    self.loop
                )

    async def _handle_service_upsert(self, name: str):
        if not self.aiozc:
            return

        try:
            info = await self.aiozc.async_get_service_info(
                self.SERVICE_TYPE,
                name,
                timeout=3000
            )
            if not info:
                return

            properties = {}
            for k, v in info.properties.items():
                key = k.decode() if isinstance(k, bytes) else str(k)
                val = v.decode() if isinstance(v, bytes) else str(v)
                properties[key] = val

            sensor_id = properties.get("sensor_id")
            if not sensor_id:
                print(f"[mDNS Sensor] sensor_id missing: {name}")
                return

            ip_addr = None
            if info.addresses:
                try:
                    ip_addr = socket.inet_ntoa(info.addresses[0])
                except Exception:
                    ip_addr = None

            sensor_info = {
                "sensor_id": sensor_id,
                "sensor_type": properties.get("sensor_type", "unknown"),
                "sen_name": properties.get("sen_name", sensor_id),
                "sen_locate": properties.get("sen_locate", "default"),
                "model": properties.get("model", ""),
                "mqtt_topic": properties.get("mqtt_topic", f"sensors/{sensor_id}"),
                "mdns_hostname": info.server.rstrip(".") if info.server else name.rstrip("."),
                "ip_addr": ip_addr,
                "is_online": True,
                "last_seen_at": datetime.now(),
            }

            self.discovered_sensors[sensor_id] = sensor_info
            print(f"[mDNS Sensor] found/updated: {sensor_id} @ {ip_addr}")

        except Exception as e:
            print(f"[mDNS Sensor] handle upsert error: {e}")

    async def _handle_service_removed(self, name: str):
        target_sensor_id = None

        for sensor_id, sensor in self.discovered_sensors.items():
            if sensor.get("mdns_hostname") == name.rstrip("."):
                target_sensor_id = sensor_id
                break

        if not target_sensor_id:
            return

        self.discovered_sensors[target_sensor_id]["is_online"] = False
        self.discovered_sensors[target_sensor_id]["last_seen_at"] = datetime.now()
        print(f"[mDNS Sensor] removed: {target_sensor_id}")
