"""
PulseAI Lite - WebRTC Hub Server
Receives data from C# agents via WebRTC DataChannel and runs anomaly detection.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Set, Optional, Any

import click
from aiohttp import web
import aiohttp_cors
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
)

from .detector import detector, EnhancedAnomalyDetector
from . import influx_writer
from .predict_tracker import tracker
from .forecast_evaluator import evaluator as forecast_evaluator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    force=True
)
log = logging.getLogger("webrtc-hub")

# 서드파티 라이브러리의 노이즈 억제
for _noisy in (
    "aioice", "aiortc", "aiohttp.access", "aiohttp.server", "aiohttp.web",
    "urllib3", "urllib3.connectionpool",
    "influxdb_client", "influxdb_client.client", "influxdb_client.client.write_api",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


@dataclass
class ClientState:
    client_id: str
    role: str = "unknown"
    rooms: Set[str] = field(default_factory=set)


class Hub:
    def __init__(self) -> None:
        self.pcs: Dict[str, RTCPeerConnection] = {}
        self.channels: Dict[str, Any] = {}  # RTCDataChannel
        self.clients: Dict[str, ClientState] = {}
        self.room_members: Dict[str, Set[str]] = {}

    def _ensure_client(self, client_id: str) -> ClientState:
        if client_id not in self.clients:
            self.clients[client_id] = ClientState(client_id=client_id)
        return self.clients[client_id]

    def _add_to_room(self, client_id: str, room: str) -> None:
        self._ensure_client(client_id).rooms.add(room)
        self.room_members.setdefault(room, set()).add(client_id)

    def _remove_from_all_rooms(self, client_id: str) -> None:
        st = self.clients.get(client_id)
        if not st:
            return
        for room in list(st.rooms):
            members = self.room_members.get(room)
            if members:
                members.discard(client_id)
                if not members:
                    self.room_members.pop(room, None)
        st.rooms.clear()

    def disconnect(self, client_id: str) -> None:
        self.channels.pop(client_id, None)
        pc = self.pcs.pop(client_id, None)
        if pc:
            asyncio.create_task(pc.close())
        self._remove_from_all_rooms(client_id)
        self.clients.pop(client_id, None)

    def is_online(self, client_id: str) -> bool:
        ch = self.channels.get(client_id)
        return bool(ch and getattr(ch, "readyState", "") == "open")

    async def send_to(self, to_id: str, msg: dict) -> bool:
        ch = self.channels.get(to_id)
        if not ch or ch.readyState != "open":
            return False
        ch.send(json.dumps(msg, ensure_ascii=False))
        return True

    async def broadcast_room(self, room: str, msg: dict, exclude: Optional[str] = None) -> int:
        members = self.room_members.get(room, set())
        n = 0
        for cid in list(members):
            if exclude and cid == exclude:
                continue
            if await self.send_to(cid, msg):
                n += 1
        return n

    async def broadcast_all(self, msg: dict) -> int:
        """Broadcast to all connected clients."""
        n = 0
        for cid in list(self.channels.keys()):
            if await self.send_to(cid, msg):
                n += 1
        return n


hub = Hub()


def make_pc() -> RTCPeerConnection:
    config = RTCConfiguration(iceServers=[
        RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
        RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
    ])
    return RTCPeerConnection(configuration=config)


# Time-based detection rate limiting
import time
_last_ecod_time: dict = {}
_last_arima_time: dict = {}
ECOD_INTERVAL = 10.0    # ECOD every 10 seconds
ARIMA_INTERVAL = 60.0   # AutoARIMA every 60 seconds


def process_data(data: dict) -> dict:
    """Process incoming data through enhanced anomaly detector."""
    agent_id = data.get("AgentId", "unknown")

    # Use record's simulated timestamp instead of wall-clock time
    # This allows ARIMA to run on schedule even during fast data replay
    try:
        ts_str = data.get("Timestamp", "").rstrip("Z")
        record_ts = datetime.fromisoformat(ts_str).timestamp()
    except (ValueError, AttributeError):
        record_ts = time.time()  # Fallback to wall-clock if parsing fails

    # Determine what to run this cycle
    run_ecod = False
    run_arima = False

    last_ecod = _last_ecod_time.get(agent_id, 0)
    if record_ts - last_ecod >= ECOD_INTERVAL:
        _last_ecod_time[agent_id] = record_ts
        run_ecod = True

    # Use absolute value for timing to handle both forward and reverse chronological order
    last_arima = _last_arima_time.get(agent_id, None)
    if last_arima is None:
        # First record - always run ARIMA on first data point
        _last_arima_time[agent_id] = record_ts
        run_arima = True
        log.debug(f"[TIMING] First record for {agent_id}, ARIMA enabled")
    else:
        # Subsequent records - use absolute time difference
        time_diff = abs(record_ts - last_arima)
        if time_diff >= ARIMA_INTERVAL:
            _last_arima_time[agent_id] = record_ts
            run_arima = True
            log.debug(f"[TIMING] ARIMA enabled for {agent_id}: diff={time_diff:.0f}s >= {ARIMA_INTERVAL}s")
        else:
            log.debug(f"[TIMING] ARIMA skipped for {agent_id}: diff={time_diff:.0f}s < {ARIMA_INTERVAL}s")

    # Run detection with calculated intervals
    # run_ecod and run_arima are determined by interval checks above
    result = detector.detect(data, run_ecod=run_ecod, run_arima=run_arima)

    # Log detections
    for d in result.detections:
        if d.engine == "ecod":
            log.info(f"ECOD {d.metric}: score={d.score:.3f}, severity={d.severity}, confidence={d.confidence:.2f}")
        elif d.engine == "arima":
            log.info(f"ARIMA {d.metric}: value={d.value:.2f}, forecast={d.forecast:.2f}, residual={d.residual:.2f}")
        elif d.engine == "ensemble":
            log.info(f"ENSEMBLE: score={d.score:.3f}, severity={d.severity}")
        elif d.engine == "peripheral":
            # 연속 실패는 처음(3회), 이후 100회 단위로만 출력
            count = int(d.value)
            if count == 3 or count % 100 == 0:
                log.info(f"PERIPHERAL {d.metric}: {d.details}")
    
    # Run forecast evaluation: collect ARIMA predictions per horizon
    # and evaluate them with long-term ECOD
    forecasts_by_horizon: dict = {}
    for d in result.detections:
        if d.engine == "arima" and d.forecast_horizon:
            for fh in d.forecast_horizon:
                h_min = fh["minutes"]
                if h_min not in forecasts_by_horizon:
                    forecasts_by_horizon[h_min] = {}
                metric_key = d.metric.lower()
                if metric_key == "diskio":
                    metric_key = "disk_io"
                forecasts_by_horizon[h_min][metric_key] = fh["value"]

    # Update event state and fallback buffer
    forecast_evaluator.update_event(agent_id, data)
    forecast_evaluator.update_fallback_buffer(agent_id, data)

    # Evaluate forecasts
    forecast_eval = None
    if forecasts_by_horizon:
        eval_result = forecast_evaluator.evaluate(agent_id, data.get("Timestamp", ""), forecasts_by_horizon)
        forecast_eval = forecast_evaluator.to_dict(eval_result)
        if eval_result.overall_severity != "normal":
            log.info(f"FORECAST_EVAL {agent_id}: {eval_result.overall_severity} "
                     f"(horizons: {[(h.horizon_min, h.severity, h.final_score) for h in eval_result.horizons]})")

    result_dict = detector.to_dict(result)

    # Persist all detection results (ECOD, ARIMA, ensemble) to InfluxDB
    timestamp_str = data.get("Timestamp", "")
    _store_info = data.get("StoreInfo", {})
    for d in result.detections:
        asyncio.create_task(influx_writer.write_detection(
            agent_id=agent_id,
            timestamp=timestamp_str,
            engine=d.engine,
            metric=d.metric,
            value=d.value,
            score=d.score,
            threshold=d.threshold,
            severity=d.severity,
            confidence=d.confidence,
            forecast=d.forecast,
            residual=float(d.residual) if d.residual is not None else None,
            details=d.details,
            bucket=influx_writer.INFLUX_BUCKET,
            store_info=_store_info,
        ))

    if forecast_eval:
        result_dict["forecast_evaluation"] = forecast_eval
        asyncio.create_task(influx_writer.write_forecast_evaluation(
            agent_id=agent_id,
            timestamp=timestamp_str,
            horizons=forecast_eval.get("horizons", []),
            overall_severity=forecast_eval.get("overall_severity", "normal"),
            model_ready=forecast_eval.get("model_ready", False),
            data_source=forecast_eval.get("data_source", "none"),
            bucket=influx_writer.INFLUX_BUCKET,
            store_info=_store_info,
        ))

    return result_dict



def _handle_data_message(client_id: str, st: ClientState, channel, data: dict):
    """DataChannel 메시지 공통 처리 (client-created / server-created 채널 모두 사용)"""
    t = data.get("type")

    # C# 에이전트가 type/payload 래핑 없이 직접 보내는 경우 자동 래핑
    if t is None and data.get("AgentId"):
        data = {"type": "data", "payload": data}
        t = "data"

    log.info("MSG from %s: type=%s", client_id, t)

    if t == "hello":
        st.role = data.get("role", st.role)
        channel.send(json.dumps({"type": "hello_ack", "role": st.role}, ensure_ascii=False))
        return

    if t == "join":
        room = data.get("room")
        if room:
            hub._add_to_room(client_id, room)
            channel.send(json.dumps({"type": "join_ack", "room": room}, ensure_ascii=False))
        return

    if t == "leave":
        room = data.get("room")
        if room and room in st.rooms:
            st.rooms.discard(room)
            members = hub.room_members.get(room)
            if members:
                members.discard(client_id)
                if not members:
                    hub.room_members.pop(room, None)
            channel.send(json.dumps({"type": "leave_ack", "room": room}, ensure_ascii=False))
        return

    if t == "send":
        to_id = data.get("to")
        payload = data.get("payload")
        if not to_id:
            channel.send(json.dumps({"type": "error", "error": "missing to"}, ensure_ascii=False))
            return
        asyncio.create_task(hub.send_to(to_id, {"type": "relay", "from": client_id, "payload": payload}))
        return

    if t == "broadcast":
        room = data.get("room")
        payload = data.get("payload")
        if not room:
            channel.send(json.dumps({"type": "error", "error": "missing room"}, ensure_ascii=False))
            return
        asyncio.create_task(hub.broadcast_room(room, {"type": "relay", "from": client_id, "room": room, "payload": payload}, exclude=client_id))
        return

    if t == "ping":
        channel.send(json.dumps({"type": "pong", "ts": data.get("ts")}, ensure_ascii=False))
        return

    if t == "data":
        payload = data.get("payload", {})
        agent_id = payload.get("AgentId")

        if not agent_id:
            channel.send(json.dumps({"type": "data_ack", "ts": data.get("ts")}, ensure_ascii=False))
            return

        store_info = payload.get("StoreInfo", {})
        for log_entry in payload.get("Logs", []):
            body_type = log_entry.get("BodyType", "")
            key_values = log_entry.get("KeyValues", {})
            if not key_values:
                continue
            if body_type == "주변장치 체크":
                asyncio.create_task(influx_writer.write_peripheral_status(
                    agent_id,
                    payload.get("Timestamp", ""),
                    key_values,
                    bucket=influx_writer.INFLUX_BUCKET,
                    store_info=store_info,
                ))
            elif body_type:
                # 승인 처리시간, 승인 처리결과, 영수증프린터 커버상태, 영수증 용지상태
                asyncio.create_task(influx_writer.write_log_entry(
                    agent_id,
                    payload.get("Timestamp", ""),
                    body_type,
                    key_values,
                    bucket=influx_writer.INFLUX_BUCKET,
                    store_info=store_info,
                ))

        if "CPU" not in payload:
            channel.send(json.dumps({"type": "data_ack", "ts": data.get("ts")}, ensure_ascii=False))
            return

        result = process_data(payload)

        asyncio.create_task(influx_writer.write_metrics(
            agent_id,
            payload.get("Timestamp"),
            result.get("raw_metrics", {}),
            bucket=influx_writer.INFLUX_BUCKET,
            full_data=payload
        ))

        asyncio.create_task(tracker.compare_actual_async(
            payload.get("AgentId"),
            payload.get("Timestamp"),
            result.get("raw_metrics", {})
        ))

        for det in result.get("detections", []):
            if det.get("engine") == "arima" and det.get("forecast_horizon"):
                for fh in det.get("forecast_horizon", []):
                    asyncio.create_task(influx_writer.write_forecast(
                        payload.get("AgentId"),
                        payload.get("Timestamp"),
                        det.get("metric"),
                        fh.get("minutes"),
                        fh.get("value"),
                        bucket=influx_writer.INFLUX_BUCKET,
                        store_info=store_info,
                    ))
                    tracker.record(
                        payload.get("AgentId"),
                        det.get("metric"),
                        payload.get("Timestamp"),
                        fh.get("minutes"),
                        fh.get("value")
                    )

        channel.send(json.dumps({"type": "data_ack", "ts": data.get("ts")}, ensure_ascii=False))
        return

    # Default: echo
    channel.send(json.dumps({"type": "echo", "payload": data}, ensure_ascii=False))


async def offer(request: web.Request) -> web.Response:
    client_id = request.query.get("client_id")
    role = request.query.get("role", "unknown")
    if not client_id:
        return web.json_response({"error": "missing client_id"}, status=400)

    params = await request.json()
    sdp = params["sdp"]
    type_ = params["type"]

    if client_id in hub.pcs:
        log.info("Replacing existing connection for client_id=%s", client_id)
        hub.disconnect(client_id)

    pc = make_pc()
    hub.pcs[client_id] = pc
    st = hub._ensure_client(client_id)
    st.role = role

    log.info("New PeerConnection client_id=%s role=%s (total=%d)", client_id, role, len(hub.pcs))

    @pc.on("datachannel")
    def on_datachannel(channel):
        hub.channels[client_id] = channel
        log.info("DataChannel open: client_id=%s label=%s", client_id, channel.label)

        # Send welcome message (smarthug 원본과 동일한 구조)
        welcome_msg = {
            "type": "welcome",
            "client_id": client_id,
            "mode": "live",
            "batch_forecast": None,
        }
        channel.send(json.dumps(welcome_msg, ensure_ascii=False))

        # Auto-join the "pulseai" room for broadcasts
        hub._add_to_room(client_id, "pulseai")

        @channel.on("message")
        def on_message(message):
            # RAW 로깅
            raw = message if isinstance(message, str) else repr(message)
            log.info("RAW from %s: %s", client_id, raw)

            try:
                data = json.loads(message) if isinstance(message, str) else {"type": "binary", "len": len(message)}
            except Exception:
                data = {"type": "text", "payload": str(message)}

            _handle_data_message(client_id, st, channel, data)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        log.info("client_id=%s connectionState=%s", client_id, pc.connectionState)
        if hub.pcs.get(client_id) is not pc:
            return
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()
            hub.disconnect(client_id)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=type_))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Agent의 IP 서브넷에 맞는 candidate만 남기기
    # nanocube-pico-1은 인터페이스가 많아서 SIPSorcery가 혼동할 수 있음
    answer_sdp = pc.localDescription.sdp
    agent_ip = request.remote or ""
    # Agent가 10.145.165.x 대역이면 해당 서브넷 candidate만 남김
    if agent_ip.startswith("10.145.165."):
        filtered_lines = []
        for line in answer_sdp.splitlines():
            if line.startswith("a=candidate:"):
                # 10.145.165.x candidate만 유지
                if "10.145.165." not in line:
                    continue
            filtered_lines.append(line)
        answer_sdp = "\n".join(filtered_lines)
        log.info("Filtered SDP candidates to 10.145.165.x subnet for client_id=%s", client_id)

    return web.json_response({"sdp": answer_sdp, "type": pc.localDescription.type})


async def who(request: web.Request) -> web.Response:
    online = []
    for cid, st in hub.clients.items():
        online.append({
            "client_id": cid,
            "role": st.role,
            "rooms": sorted(list(st.rooms)),
            "online": hub.is_online(cid),
        })
    return web.json_response({
        "clients": online,
        "rooms": {k: sorted(list(v)) for k, v in hub.room_members.items()},
        "mode": "live",
    })


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "mode": "live"})


async def api_accuracy(request: web.Request) -> web.Response:
    """
    Get prediction accuracy metrics from InfluxDB.
    Query params: agent_id, metric, horizon_min
    """
    agent_id = request.query.get("agent_id", "V135-POS-03")
    metric = request.query.get("metric", "CPU")
    horizon_min = request.query.get("horizon_min", "30")
    bucket = request.query.get("bucket", influx_writer.INFLUX_BUCKET)

    try:
        data = influx_writer.get_latest_accuracy(agent_id, metric, int(horizon_min), bucket=bucket)
        return web.json_response(data)
    except Exception as e:
        log.warning(f"Error querying accuracy: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def api_forecast_vs_actual(request: web.Request) -> web.Response:
    """
    Return time-matched forecast vs actual pairs from the accuracy measurement.
    Query params: agent_id, metric (cpu/memory), horizon_min, bucket
    """
    agent_id = request.query.get("agent_id", "V135-POS-03")
    metric_param = request.query.get("metric", "cpu").lower()
    horizon_min = request.query.get("horizon_min", "60")
    bucket = request.query.get("bucket", influx_writer.INFLUX_BUCKET)

    # accuracy measurement uses mixed case: CPU, Memory
    metric_tag = {"cpu": "CPU", "memory": "Memory"}.get(metric_param, metric_param.upper())

    try:
        from influxdb_client import InfluxDBClient

        ic = InfluxDBClient(url=influx_writer.INFLUX_URL, token=influx_writer.INFLUX_TOKEN, org=influx_writer.INFLUX_ORG)
        query_api = ic.query_api()

        # Query matched forecast/actual pairs from the accuracy measurement
        query = f'''
        from(bucket: "{bucket}")
          |> range(start: -96h)
          |> filter(fn: (r) => r._measurement == "accuracy"
              and r.agent_id == "{agent_id}"
              and r.metric == "{metric_tag}"
              and r.horizon_min == "{horizon_min}")
          |> filter(fn: (r) => r._field == "actual_value" or r._field == "forecast_value" or r._field == "error_pct")
          |> sort(columns: ["_time"], desc: false)
          |> limit(n: 60)
        '''

        # Group fields by timestamp into records
        records_by_time: dict = {}
        tables = query_api.query(query)
        for table in tables:
            for record in table.records:
                t = str(record.get_time())
                if t not in records_by_time:
                    records_by_time[t] = {"time": t}
                records_by_time[t][record.get_field()] = record.get_value()

        ic.close()

        records = sorted(records_by_time.values(), key=lambda x: x["time"])

        return web.json_response({
            "agent_id": agent_id,
            "metric": metric_param,
            "horizon_min": horizon_min,
            "records": records,
        })

    except Exception as e:
        log.warning(f"Error querying forecast vs actual: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def api_forecast_evaluation(request: web.Request) -> web.Response:
    """
    Get the latest forecast evaluation for an agent.
    Called on page load to restore state after refresh/restart.
    Query params: agent_id, bucket
    """
    agent_id = request.query.get("agent_id", "V135-POS-03")
    bucket = request.query.get("bucket", influx_writer.INFLUX_BUCKET)

    try:
        data = influx_writer.get_latest_forecast_evaluation(agent_id, bucket=bucket)
        if data:
            return web.json_response(data)
        return web.json_response({"error": "No evaluation data found"}, status=404)
    except Exception as e:
        log.warning(f"Error querying forecast evaluation: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def api_recent_metrics(request: web.Request) -> web.Response:
    """
    Get recent raw metrics from InfluxDB.
    Query params: agent_id, limit, bucket
    """
    agent_id = request.query.get("agent_id", "V135-POS-03")
    limit = int(request.query.get("limit", "100"))
    bucket = request.query.get("bucket", influx_writer.INFLUX_BUCKET)

    try:
        data = influx_writer.get_recent_metrics(agent_id, limit=limit, bucket=bucket)
        return web.json_response({"agent_id": agent_id, "metrics": data})
    except Exception as e:
        log.warning(f"Error querying recent metrics: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def api_recent_detections(request: web.Request) -> web.Response:
    """
    Get recent anomaly detection results from InfluxDB.
    Query params: agent_id, limit, bucket
    """
    agent_id = request.query.get("agent_id", "V135-POS-03")
    limit = int(request.query.get("limit", "200"))
    bucket = request.query.get("bucket", influx_writer.INFLUX_BUCKET)

    try:
        data = influx_writer.get_recent_detections(agent_id, limit=limit, bucket=bucket)
        return web.json_response({"agent_id": agent_id, "detections": data})
    except Exception as e:
        log.warning(f"Error querying recent detections: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def on_shutdown(app: web.Application):
    coros = []
    for cid, pc in list(hub.pcs.items()):
        coros.append(pc.close())
    await asyncio.gather(*coros, return_exceptions=True)
    hub.pcs.clear()
    hub.channels.clear()
    hub.clients.clear()
    hub.room_members.clear()


@web.middleware
async def http_logger(request: web.Request, handler):
    """모든 HTTP 요청 로깅 - WindowsAgent가 HTTP로 보내는지 감지"""
    log.info(
        "HTTP %s %s from=%s content_type=%s content_length=%s",
        request.method,
        request.path_qs,
        request.remote,
        request.content_type,
        request.content_length,
    )
    # 등록되지 않은 경로로 POST 요청이 오면 경고 + body 출력
    known_paths = {"/offer", "/health", "/who", "/api/accuracy",
                   "/api/forecast-vs-actual", "/api/forecast-evaluation",
                   "/api/recent-metrics", "/api/recent-detections"}
    if request.path not in known_paths:
        body = await request.read()
        log.warning(
            "UNKNOWN PATH: %s %s from=%s body=%s",
            request.method, request.path, request.remote,
            body[:500] if body else b"(empty)",
        )
    elif request.method == "POST" and request.path != "/offer":
        body = await request.read()
        log.warning(
            "UNEXPECTED POST (not WebRTC offer): %s from=%s body=%s",
            request.path, request.remote,
            body[:500] if body else b"(empty)",
        )
    return await handler(request)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/who", who)
    app.router.add_post("/offer", offer)
    app.router.add_get("/api/accuracy", api_accuracy)
    app.router.add_get("/api/forecast-vs-actual", api_forecast_vs_actual)
    app.router.add_get("/api/forecast-evaluation", api_forecast_evaluation)
    app.router.add_get("/api/recent-metrics", api_recent_metrics)
    app.router.add_get("/api/recent-detections", api_recent_detections)
    app.on_shutdown.append(on_shutdown)

    cors = aiohttp_cors.setup(
        app,
        defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
            )
        },
    )
    for route in list(app.router.routes()):
        cors.add(route)
    return app


@click.command()
@click.option("--host", default="0.0.0.0", help="Host to bind")
@click.option("--port", default=8080, type=int, help="Port to bind")
@click.option("--log-level", default="INFO", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False), help="Log level")
@click.option("--bucket", default="pos_metrics",
              type=click.Choice(["pos_metrics", "sample_metrics"], case_sensitive=False),
              help="InfluxDB bucket: pos_metrics (live) or sample_metrics (sample)")
def main(host: str, port: int, log_level: str, bucket: str) -> None:
    """PulseAI Lite - WebRTC Hub Server"""
    level = getattr(logging, log_level.upper())
    logging.getLogger().setLevel(level)
    if level > logging.DEBUG:
        for _noisy in (
            "aioice", "aiortc", "aiohttp.access", "aiohttp.server", "aiohttp.web",
            "urllib3", "urllib3.connectionpool",
            "influxdb_client", "influxdb_client.client", "influxdb_client.client.write_api",
        ):
            logging.getLogger(_noisy).setLevel(logging.WARNING)

    influx_writer.INFLUX_BUCKET = bucket
    log.info(f"Starting PulseAI Hub  [bucket={bucket}]")
    influx_writer.init_influx()

    app = create_app()

    try:
        web.run_app(app, host=host, port=port)
    finally:
        influx_writer.close_influx()


if __name__ == "__main__":
    main()
