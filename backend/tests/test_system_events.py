import asyncio
import unittest

from infrastructure.system_events import SystemMetricsHub


class SystemMetricsHubTests(unittest.TestCase):
    def test_collects_only_while_subscribed_and_shares_one_sample(self) -> None:
        calls = []

        def build() -> dict:
            calls.append(1)
            return {"type": "system", "n": len(calls)}

        async def scenario() -> None:
            hub = SystemMetricsHub(build, interval=0.05)
            await hub.start()
            await asyncio.sleep(0.1)
            self.assertEqual(calls, [], "nothing is collected without subscribers")
            first, second = hub.subscribe(), hub.subscribe()
            one = await asyncio.wait_for(first.get(), 1)
            two = await asyncio.wait_for(second.get(), 1)
            self.assertIs(one, two)
            later = await asyncio.wait_for(first.get(), 1)
            self.assertGreater(later["n"], one["n"])
            hub.unsubscribe(first)
            hub.unsubscribe(second)
            await asyncio.sleep(0.1)
            settled = len(calls)
            await asyncio.sleep(0.2)
            self.assertLessEqual(len(calls), settled + 1)
            await hub.stop()

        asyncio.run(scenario())

    def test_failed_collection_is_reported_and_slow_clients_keep_latest(self) -> None:
        def build() -> dict:
            raise OSError("no /proc")

        async def scenario() -> None:
            hub = SystemMetricsHub(build, interval=0.02)
            await hub.start()
            queue = hub.subscribe()
            await asyncio.sleep(0.1)
            self.assertEqual(queue.qsize(), 1)
            self.assertIn("error", queue.get_nowait())
            await hub.stop()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
