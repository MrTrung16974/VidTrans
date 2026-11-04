from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from infrastructure.browser_power import BLANK_URL, BrowserPower


class FakeDriver:
    def __init__(self, urls: list[str]) -> None:
        self.urls = urls
        self.parks = 0
        self.restores: list[list[str]] = []

    def list_urls(self) -> list[str]:
        return list(self.urls)

    def park(self) -> list[str]:
        self.parks += 1
        parked = [url for url in self.urls if url != BLANK_URL]
        self.urls = [BLANK_URL for _ in self.urls]
        return parked

    def restore(self, urls: list[str]) -> None:
        self.restores.append(list(urls))
        self.urls = list(urls)


class BrowserPowerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Path(self.directory.name) / "power" / "tiktok.json"
        self.now = 0.0
        self.busy = False

    def power(self, driver: FakeDriver) -> BrowserPower:
        return BrowserPower(
            "tiktok", "https://start.example/", self.settings, driver,
            idle_seconds=600, is_busy=lambda: self.busy, clock=lambda: self.now,
        )

    def test_idle_browser_is_parked_and_wakes_on_its_previous_page(self) -> None:
        driver = FakeDriver(["https://studio.example/upload"])
        power = self.power(driver)
        self.now = 599
        self.assertFalse(power.sweep(), "not idle long enough yet")
        self.now = 600
        self.assertTrue(power.sweep())
        self.assertFalse(power.status()["awake"])
        status = power.heartbeat()
        self.assertTrue(status["awake"])
        self.assertEqual(driver.restores, [["https://studio.example/upload"]])

    def test_heartbeat_keeps_an_open_page_awake(self) -> None:
        driver = FakeDriver(["https://studio.example/"])
        power = self.power(driver)
        self.now = 500
        power.heartbeat()
        self.now = 1000
        self.assertFalse(power.sweep())
        self.now = 1100
        self.assertTrue(power.sweep())

    def test_busy_browser_is_never_parked(self) -> None:
        driver = FakeDriver(["https://studio.example/"])
        power = self.power(driver)
        self.busy = True
        self.now = 10_000
        self.assertFalse(power.sweep())
        self.assertEqual(driver.parks, 0)

    def test_a_blank_browser_wakes_on_the_start_page(self) -> None:
        driver = FakeDriver([BLANK_URL])
        self.power(driver).heartbeat()
        self.assertEqual(driver.restores, [["https://start.example/"]])

    def test_owner_switch_off_parks_and_persists_until_switched_on(self) -> None:
        driver = FakeDriver(["https://studio.example/"])
        power = self.power(driver)
        status = power.set_enabled(False)
        self.assertEqual((status["enabled"], status["awake"]), (False, False))
        self.assertFalse(power.heartbeat()["awake"], "an open page does not wake a switched-off browser")
        # Automation opened a page meanwhile: the next sweep parks it right away.
        driver.urls = ["https://studio.example/upload"]
        self.assertTrue(power.sweep())
        restarted = self.power(driver)
        self.assertFalse(restarted.enabled, "the switch survives a restart")
        self.assertTrue(restarted.set_enabled(True)["awake"])
        self.assertTrue(self.power(driver).enabled)

    def test_unreachable_sidecar_reports_unavailable(self) -> None:
        class Down(FakeDriver):
            def list_urls(self) -> list[str]:
                raise OSError("connection refused")

        power = self.power(Down([]))
        status = power.heartbeat()
        self.assertEqual((status["available"], status["awake"]), (False, False))
        self.now = 10_000
        self.assertFalse(power.sweep())


if __name__ == "__main__":
    unittest.main()
