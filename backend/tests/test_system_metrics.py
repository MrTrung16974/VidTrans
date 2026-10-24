import io
import json
import tempfile
import unittest
from pathlib import Path

from infrastructure.system_metrics import HostProcessCollector, SystemMetricsCollector


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class SystemMetricsCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.proc = root / "proc"
        self.cgroup = root / "cgroup"
        (self.proc / "self").mkdir(parents=True)
        (self.proc / "pressure").mkdir()
        self.cgroup.mkdir()
        self.write_proc_stat(user=100, idle=900)
        (self.proc / "meminfo").write_text(
            "MemTotal:       8000000 kB\n"
            "MemFree:         500000 kB\n"
            "MemAvailable:   2000000 kB\n"
            "Buffers:         100000 kB\n"
            "Cached:          700000 kB\n"
            "SwapTotal:      4000000 kB\n"
            "SwapFree:       1000000 kB\n"
        )
        (self.proc / "loadavg").write_text("3.50 2.25 1.00 2/900 12345\n")
        (self.proc / "uptime").write_text("5000.25 12000.00\n")
        (self.proc / "self" / "status").write_text("VmRSS:\t  1048576 kB\nThreads:\t42\n")
        (self.proc / "pressure" / "memory").write_text(
            "some avg10=12.50 avg60=8.00 avg300=3.00 total=1\n"
            "full avg10=4.00 avg60=2.00 avg300=1.00 total=1\n"
        )
        (self.cgroup / "memory.current").write_text("1073741824\n")
        (self.cgroup / "memory.max").write_text("max\n")
        (self.cgroup / "memory.events").write_text("low 0\nhigh 0\nmax 3\noom 2\noom_kill 2\n")
        (self.cgroup / "cpu.stat").write_text("usage_usec 1000000\n")
        self.clock = FakeClock()
        self.sleeps: list[float] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_proc_stat(self, *, user: int, idle: int) -> None:
        (self.proc / "stat").write_text(f"cpu  {user} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 0 0 1 0 0 0 0 0 0\n")

    def collector(self, **kwargs) -> SystemMetricsCollector:
        def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            self.clock.now += seconds
            self.write_proc_stat(user=150, idle=950)

        return SystemMetricsCollector(
            proc_root=self.proc,
            cgroup_root=self.cgroup,
            clock=self.clock,
            sleep=sleep,
            **kwargs,
        )

    def test_reads_host_memory_swap_and_pressure(self) -> None:
        metrics = self.collector().collect()

        memory = metrics["memory"]
        self.assertEqual(memory["total_bytes"], 8000000 * 1024)
        self.assertEqual(memory["used_bytes"], 6000000 * 1024)
        self.assertEqual(memory["used_percent"], 75.0)
        self.assertEqual(memory["swap_used_percent"], 75.0)
        self.assertEqual(metrics["host"]["load_average"], [3.5, 2.25, 1.0])
        self.assertEqual(metrics["host"]["uptime_seconds"], 5000.25)
        self.assertEqual(metrics["pressure"], {"memory": 12.5})
        self.assertEqual(metrics["process"]["rss_bytes"], 1024 ** 3)
        self.assertEqual(metrics["process"]["threads"], 42)

    def test_first_cpu_reading_samples_and_later_readings_use_the_previous_call(self) -> None:
        collector = self.collector()

        first = collector.collect()["cpu"]
        self.assertEqual(len(self.sleeps), 1)
        self.assertEqual(first["usage_percent"], 50.0)

        self.clock.now += 5
        self.write_proc_stat(user=240, idle=960)
        second = collector.collect()["cpu"]
        self.assertEqual(len(self.sleeps), 1)
        self.assertEqual(second["usage_percent"], 90.0)

    def test_container_reports_oom_kills_and_unlimited_memory(self) -> None:
        collector = self.collector()
        collector.collect()
        self.clock.now += 2
        (self.cgroup / "cpu.stat").write_text("usage_usec 3000000\n")

        container = collector.collect()["container"]

        self.assertEqual(container["memory_bytes"], 1024 ** 3)
        self.assertIsNone(container["memory_limit_bytes"])
        self.assertIsNone(container["memory_used_percent"])
        self.assertEqual(container["oom_kill_count"], 2)
        self.assertEqual(container["cpu_percent"], 100.0)

    def test_missing_sources_degrade_instead_of_failing(self) -> None:
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        metrics = SystemMetricsCollector(proc_root=empty, cgroup_root=empty).collect()

        self.assertEqual(metrics["memory"], {})
        self.assertIsNone(metrics["cpu"]["usage_percent"])
        self.assertIsNone(metrics["container"])
        self.assertIsNone(metrics["ollama"])

    def test_reports_disk_usage_and_loaded_ollama_models(self) -> None:
        payload = {"models": [{"name": "translategemma:4b", "size": 2900000000, "size_vram": 0}]}

        def opener(request, timeout):
            self.assertTrue(request.full_url.endswith("/api/ps"))
            return io.BytesIO(json.dumps(payload).encode())

        metrics = self.collector(
            disk_paths={"work": Path(self.tmp.name)},
            ollama_url=lambda: "http://ollama:11434",
            opener=opener,
        ).collect()

        self.assertEqual(metrics["disks"][0]["label"], "work")
        self.assertGreater(metrics["disks"][0]["total_bytes"], 0)
        self.assertEqual(
            metrics["ollama"]["models"][0],
            {"name": "translategemma:4b", "size_bytes": 2900000000, "vram_bytes": 0, "expires_at": None},
        )

    def test_unreachable_ollama_is_reported(self) -> None:
        def opener(request, timeout):
            raise OSError("connection refused")

        metrics = self.collector(ollama_url=lambda: "http://ollama:11434", opener=opener).collect()

        self.assertEqual(metrics["ollama"], {"reachable": False, "models": []})


class HostProcessCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = Path(self.tmp.name) / "proc"
        self.proc.mkdir()
        (self.proc / "meminfo").write_text("MemTotal: 8000000 kB\n")
        (self.proc / "uptime").write_text("1000.00 0\n")
        self.clock = FakeClock()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add_process(self, pid: int, name: str, *, cpu_ticks: int, rss_pages: int, cgroup: str = "0::/system.slice/docker-abc.scope\n") -> None:
        folder = self.proc / str(pid)
        folder.mkdir(exist_ok=True)
        # state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt
        # utime stime cutime cstime priority nice threads itreal starttime vsize rss
        fields = ["S", "1", "1", "1", "0", "-1", "0", "0", "0", "0", "0",
                  str(cpu_ticks), "0", "0", "0", "20", "0", "1", "0", "50000", "0", str(rss_pages)]
        (folder / "stat").write_text(f"{pid} ({name}) {' '.join(fields)}\n")
        (folder / "cgroup").write_text(cgroup)
        (folder / "cmdline").write_text("secret-token=abc")

    def collector(self) -> HostProcessCollector:
        def sleep(seconds: float) -> None:
            self.clock.now += 1.0
            self.add_process(10, "llama-server", cpu_ticks=300, rss_pages=1000)

        return HostProcessCollector(
            proc_root=self.proc, self_proc_root=self.proc, clock=self.clock, sleep=sleep, cache_seconds=0
        )

    def test_ranks_processes_and_reports_only_their_short_names(self) -> None:
        self.add_process(10, "llama-server", cpu_ticks=100, rss_pages=1000)
        self.add_process(20, "chromium", cpu_ticks=5, rss_pages=200)
        self.add_process(30, "kworker/0:1", cpu_ticks=0, rss_pages=0)
        self.add_process(40, "evil\x1bname", cpu_ticks=0, rss_pages=10, cgroup="0::/init.scope\n")

        result = self.collector().collect(limit=2)

        self.assertEqual([item["name"] for item in result["by_memory"]], ["llama-server", "chromium"])
        top = result["by_cpu"][0]
        self.assertEqual(top["name"], "llama-server")
        self.assertEqual(top["group"], "Ollama")
        self.assertEqual(top["cpu_percent"], 200.0)  # 200 ticks in 1 s at 100 Hz = two cores
        self.assertEqual(result["process_count"], 3)  # kernel thread skipped
        self.assertFalse(result["host_wide"])
        names = {item["name"] for item in self.collector().collect(limit=10)["by_memory"]}
        self.assertIn("evil?name", names)
        self.assertNotIn("secret", json.dumps(result))
        groups = {group["name"]: group for group in result["groups"]}
        self.assertEqual(groups["Ollama"]["count"], 1)
        self.assertEqual(groups["Hệ thống"]["count"], 1)

    def test_repeated_polls_within_the_cache_window_reuse_one_scan(self) -> None:
        self.add_process(10, "llama-server", cpu_ticks=100, rss_pages=1000)
        collector = HostProcessCollector(
            proc_root=self.proc, self_proc_root=self.proc, clock=self.clock, sleep=lambda _s: None, cache_seconds=3
        )
        collector.collect()
        self.add_process(50, "postgres", cpu_ticks=0, rss_pages=10)
        self.assertEqual(collector.collect()["process_count"], 1)
        self.clock.now += 5
        self.assertEqual(collector.collect()["process_count"], 2)


if __name__ == "__main__":
    unittest.main()
