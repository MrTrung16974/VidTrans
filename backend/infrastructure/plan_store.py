"""Billing plans and what each one may use, kept in the database.

Three tables:

``plans``              one row per plan: trial, free, creator, pro, enterprise.
``plan_features``      the catalog of features a plan can unlock.
``plan_entitlements``  one row per (plan, feature) with ``enabled`` 0/1. Every
                       pair has an explicit row, so trial and free carry their
                       denials in the data instead of relying on a missing row.
``app_menus``          web and mobile navigation entries: label, order, on/off,
                       lowest role and required feature (domain/app_menus.py).

Start-up inserts the defaults from domain/plan_entitlements.py for any row that
is missing and never overwrites existing rows, so an operator's change in the
database survives restarts and deploys. A row that is still missing at check
time counts as denied.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from domain.app_menus import DEFAULT_MENUS, MenuConfigError, validate_menu_update
from domain.plan_entitlements import DEFAULT_PLAN_FEATURES, DEFAULT_PLANS, FEATURES
from infrastructure.database import DatabaseTarget, connect, prepare_target

# Several API workers and the job worker each hold a cache; a change made in
# the database reaches all of them within this many seconds.
CACHE_SECONDS = 30.0


class PlanStoreError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanStore:
    def __init__(self, database_path: DatabaseTarget, cache_seconds: float = CACHE_SECONDS) -> None:
        self.database_path = prepare_target(database_path)
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._cache: tuple[float, dict[str, dict[str, Any]], dict[str, frozenset[str]]] | None = None
        self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = connect(self.database_path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        now = utc_now()
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    plan_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    price_vnd INTEGER,
                    monthly_minutes INTEGER,
                    is_paid INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS plan_features (
                    feature_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS plan_entitlements (
                    plan_key TEXT NOT NULL REFERENCES plans(plan_key) ON DELETE CASCADE,
                    feature_key TEXT NOT NULL REFERENCES plan_features(feature_key) ON DELETE CASCADE,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (plan_key, feature_key)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS app_menus (
                    platform TEXT NOT NULL,
                    menu_key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    short_label TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    required_feature TEXT REFERENCES plan_features(feature_key) ON DELETE SET NULL,
                    min_role TEXT NOT NULL,
                    is_enabled INTEGER NOT NULL CHECK (is_enabled IN (0, 1)),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (platform, menu_key)
                )
            """)
            for key, plan in DEFAULT_PLANS.items():
                db.execute(
                    "INSERT OR IGNORE INTO plans(plan_key,name,price_vnd,monthly_minutes,is_paid,sort_order,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (key, plan["name"], plan["price_vnd"], plan["monthly_minutes"], int(plan["is_paid"]),
                     plan["sort_order"], now, now),
                )
            for key, (name, description) in FEATURES.items():
                db.execute(
                    "INSERT OR IGNORE INTO plan_features(feature_key,name,description,created_at) VALUES(?,?,?,?)",
                    (key, name, description, now),
                )
            for plan_key in DEFAULT_PLANS:
                enabled = DEFAULT_PLAN_FEATURES.get(plan_key, frozenset())
                for feature_key in FEATURES:
                    db.execute(
                        "INSERT OR IGNORE INTO plan_entitlements(plan_key,feature_key,enabled,updated_at) "
                        "VALUES(?,?,?,?)",
                        (plan_key, feature_key, int(feature_key in enabled), now),
                    )
            for platform, key, label, short_label, order, feature, min_role in DEFAULT_MENUS:
                db.execute(
                    "INSERT OR IGNORE INTO app_menus(platform,menu_key,label,short_label,sort_order,"
                    "required_feature,min_role,is_enabled,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (platform, key, label, short_label, order, feature, min_role, 1, now),
                )

    def _load(self) -> tuple[dict[str, dict[str, Any]], dict[str, frozenset[str]]]:
        with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < self.cache_seconds:
                return self._cache[1], self._cache[2]
            with self._db() as db:
                plan_rows = db.execute("SELECT * FROM plans ORDER BY sort_order, plan_key").fetchall()
                grant_rows = db.execute(
                    "SELECT plan_key, feature_key FROM plan_entitlements WHERE enabled=1"
                ).fetchall()
            plans = {
                str(row["plan_key"]): {
                    "key": str(row["plan_key"]),
                    "name": str(row["name"]),
                    "price_vnd": row["price_vnd"],
                    "monthly_minutes": row["monthly_minutes"],
                    "is_paid": bool(row["is_paid"]),
                    "sort_order": int(row["sort_order"]),
                }
                for row in plan_rows
            }
            grants: dict[str, set[str]] = {key: set() for key in plans}
            for row in grant_rows:
                grants.setdefault(str(row["plan_key"]), set()).add(str(row["feature_key"]))
            features = {key: frozenset(values) for key, values in grants.items()}
            self._cache = (time.monotonic(), plans, features)
            return plans, features

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    # EntitlementSource for domain.plan_entitlements
    def features_for(self, plan_key: str) -> frozenset[str]:
        return self._load()[1].get(plan_key, frozenset())

    def plan(self, plan_key: str) -> dict[str, Any] | None:
        plan = self._load()[0].get(plan_key)
        return dict(plan) if plan else None

    def plans(self) -> list[dict[str, Any]]:
        """Every plan with its enabled features, cheapest first."""

        plans, features = self._load()
        return [{**plan, "features": sorted(features.get(key, frozenset()))} for key, plan in plans.items()]

    def plan_keys(self) -> frozenset[str]:
        return frozenset(self._load()[0])

    def matrix(self) -> list[dict[str, Any]]:
        """Every (plan, feature) row, for the admin screen and audits."""

        with self._db() as db:
            rows = db.execute("""
                SELECT e.plan_key, e.feature_key, f.name AS feature_name, e.enabled, e.updated_at
                FROM plan_entitlements e
                JOIN plans p ON p.plan_key = e.plan_key
                JOIN plan_features f ON f.feature_key = e.feature_key
                ORDER BY p.sort_order, e.feature_key
            """).fetchall()
        return [
            {
                "plan_key": str(row["plan_key"]),
                "feature_key": str(row["feature_key"]),
                "feature_name": str(row["feature_name"]),
                "enabled": bool(row["enabled"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def set_entitlement(self, plan_key: str, feature_key: str, enabled: bool) -> None:
        if plan_key not in self.plan_keys():
            raise PlanStoreError("Gói dịch vụ không hợp lệ")
        if feature_key not in FEATURES:
            raise PlanStoreError("Tính năng không hợp lệ")
        now = utc_now()
        with self._db() as db:
            updated = db.execute(
                "UPDATE plan_entitlements SET enabled=?, updated_at=? WHERE plan_key=? AND feature_key=?",
                (int(enabled), now, plan_key, feature_key),
            ).rowcount
            if not updated:
                db.execute(
                    "INSERT INTO plan_entitlements(plan_key,feature_key,enabled,updated_at) VALUES(?,?,?,?)",
                    (plan_key, feature_key, int(enabled), now),
                )
        self.invalidate()

    def menus(self, platform: str | None = None) -> list[dict[str, Any]]:
        """Menu rows in display order; every platform when ``platform`` is None."""

        query = "SELECT * FROM app_menus"
        values: list[Any] = []
        if platform is not None:
            query += " WHERE platform=?"
            values.append(platform)
        query += " ORDER BY platform, sort_order, menu_key"
        with self._db() as db:
            rows = db.execute(query, values).fetchall()
        return [
            {
                "platform": str(row["platform"]),
                "menu_key": str(row["menu_key"]),
                "label": str(row["label"]),
                "short_label": str(row["short_label"]),
                "sort_order": int(row["sort_order"]),
                "required_feature": row["required_feature"],
                "min_role": str(row["min_role"]),
                "is_enabled": bool(row["is_enabled"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def update_menu(self, platform: str, menu_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        clean = validate_menu_update(platform, menu_key, fields)
        with self._db() as db:
            updated = db.execute(
                "UPDATE app_menus SET label=?, short_label=?, sort_order=?, required_feature=?, min_role=?, "
                "is_enabled=?, updated_at=? WHERE platform=? AND menu_key=?",
                (clean["label"], clean["short_label"], clean["sort_order"], clean["required_feature"],
                 clean["min_role"], int(clean["is_enabled"]), utc_now(), platform, menu_key),
            ).rowcount
        if not updated:
            raise MenuConfigError("Menu không tồn tại")
        return next(menu for menu in self.menus(platform) if menu["menu_key"] == menu_key)
