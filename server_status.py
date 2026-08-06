"""Render/호스트 서버 리소스·통신 상태 수집."""
from __future__ import annotations

import os
import shutil
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:
    KST = timezone(timedelta(hours=9))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_PROCESS_STARTED = time.time()


def _bytes_label(n: int | float | None) -> str:
    if n is None:
        return "-"
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "-"
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    if i == 0:
        return f"{int(v)}{units[i]}"
    return f"{v:.1f}{units[i]}"


def _pct(used: float | int | None, total: float | int | None) -> float | None:
    try:
        t = float(total or 0)
        if t <= 0:
            return None
        return round(100.0 * float(used or 0) / t, 1)
    except (TypeError, ValueError):
        return None


def _level(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    if pct >= 90:
        return "critical"
    if pct >= 75:
        return "warn"
    return "ok"


def _dir_size(path: Path, *, max_files: int = 80000) -> int:
    total = 0
    n = 0
    try:
        if path.is_file():
            return int(path.stat().st_size)
        if not path.is_dir():
            return 0
        for f in path.rglob("*"):
            if not f.is_file():
                continue
            try:
                total += int(f.stat().st_size)
            except OSError:
                continue
            n += 1
            if n >= max_files:
                break
    except OSError:
        return total
    return total


def _app_paths() -> list[tuple[str, Path]]:
    """앱 실사용 집계 대상 (Render 프로젝트 루트 기준)."""
    cwd = Path.cwd().resolve()
    # Render 기본 경로 우선
    render_src = Path("/opt/render/project/src")
    root = render_src if render_src.is_dir() else cwd
    names = [
        ("코드/설정", root),
        ("업로드 파일", root / "static" / "uploads"),
        ("static", root / "static"),
        ("resources", root / "resources"),
        ("data", root / "data"),
        ("risk_assessment", root / "risk_assessment"),
        ("임시/캐시", root / "__pycache__"),
    ]
    # 중복 제거(코드/설정은 루트 전체라 개별 폴더와 겹침 → 루트는 총합용으로만)
    return [(label, p) for label, p in names if p.exists()]


def _app_disk_breakdown() -> dict:
    """Smart FMS가 실제로 쓰는 디스크만 집계 (호스트 공유 디스크와 구분)."""
    cwd = Path.cwd().resolve()
    render_src = Path("/opt/render/project/src")
    root = render_src if render_src.is_dir() else cwd

    skip_names = {".git", ".venv", "venv", "node_modules"}
    top: list[dict] = []
    app_total = 0
    try:
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if child.name in skip_names:
                continue
            size = _dir_size(child)
            if size <= 0:
                continue
            app_total += size
            top.append(
                {
                    "name": child.name + ("/" if child.is_dir() else ""),
                    "bytes": size,
                    "label": _bytes_label(size),
                }
            )
    except OSError:
        app_total = _dir_size(root)
        top = []

    # 업로드만 별도 강조
    uploads = root / "static" / "uploads"
    uploads_bytes = _dir_size(uploads) if uploads.exists() else 0
    top.sort(key=lambda x: x["bytes"], reverse=True)
    top = top[:8]

    # 영구 디스크 마운트(있을 때만)
    persistent = None
    for env_key in ("RENDER_DISK_PATH", "DISK_MOUNT_PATH"):
        mp = (os.environ.get(env_key) or "").strip()
        if mp and Path(mp).exists():
            try:
                u = shutil.disk_usage(mp)
                persistent = {
                    "path": mp,
                    "total": u.total,
                    "used": u.used,
                    "free": u.free,
                    "total_label": _bytes_label(u.total),
                    "used_label": _bytes_label(u.used),
                    "free_label": _bytes_label(u.free),
                    "percent": _pct(u.used, u.total),
                }
            except OSError:
                persistent = None
            break

    return {
        "root": str(root),
        "total_bytes": app_total,
        "total_label": _bytes_label(app_total),
        "uploads_bytes": uploads_bytes,
        "uploads_label": _bytes_label(uploads_bytes),
        "top": top,
        "persistent": persistent,
        "desc": "Smart FMS 앱 폴더가 실제로 차지한 용량입니다. (공유 호스트 디스크와 별개)",
    }


def _host_disk_info() -> dict:
    """컨테이너/호스트 루트 파일시스템 — 공유 인프라 참고값."""
    path = "C:\\" if os.name == "nt" else "/"
    try:
        u = shutil.disk_usage(path)
        total, used, free = u.total, u.used, u.free
        pct = _pct(used, total)
        return {
            "path": path,
            "total": total,
            "used": used,
            "free": free,
            "total_label": _bytes_label(total),
            "used_label": _bytes_label(used),
            "free_label": _bytes_label(free),
            "percent": pct,
            "level": "ok",  # 공유 FS라 경보에 쓰지 않음
            "desc": "Render 공유 루트 디스크 참고값입니다. OS·다른 컨테이너와 합쳐진 수치라 Smart FMS 점유가 아닙니다.",
        }
    except OSError:
        return {
            "path": path,
            "total": None,
            "used": None,
            "free": None,
            "total_label": "-",
            "used_label": "-",
            "free_label": "-",
            "percent": None,
            "level": "unknown",
            "desc": "호스트 디스크 정보를 읽지 못했습니다.",
        }


def _disk_info() -> dict:
    """대시보드용: 앱 실사용 + 호스트 참고."""
    app = _app_disk_breakdown()
    host = _host_disk_info()
    # 표시용 percent는 앱/호스트 비교가 아니라 앱 크기를 읽기 쉽게 고정 표기
    return {
        "mode": "app_vs_host",
        "app": app,
        "host": host,
        # 하위 호환(옛 UI 필드): 앱 실사용을 메인으로
        "path": app.get("root") or host.get("path"),
        "total": host.get("total"),
        "used": app.get("total_bytes"),
        "free": host.get("free"),
        "total_label": app.get("total_label"),
        "used_label": app.get("total_label"),
        "free_label": host.get("free_label"),
        "percent": None,
        "level": "ok",
        "desc": app.get("desc"),
    }


def _mem_cpu_info() -> tuple[dict, dict]:
    mem = {
        "total": None,
        "used": None,
        "available": None,
        "total_label": "-",
        "used_label": "-",
        "available_label": "-",
        "percent": None,
        "level": "unknown",
        "process_rss_label": "-",
    }
    cpu = {
        "percent": None,
        "count": os.cpu_count() or 0,
        "load_1m": None,
        "load_5m": None,
        "load_15m": None,
        "level": "unknown",
    }
    try:
        import psutil

        vm = psutil.virtual_memory()
        pct = float(vm.percent)
        mem.update(
            {
                "total": int(vm.total),
                "used": int(vm.used),
                "available": int(vm.available),
                "total_label": _bytes_label(vm.total),
                "used_label": _bytes_label(vm.used),
                "available_label": _bytes_label(vm.available),
                "percent": round(pct, 1),
                "level": _level(pct),
                "process_rss_label": _bytes_label(psutil.Process().memory_info().rss),
            }
        )
        # 짧은 샘플 — Render 단발성 폴링에 적합
        cpu_pct = float(psutil.cpu_percent(interval=0.15))
        cpu["percent"] = round(cpu_pct, 1)
        cpu["level"] = _level(cpu_pct)
        try:
            load = os.getloadavg()
            cpu["load_1m"] = round(load[0], 2)
            cpu["load_5m"] = round(load[1], 2)
            cpu["load_15m"] = round(load[2], 2)
        except (AttributeError, OSError):
            pass
    except Exception:
        # psutil 없을 때 /proc·loadavg 폴백
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                info = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(":")] = int(parts[1]) * 1024
            total = info.get("MemTotal")
            avail = info.get("MemAvailable") or info.get("MemFree")
            if total and avail is not None:
                used = total - avail
                pct = _pct(used, total)
                mem.update(
                    {
                        "total": total,
                        "used": used,
                        "available": avail,
                        "total_label": _bytes_label(total),
                        "used_label": _bytes_label(used),
                        "available_label": _bytes_label(avail),
                        "percent": pct,
                        "level": _level(pct),
                    }
                )
        except OSError:
            pass
        try:
            load = os.getloadavg()
            cpu["load_1m"] = round(load[0], 2)
            cpu["load_5m"] = round(load[1], 2)
            cpu["load_15m"] = round(load[2], 2)
            cores = max(cpu["count"] or 1, 1)
            load_pct = round(100.0 * load[0] / cores, 1)
            cpu["percent"] = load_pct
            cpu["level"] = _level(load_pct)
        except (AttributeError, OSError):
            pass
    return mem, cpu


