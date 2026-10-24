import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

import main


def account(user_id: str, role: str) -> dict[str, str]:
    return {
        "user_id": user_id,
        "email": f"{user_id}@test.local",
        "display_name": user_id,
        "role": role,
        "plan": "free",
        "approval_status": "approved",
    }


def request_for(value: dict[str, str] | None) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    if value is not None:
        request.state.account = value
    return request


PROCESSES = {
    "host_wide": True,
    "process_count": 1,
    "by_memory": [{"pid": 7, "name": "llama-server", "group": "Ollama", "rss_bytes": 1}],
    "by_cpu": [],
    "groups": [],
}


class AdminSystemMonitorApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.collector = Mock()
        self.collector.collect.return_value = PROCESSES
        self.governance = Mock()
        self.patches = [
            patch.object(main, "PROCESS_METRICS", self.collector),
            patch.object(main, "TENANT_GOVERNANCE", self.governance),
            patch.dict(main._process_audit_last, clear=True),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def call_processes(self, identity: dict[str, str] | None, limit: int = 15):
        return asyncio.run(main.admin_system_processes(request_for(identity), limit=limit))

    def test_non_admin_roles_are_refused_before_any_scan(self) -> None:
        for role in ("editor", "viewer"):
            with self.assertRaises(HTTPException) as denied:
                self.call_processes(account(f"user-{role}", role))
            self.assertEqual(denied.exception.status_code, 403)
        self.collector.collect.assert_not_called()
        self.governance.audit.assert_not_called()

    def test_auth_disabled_fallback_owner_is_refused(self) -> None:
        # Without a verified session request.state.account is unset and
        # current_account() would return the bootstrap owner.
        with self.assertRaises(HTTPException) as denied:
            self.call_processes(None)
        self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(HTTPException):
            asyncio.run(main.admin_system_metrics(request_for(None)))
        self.collector.collect.assert_not_called()

    def test_admin_and_owner_see_processes_and_viewing_is_audited_once_per_window(self) -> None:
        admin = account("admin-1", "admin")
        first = self.call_processes(admin, limit=5)
        self.call_processes(admin, limit=5)
        self.call_processes(account("owner-1", "owner"))

        self.assertEqual(json.loads(first.body)["by_memory"][0]["name"], "llama-server")
        self.assertEqual(first.headers["cache-control"], "no-store")
        self.collector.collect.assert_any_call(5)
        audited = [call.kwargs["actor_user_id"] for call in self.governance.audit.call_args_list]
        self.assertEqual(audited, ["admin-1", "owner-1"])
        self.assertEqual(
            self.governance.audit.call_args_list[0].kwargs["action"], "admin.system.processes.view"
        )

    def test_admin_middleware_still_guards_the_route_prefix(self) -> None:
        self.assertTrue(main.is_protected_path("/api/v1/admin/system-processes"))
        self.assertTrue(main.is_protected_path("/api/v1/admin/system-metrics"))


if __name__ == "__main__":
    unittest.main()
