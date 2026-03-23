"""
Quick Fill — InfluxDB 실데이터 + sample 파일 기반으로
ECOD/ARIMA 계산 없이 메트릭만 빠르게 생성하여 InfluxDB에 채움.
10분 이내 완료 목표.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import click
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
log = logging.getLogger("quick_fill")

# ── Time-of-day profiles (KST hour) ─────────────────────────────────────────
_HOUR_PROFILE = {
    0: (0.20, 0.05), 1: (0.15, 0.03), 2: (0.15, 0.02), 3: (0.15, 0.02),
    4: (0.18, 0.03), 5: (0.25, 0.05), 6: (0.40, 0.15), 7: (0.55, 0.25),
    8: (0.70, 0.40), 9: (0.85, 0.60), 10: (0.95, 0.80), 11: (1.00, 0.90),
    12: (1.10, 1.00), 13: (1.05, 0.95), 14: (0.95, 0.85), 15: (0.90, 0.80),
    16: (0.90, 0.80), 17: (0.95, 0.85), 18: (1.05, 0.95), 19: (1.00, 0.90),
    20: (0.90, 0.80), 21: (0.75, 0.60), 22: (0.50, 0.30), 23: (0.30, 0.10),
}


def load_from_influxdb(agent_id: str, bucket: str) -> List[dict]:
    """Load metrics + last peripheral/process state from InfluxDB."""
    from influxdb_client import InfluxDBClient
    from . import influx_writer as iw

    log.info(f"Loading from InfluxDB: agent={agent_id}, bucket={bucket}")
    ic = InfluxDBClient(url=iw.INFLUX_URL, token=iw.INFLUX_TOKEN, org=iw.INFLUX_ORG)
    qa = ic.query_api()

    # Metrics
    tables = qa.query(f'''
    from(bucket: "{bucket}")
      |> range(start: -72h)
      |> filter(fn: (r) => r._measurement == "metrics" and r.agent_id == "{agent_id}")
      |> filter(fn: (r) => r._field == "cpu" or r._field == "memory" or r._field == "disk_io"
          or r._field == "network_sent_bytes" or r._field == "network_received_bytes")
      |> sort(columns: ["_time"], desc: false)
    ''')
    rmap: Dict = {}
    for t in tables:
        for r in t.records:
            ts = r.get_time()
            if ts not in rmap:
                rmap[ts] = {"ts": ts, "CPU": 0, "Memory": 0, "DiskIO": 0,
                            "Network": {"Sent": 0, "Recv": 0}}
            v = r.get_value()
            if v is None:
                continue
            f = r.get_field()
            if f == "cpu":      rmap[ts]["CPU"] = float(v)
            elif f == "memory": rmap[ts]["Memory"] = float(v)
            elif f == "disk_io": rmap[ts]["DiskIO"] = float(v)
            elif f == "network_sent_bytes": rmap[ts]["Network"]["Sent"] = int(v)
            elif f == "network_received_bytes": rmap[ts]["Network"]["Recv"] = int(v)

    # Last peripheral status
    peripherals = {}
    try:
        pt = qa.query(f'''
        from(bucket: "{bucket}")
          |> range(start: -72h)
          |> filter(fn: (r) => r._measurement == "peripheral_status" and r.agent_id == "{agent_id}")
          |> last()
        ''')
        for t in pt:
            for r in t.records:
                peripherals[r.get_field()] = int(r.get_value()) if r.get_value() is not None else 1
    except Exception:
        pass

    # Last process status
    process = {}
    try:
        pr = qa.query(f'''
        from(bucket: "{bucket}")
          |> range(start: -72h)
          |> filter(fn: (r) => r._measurement == "metrics" and r.agent_id == "{agent_id}")
          |> filter(fn: (r) => r._field =~ /^process_/)
          |> last()
        ''')
        for t in pr:
            for r in t.records:
                process[r.get_field().replace("process_", "")] = int(r.get_value() or 1)
    except Exception:
        pass

    ic.close()
    records = sorted(rmap.values(), key=lambda x: x["ts"])
    if peripherals or process:
        log.info(f"Peripherals: {list(peripherals.keys())}, Process: {list(process.keys())}")
    log.info(f"Loaded {len(records)} records from InfluxDB")
    return records, peripherals, process


def load_from_file(path: Path, interval_min: int) -> List[dict]:
    """Load and aggregate sample file into windows."""
    records = []
    with open(path) as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except Exception:
                continue

    cpu_recs = [r for r in records if "CPU" in r and r.get("CPU") is not None]
    log.info(f"File: {len(cpu_recs)} records with CPU")

    windows: Dict = {}
    for r in cpu_recs:
        try:
            ts_str = r["Timestamp"]
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S") if " " in ts_str else datetime.fromisoformat(ts_str.rstrip("Z"))
            key = ts.replace(second=0, microsecond=0, minute=(ts.minute // interval_min) * interval_min)
            windows.setdefault(key, []).append(r)
        except Exception:
            continue

    result = []
    for ts_key in sorted(windows):
        recs = windows[ts_key]
        result.append({
            "CPU": float(np.mean([r.get("CPU", 0) for r in recs])),
            "Memory": float(np.mean([r.get("Memory", 0) for r in recs])),
            "DiskIO": float(np.mean([r.get("DiskIO", 0) for r in recs])),
            "Network": {
                "Sent": int(np.mean([r.get("Network", {}).get("Sent", 0) for r in recs])),
                "Recv": int(np.mean([r.get("Network", {}).get("Recv", 0) for r in recs])),
            },
        })
    log.info(f"File aggregated: {len(result)} windows")
    return result


def synthesize(
    base: List[dict],
    slots: List[datetime],
    agent_id: str,
    peripherals: dict,
    process: dict,
    store_info: dict,
    seed: int = 42,
) -> List[dict]:
    """Generate synthetic data with time-of-day variation + noise + events."""
    rng = np.random.default_rng(seed)
    BLEND = min(6, len(slots))
    last = base[-1]

    points = []
    for i, ts in enumerate(slots):
        b = base[i % len(base)]
        hour = ts.hour
        cm, nm = _HOUR_PROFILE.get(hour, (0.8, 0.5))

        cpu = b["CPU"] * cm + rng.normal(0, b["CPU"] * 0.08)
        mem = b["Memory"] + rng.normal(0, 1.5)
        disk = b["DiskIO"] * cm + rng.normal(0, max(b["DiskIO"] * 0.1, 0.01))
        sent = int(max(0, b["Network"]["Sent"] * nm + rng.normal(0, max(b["Network"]["Sent"] * 0.15, 10))))
        recv = int(max(0, b["Network"]["Recv"] * nm + rng.normal(0, max(b["Network"]["Recv"] * 0.15, 10))))

        cpu = float(np.clip(cpu, 1.0, 100.0))
        mem = float(np.clip(mem, 30.0, 98.0))
        disk = float(np.clip(disk, 0.0, 100.0))

        # Smooth blend from last real data
        if i < BLEND:
            a = i / BLEND
            cpu = last["CPU"] * (1 - a) + cpu * a
            mem = last["Memory"] * (1 - a) + mem * a
            disk = last["DiskIO"] * (1 - a) + disk * a
            sent = int(last["Network"]["Sent"] * (1 - a) + sent * a)
            recv = int(last["Network"]["Recv"] * (1 - a) + recv * a)

        dp = {
            "AgentId": agent_id,
            "Timestamp": ts.isoformat() + "Z",
            "CPU": round(cpu, 1),
            "Memory": round(mem, 1),
            "DiskIO": round(disk, 2),
            "Network": {"Sent": sent, "Recv": recv},
            "StoreInfo": store_info,
            "_nanos_offset": 0,
        }
        if peripherals:
            dp["Peripherals"] = dict(peripherals)
        if process:
            dp["Process"] = {k: v for k, v in process.items()}
        points.append(dp)

    # Inject a few events
    n = len(points)
    if n > 50:
        for _ in range(max(1, n // 36)):
            etype = rng.choice(["cpu_spike", "mem_rise", "net_surge", "idle"])
            s = int(rng.integers(10, max(11, n - 40)))
            if etype == "cpu_spike":
                for j in range(int(rng.integers(2, 6))):
                    if s + j >= n: break
                    t = j / 4
                    points[s + j]["CPU"] = round(float(np.clip(points[s + j]["CPU"] + rng.uniform(40, 60) * np.sin(t * np.pi), 0, 100)), 1)
            elif etype == "mem_rise":
                target = rng.uniform(85, 96)
                dur = int(rng.integers(15, 30))
                for j in range(dur):
                    if s + j >= n: break
                    p = j / max(dur - 1, 1)
                    points[s + j]["Memory"] = round(float(np.clip(points[s + j]["Memory"] + (target - points[s + j]["Memory"]) * p, 30, 98)), 1)
            elif etype == "net_surge":
                m = rng.uniform(5, 15)
                for j in range(int(rng.integers(3, 8))):
                    if s + j >= n: break
                    points[s + j]["Network"]["Sent"] = int(points[s + j]["Network"]["Sent"] * m)
                    points[s + j]["Network"]["Recv"] = int(points[s + j]["Network"]["Recv"] * m)
            elif etype == "idle":
                for j in range(int(rng.integers(6, 12))):
                    if s + j >= n: break
                    points[s + j]["CPU"] = round(float(rng.uniform(1.0, 3.0)), 1)
                    points[s + j]["Network"]["Sent"] = int(rng.uniform(0, 20))
                    points[s + j]["Network"]["Recv"] = int(rng.uniform(0, 10))

    log.info(f"Synthesized {len(points)} data points")
    return points


async def run(
    file_path: str,
    interval_min: int,
    agent_id: str,
    bucket: str,
    start_after: str,
    store_code: str,
    store_name: str,
    pos_no: str,
    region_code: str,
    region_name: str,
    seed: int,
):
    from . import influx_writer
    from .influx_writer import init_influx, close_influx, write_metrics

    store_info = {
        "StoreCode": store_code, "StoreName": store_name,
        "PosNo": pos_no, "RegionCode": region_code, "RegionName": region_name,
    }

    # Parse start_after
    try:
        sa = datetime.fromisoformat(start_after.rstrip("Z"))
    except Exception:
        log.error(f"Invalid --start-after: {start_after}")
        return

    # Load base data
    influx_writer.INFLUX_BUCKET = bucket
    init_influx()

    influx_records, peripherals, process = load_from_influxdb(agent_id, bucket)

    # Merge with file
    base = []
    if influx_records:
        for r in influx_records:
            base.append({"CPU": r["CPU"], "Memory": r["Memory"], "DiskIO": r["DiskIO"], "Network": r["Network"]})

    fpath = Path(file_path)
    if fpath.exists():
        file_windows = load_from_file(fpath, interval_min)
        if base and file_windows:
            merged = []
            for i in range(max(len(base), len(file_windows))):
                if i < len(base): merged.append(base[i])
                if i < len(file_windows): merged.append(file_windows[i])
            base = merged
            log.info(f"Merged: {len(base)} base patterns")
        elif file_windows:
            base = file_windows

    if not base:
        log.error("No base data!")
        return

    # Build time slots
    now = datetime.utcnow().replace(second=0, microsecond=0)
    now = now - timedelta(minutes=now.minute % interval_min)
    start = sa.replace(second=0, microsecond=0)
    start = start - timedelta(minutes=start.minute % interval_min) + timedelta(minutes=interval_min)

    slots = []
    ts = start
    while ts <= now:
        slots.append(ts)
        ts += timedelta(minutes=interval_min)

    if not slots:
        log.error(f"No slots! start={start}, now={now}")
        return

    log.info(f"Filling {len(slots)} slots: {slots[0]} → {slots[-1]}")

    # Synthesize
    data_points = synthesize(base, slots, agent_id, peripherals, process, store_info, seed)

    # Write to InfluxDB
    log.info(f"Writing {len(data_points)} metrics to InfluxDB...")
    written = 0
    for i, dp in enumerate(data_points):
        try:
            raw = {
                "CPU": dp["CPU"], "Memory": dp["Memory"],
                "DiskIO": dp["DiskIO"], "_nanos_offset": dp["_nanos_offset"],
            }
            await write_metrics(
                agent_id=dp["AgentId"],
                timestamp=dp["Timestamp"],
                raw_metrics=raw,
                bucket=bucket,
                full_data=dp,
            )
            written += 1
        except Exception as e:
            log.warning(f"Write error slot {i}: {e}")

        if (i + 1) % max(1, len(slots) // 10) == 0:
            pct = (i + 1) * 100 // len(slots)
            log.info(f"  {i+1}/{len(slots)} ({pct}%) written={written}")

    log.info(f"Done! {written}/{len(data_points)} metrics written")
    log.info(f"  {data_points[0]['Timestamp']} → {data_points[-1]['Timestamp']}")
    close_influx()


@click.command()
@click.option("--file", "file_path", default="../sample/data_pos.txt")
@click.option("--interval-min", default=10, type=int)
@click.option("--agent-id", default="V135-POS-03")
@click.option("--bucket", default="pos_metrics")
@click.option("--start-after", required=True, help="ISO datetime, e.g. 2026-03-20T09:51:00")
@click.option("--store-code", default="V135")
@click.option("--store-name", default="GS25역삼홍인점")
@click.option("--pos-no", default="3")
@click.option("--region-code", default="16")
@click.option("--region-name", default="2부문")
@click.option("--seed", default=42, type=int)
def cli(file_path, interval_min, agent_id, bucket, start_after,
        store_code, store_name, pos_no, region_code, region_name, seed):
    """Quick fill: write synthesized metrics to InfluxDB (no ECOD/ARIMA)."""
    asyncio.run(run(
        file_path=file_path, interval_min=interval_min,
        agent_id=agent_id, bucket=bucket, start_after=start_after,
        store_code=store_code, store_name=store_name,
        pos_no=pos_no, region_code=region_code, region_name=region_name,
        seed=seed,
    ))


if __name__ == "__main__":
    cli()