def _uptime_info() -> dict:
    elapsed = max(0, int(time.time() - _PROCESS_STARTED))
    days, rem = divmod(elapsed, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        label = f"{days}일 {hours}시간 {minutes}분"
    elif hours:
        label = f"{hours}시간 {minutes}분"
    else:
        label = f"{minutes}분 {seconds}초"
    return {
        "seconds": elapsed,
        "label": label,
        "started_at": datetime.fromtimestamp(_PROCESS_STARTED, tz=timezone.utc)
        .astimezone(KST)
        .strftime("%Y-%m-%d %H:%M:%S"),
    }


def _render_meta() -> dict:
    is_render = bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_SERVICE_ID")
        or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    )
    return {
        "is_render": is_render,
        "service_name": (
            os.environ.get("RENDER_SERVICE_NAME")
            or os.environ.get("RENDER_SERVICE_ID")
            or socket.gethostname()
        ),
        "instance_id": (os.environ.get("RENDER_INSTANCE_ID") or "")[:24] or "-",
        "region": os.environ.get("RENDER_REGION") or "-",
        "git_commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:7] or "-",
        "external_url": os.environ.get("RENDER_EXTERNAL_URL")
        or (
            f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}"
            if os.environ.get("RENDER_EXTERNAL_HOSTNAME")
            else "-"
        ),
    }


