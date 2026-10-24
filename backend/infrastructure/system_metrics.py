"""Point-in-time host, container and Ollama metrics for the admin monitor.

Reads Linux ``/proc`` and cgroup v2 files directly so no extra dependency is
needed. Inside Docker, ``/proc/meminfo``, ``/proc/stat`` and ``/proc/loadavg``
describe the whole host, while the cgroup files describe this container only.
Every reader degrades to ``None`` on platforms (macOS) or kernels that lack
the file, so the endpoint never fails because one source is missing.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

_CPU_SAMPLE_SECONDS = 0.25
# A previous /proc/stat reading older than this gives a stale average.
_CPU_SAMPLE_MAX_AGE_SECONDS = 60.0

_direct_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    text = _read(path)
    try:
        return int(text.strip()) if text is not None else None
    except ValueError:  # "max" means no limit
        return None


def _key_values(text: str | None) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in (text or "").splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2:
            try:
                values[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return values


def _percent(part: float | None, whole: float | None) -> float | None:
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


class SystemMetricsCollector:
    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        disk_paths: dict[str, Path] | None = None,
        ollama_url: Callable[[], str] | None = None,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._proc = proc_root
        self._cgroup = cgroup_root
        self._disk_paths = disk_paths or {}
        self._ollama_url = ollama_url
        self._open = opener or _direct_open
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_cpu: tuple[float, list[int]] | None = None
        self._last_container_cpu: tuple[float, int] | None = None

    def collect(self) -> dict[str, Any]:
        return {
            "collected_at": time.time(),
            "host": self._host(),
            "cpu": self._cpu(),
            "memory": self._memory(),
            "pressure": self._pressure(),
            "container": self._container(),
            "process": self._process(),
            "disks": self._disks(),
            "ollama": self._ollama(),
        }

    def _host(self) -> dict[str, Any]:
        uptime_text = _read(self._proc / "uptime")
        loadavg_text = _read(self._proc / "loadavg")
        load = [float(value) for value in loadavg_text.split()[:3]] if loadavg_text else None
        if load is None and hasattr(os, "getloadavg"):
            load = [round(value, 2) for value in os.getloadavg()]
        return {
            "hostname": socket.gethostname(),
            "uptime_seconds": float(uptime_text.split()[0]) if uptime_text else None,
            "load_average": load,
            "cpu_count": os.cpu_count() or 1,
        }

    def _cpu_times(self) -> list[int] | None:
        text = _read(self._proc / "stat")
        if not text or not text.startswith("cpu "):
            return None
        return [int(value) for value in text.splitlines()[0].split()[1:]]

    def _cpu(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            current = self._cpu_times()
            if current is None:
                return {"usage_percent": None, "iowait_percent": None, "steal_percent": None}
            previous = self._last_cpu
            if previous is None or now - previous[0] > _CPU_SAMPLE_MAX_AGE_SECONDS:
                self._sleep(_CPU_SAMPLE_SECONDS)
                previous = (now, current)
                now = self._clock()
                current = self._cpu_times() or current
            self._last_cpu = (now, current)
        deltas = [after - before for after, before in zip(current, previous[1])]
        total = sum(deltas)
        # user nice system idle iowait irq softirq steal ...
        idle = deltas[3] + (deltas[4] if len(deltas) > 4 else 0)
        return {
            "usage_percent": _percent(total - idle, total),
            "iowait_percent": _percent(deltas[4], total) if len(deltas) > 4 else None,
            "steal_percent": _percent(deltas[7], total) if len(deltas) > 7 else None,
        }

    def _memory(self) -> dict[str, Any]:
        info = _key_values(_read(self._proc / "meminfo"))
        if not info:
            return {}
        kib = 1024
        total = info.get("MemTotal", 0) * kib
        available = info.get("MemAvailable", 0) * kib
        swap_total = info.get("SwapTotal", 0) * kib
        swap_used = swap_total - info.get("SwapFree", 0) * kib
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": total - available,
            "used_percent": _percent(total - available, total),
            "cached_bytes": (info.get("Cached", 0) + info.get("Buffers", 0)) * kib,
            "swap_total_bytes": swap_total,
            "swap_used_bytes": swap_used,
            "swap_used_percent": _percent(swap_used, swap_total),
        }

    def _pressure(self) -> dict[str, Any]:
        """Linux PSI: share of time tasks stalled waiting for a resource."""

        pressure: dict[str, Any] = {}
        for resource in ("cpu", "memory", "io"):
            text = _read(self._proc / "pressure" / resource)
            if not text:
                continue
            for line in text.splitlines():
                kind, *fields = line.split()
                values = dict(field.split("=", 1) for field in fields)
                if kind == "some":
                    pressure[resource] = float(values.get("avg10", 0.0))
        return pressure

    def _container(self) -> dict[str, Any] | None:
        current = _read_int(self._cgroup / "memory.current")
        if current is None:
            return None
        limit = _read_int(self._cgroup / "memory.max")
        events = _key_values(_read(self._cgroup / "memory.events"))
        usage_usec = _key_values(_read(self._cgroup / "cpu.stat")).get("usage_usec")
        cpu_percent = None
        if usage_usec is not None:
            now = self._clock()
            with self._lock:
                previous = self._last_container_cpu
                self._last_container_cpu = (now, usage_usec)
            if previous and now > previous[0]:
                # 100% = one full core, like `docker stats`.
                cpu_percent = round((usage_usec - previous[1]) / ((now - previous[0]) * 1e4), 1)
        return {
            "memory_bytes": current,
            "memory_limit_bytes": limit,
            "memory_used_percent": _percent(current, limit),
            "swap_bytes": _read_int(self._cgroup / "memory.swap.current"),
            "oom_kill_count": events.get("oom_kill", 0),
            "cpu_percent": cpu_percent,
        }

    def _process(self) -> dict[str, Any]:
        status = _key_values(_read(self._proc / "self" / "status"))
        return {
            "pid": os.getpid(),
            "rss_bytes": status["VmRSS"] * 1024 if "VmRSS" in status else None,
            "threads": status.get("Threads"),
        }

    def _disks(self) -> list[dict[str, Any]]:
        disks = []
        for label, path in self._disk_paths.items():
            try:
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            disks.append(
                {
                    "label": label,
                    "path": str(path),
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "used_percent": _percent(usage.used, usage.total),
                }
            )
        return disks

    def _ollama(self) -> dict[str, Any] | None:
        if self._ollama_url is None:
            return None
        url = self._ollama_url()
        request = urllib.request.Request(f"{url}/api/ps", headers={"Accept": "application/json"})
        try:
            with self._open(request, timeout=1.5) as response:
                payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except Exception:
            return {"reachable": False, "models": []}
        return {
            "reachable": True,
            "models": [
                {
                    "name": str(model.get("name", "")),
                    "size_bytes": int(model.get("size") or 0),
                    "vram_bytes": int(model.get("size_vram") or 0),
                    "expires_at": model.get("expires_at"),
                }
                for model in payload.get("models", [])
            ],
        }


_PROCESS_GROUPS = (
    ("ollama", "Ollama"),
    ("llama-server", "Ollama"),
    ("chrom", "Trình duyệt"),
    ("xvfb", "Trình duyệt"),
    ("x11vnc", "Trình duyệt"),
    ("websockify", "Trình duyệt"),
    ("postgres", "Postgres"),
    ("redis", "Redis"),
    ("nginx", "Nginx"),
    ("dockerd", "Docker"),
    ("containerd", "Docker"),
)


def _process_group(name: str, same_container: bool, cgroup: str | None) -> str:
    if same_container:
        return "VidTrans backend"
    lowered = name.lower()
    for marker, group in _PROCESS_GROUPS:
        if marker in lowered:
            return group
    return "Container khác" if cgroup and "docker" in cgroup else "Hệ thống"


def _safe_process_name(name: str) -> str:
    # The kernel's 15-character ``comm`` is set by the process itself; keep it
    # printable so it cannot smuggle control characters into the admin UI.
    return "".join(char if char.isprintable() else "?" for char in name)[:32]


class HostProcessCollector:
    """Top processes by memory and CPU, from the host ``/proc`` when mounted.

    Only the short process name (``comm``) is reported. Command lines,
    environments and working directories can carry credentials, so they are
    never read.
    """

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        self_proc_root: Path = Path("/proc"),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        cache_seconds: float = 3.0,
    ) -> None:
        self._proc = proc_root
        self._self_proc = self_proc_root
        self._clock = clock
        self._sleep = sleep
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._previous: tuple[float, dict[int, int]] | None = None
        self._cached: tuple[float, dict[str, Any]] | None = None
        try:
            self._ticks = os.sysconf("SC_CLK_TCK")
        except (AttributeError, OSError, ValueError):
            self._ticks = 100
        try:
            self._page_size = os.sysconf("SC_PAGE_SIZE")
        except (AttributeError, OSError, ValueError):
            self._page_size = 4096

    @property
    def host_wide(self) -> bool:
        return self._proc != self._self_proc

    def collect(self, limit: int = 15) -> dict[str, Any]:
        # One scan at a time, and repeated polls within the cache window reuse
        # it, so a refreshing page cannot turn this into a CPU drain.
        with self._lock:
            now = self._clock()
            if self._cached and now - self._cached[0] < self._cache_seconds:
                snapshot = self._cached[1]
            else:
                snapshot = self._scan()
                self._cached = (self._clock(), snapshot)
        processes = snapshot["processes"]
        return {
            "host_wide": self.host_wide,
            "process_count": len(processes),
            "by_memory": sorted(processes, key=lambda item: item["rss_bytes"], reverse=True)[:limit],
            "by_cpu": sorted(processes, key=lambda item: item["cpu_percent"], reverse=True)[:limit],
            "groups": snapshot["groups"],
        }

    def _cpu_ticks(self) -> dict[int, int]:
        ticks = {}
        for pid, fields in self._stats().items():
            try:
                ticks[pid] = int(fields[12]) + int(fields[13])
            except (IndexError, ValueError):
                continue
        return ticks

    def _stats(self) -> dict[int, list[str]]:
        stats: dict[int, list[str]] = {}
        try:
            entries = list(self._proc.iterdir())
        except OSError:
            return stats
        for entry in entries:
            if not entry.name.isdigit():
                continue
            text = _read(entry / "stat")
            if not text or ")" not in text:
                continue
            # Fields after "(comm)": state is index 0, utime 11, stime 12,
            # starttime 19, rss (pages) 21.
            stats[int(entry.name)] = [text[text.index("(") + 1 : text.rindex(")")], *text[text.rindex(")") + 2 :].split()]
        return stats

    def _own_cgroup(self, stats: dict[int, list[str]]) -> str | None:
        """This container's cgroup as the scanned ``/proc`` spells it.

        Inside a cgroup namespace ``/proc/self/cgroup`` reads ``0::/``, while
        the host ``/proc`` shows the full path, so find this process in the
        host table (same in-namespace PID and start time) and use its entry.
        """

        if not self.host_wide:
            return _read(self._proc / "self" / "cgroup")
        own = _read(self._self_proc / "self" / "stat")
        if not own or ")" not in own:
            return None
        own_start = own[own.rindex(")") + 2 :].split()[19]
        own_pid = str(os.getpid())
        for pid, fields in stats.items():
            if len(fields) <= 20 or fields[20] != own_start:
                continue
            status = _read(self._proc / str(pid) / "status") or ""
            nspids = next((line.split()[1:] for line in status.splitlines() if line.startswith("NSpid:")), [])
            if nspids and nspids[-1] == own_pid:
                return _read(self._proc / str(pid) / "cgroup")
        return None

    def _scan(self) -> dict[str, Any]:
        now = self._clock()
        previous = self._previous
        if previous is None or now - previous[0] > _CPU_SAMPLE_MAX_AGE_SECONDS:
            previous = (now, self._cpu_ticks())
            self._sleep(_CPU_SAMPLE_SECONDS)
            now = self._clock()
        stats = self._stats()
        elapsed = max(now - previous[0], 1e-6)
        mem_total = _key_values(_read(self._proc / "meminfo")).get("MemTotal", 0) * 1024
        uptime_text = _read(self._proc / "uptime")
        uptime = float(uptime_text.split()[0]) if uptime_text else None
        own_cgroup = self._own_cgroup(stats)

        processes = []
        ticks_now: dict[int, int] = {}
        for pid, fields in stats.items():
            name, rest = fields[0], fields[1:]
            try:
                cpu_ticks = int(rest[11]) + int(rest[12])
                rss = int(rest[21]) * self._page_size
                start = int(rest[19]) / self._ticks
            except (IndexError, ValueError):
                continue
            ticks_now[pid] = cpu_ticks
            if rss <= 0:  # kernel threads
                continue
            cpu_delta = cpu_ticks - previous[1].get(pid, cpu_ticks)
            cgroup = _read(self._proc / str(pid) / "cgroup")
            same_container = bool(own_cgroup) and cgroup == own_cgroup
            processes.append(
                {
                    "pid": pid,
                    "name": _safe_process_name(name),
                    "group": _process_group(name, same_container, cgroup),
                    "rss_bytes": rss,
                    "memory_percent": _percent(rss, mem_total),
                    # 100% = one full core, like `top`.
                    "cpu_percent": round(max(0, cpu_delta) / self._ticks / elapsed * 100, 1),
                    "uptime_seconds": round(uptime - start) if uptime is not None else None,
                }
            )
        self._previous = (now, ticks_now)

        groups: dict[str, dict[str, Any]] = {}
        for process in processes:
            group = groups.setdefault(
                process["group"], {"name": process["group"], "rss_bytes": 0, "cpu_percent": 0.0, "count": 0}
            )
            group["rss_bytes"] += process["rss_bytes"]
            group["cpu_percent"] = round(group["cpu_percent"] + process["cpu_percent"], 1)
            group["count"] += 1
        return {
            "processes": processes,
            "groups": sorted(groups.values(), key=lambda item: item["rss_bytes"], reverse=True),
        }