async def _db_status(db: AsyncSession | None) -> dict:
    if db is None:
        return {
            "ok": False,
            "label": "미연결",
            "latency_ms": None,
            "dialect": "-",
            "level": "unknown",
        }
    dialect = "-"
    try:
        dialect = db.bind.dialect.name if db.bind is not None else "-"
    except Exception:
        pass
    t0 = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        ms = round((time.perf_counter() - t0) * 1000, 1)
        level = "ok"
        if ms >= 500:
            level = "critical"
        elif ms >= 200:
            level = "warn"
        return {
            "ok": True,
            "label": "정상",
            "latency_ms": ms,
            "dialect": dialect,
            "level": level,
        }
    except Exception as exc:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "ok": False,
            "label": "오류",
            "latency_ms": ms,
            "dialect": dialect,
            "error": str(exc)[:120],
            "level": "critical",
        }


def _network_status() -> dict:
    """외부 HTTPS 통신 가능 여부(간이)."""
    host = "1.1.1.1"
    port = 443
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=2.0):
            ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "ok": True,
            "label": "정상",
            "target": f"{host}:{port}",
            "latency_ms": ms,
            "level": "ok" if ms < 400 else "warn",
        }
    except OSError as exc:
        return {
            "ok": False,
            "label": "불가",
            "target": f"{host}:{port}",
            "latency_ms": None,
            "error": str(exc)[:80],
            "level": "critical",
        }


def _overall(levels: list[str]) -> str:
    if "critical" in levels:
        return "critical"
    if "warn" in levels:
        return "warn"
    if all(x == "ok" for x in levels if x != "unknown"):
        return "ok"
    return "unknown"


async def collect_server_status(db: AsyncSession | None = None) -> dict:
    """Render 서버(배포 인스턴스) 상태만 수집. 로컬 PC 정보는 UI에서 제외."""
    meta = _render_meta()
    uptime = _uptime_info()
    db_st = await _db_status(db)
    net = _network_status()
    app = {
        "status": "ok",
        "label": "정상",
        "level": "ok",
        "desc": "Smart FMS 웹 서비스가 요청에 응답 중인지 확인합니다.",
    }
    levels = [app["level"], db_st.get("level") or "unknown", net.get("level") or "unknown"]

    disk = mem = cpu = None
    if meta.get("is_render"):
        disk = _disk_info()
        mem, cpu = _mem_cpu_info()
        # 프로세스(로컬 PC 체감용) 정보는 제외
        if isinstance(mem, dict):
            mem = {k: v for k, v in mem.items() if k != "process_rss_label"}
            mem["desc"] = "Render 인스턴스 메모리 사용 비율입니다. 높으면 서비스 지연이 날 수 있습니다."
        if isinstance(cpu, dict):
            cpu = {
                "percent": cpu.get("percent"),
                "count": cpu.get("count"),
                "level": cpu.get("level"),
                "desc": "Render 서버 CPU 사용률입니다. 지속 높음이면 점검이 필요합니다.",
            }
        if isinstance(disk, dict):
            disk["desc"] = (
                "메인 숫자는 Smart FMS 앱 실사용량입니다. "
                "수백 GB 사용/여유는 Render 공유 호스트 디스크 참고값이며 앱 점유가 아닙니다."
            )
        # 디스크 공유 FS 수치는 overall 경보에서 제외 (메모리·CPU만)
        levels.extend(
            [
                (mem or {}).get("level") or "unknown",
                (cpu or {}).get("level") or "unknown",
            ]
        )

    overall = _overall(levels)
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    db_st = {
        **db_st,
        "desc": "데이터베이스 연결 상태입니다. 숫자가 작을수록(ms) 응답이 빠릅니다.",
    }
    net = {
        **net,
        "desc": "서버에서 외부 네트워크로 나가는 통신이 가능한지 확인합니다.",
    }
    uptime = {
        **uptime,
        "desc": "이번 Render 인스턴스(웹 프로세스)가 켜진 뒤 경과 시간입니다.",
    }
    return {
        "as_of": now,
        "overall": overall,
        "overall_label": {
            "ok": "정상",
            "warn": "주의",
            "critical": "경고",
            "unknown": "확인중",
        }.get(overall, "확인중"),
        "overall_desc": "앱·DB·외부통신"
        + ("·서버 리소스" if meta.get("is_render") else "")
        + "을 종합한 상태입니다.",
        "is_render": bool(meta.get("is_render")),
        "env_label": "Render 서버" if meta.get("is_render") else "로컬(비서버)",
        "env_desc": (
            "Render에 배포된 웹 서버 상태를 표시합니다."
            if meta.get("is_render")
            else "현재 PC/로컬 실행입니다. 용량·CPU 등은 Render 배포 환경에서만 표시합니다."
        ),
        "disk": disk,
        "memory": mem,
        "cpu": cpu,
        "uptime": uptime,
        "db": {
            **db_st,
            "label": db_st.get("label") or "-",
        },
        "network": net,
        "render": meta,
        "app": app,
    }
