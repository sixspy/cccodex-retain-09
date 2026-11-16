"""缩天（缩短保留天数）闭环：作废重扫路径 + 串行锁写 + 版本核对拦截。"""

import os
import sqlite3
import threading

from app.db import locked_db
from app.services import ShrinkConflict, shrink_policy


def _scan(authed, as_of="2026-11-16"):
    r = authed.post("/api/scan-jobs", json={"as_of_date": as_of})
    assert r.status_code == 200
    return r.json()["job"]


def _judgments(authed, domain=None, status=None):
    params = {}
    if status:
        params["status"] = status
    items = authed.get("/api/judgments", params=params).json()["items"]
    if domain:
        items = [j for j in items if j["domain_code"] == domain]
    return items


def _shrink(authed, domain, keep_days, base_version):
    return authed.post(
        f"/api/policies/{domain}/shrink",
        json={"keep_days": keep_days, "base_version": base_version},
    )


def test_shrink_void_rescan_chain_three_views_move_together(authed):
    _scan(authed)
    old_orders = _judgments(authed, "orders", "待执行")
    assert old_orders, "缩天前应有 orders 待执行单"
    assert all(j["policy_version"] == 1 and j["keep_days"] == 7 for j in old_orders)

    # 缩天：orders 7天 → 3天，基于现行 v1
    r = _shrink(authed, "orders", 3, 1)
    assert r.status_code == 200
    body = r.json()
    assert body["to_version"] == 2
    assert body["voided"] == len(old_orders)

    # 三处同动之一：策略队列 —— v1 历史、v2 现行
    policies = [
        p for p in authed.get("/api/policies").json()["items"]
        if p["domain_code"] == "orders"
    ]
    by_version = {p["version"]: p for p in policies}
    assert by_version[1]["active"] == 0
    assert by_version[2]["active"] == 1 and by_version[2]["keep_days"] == 3

    # 三处同动之二：裁决详情 —— 旧单已作废并标出原因，不再是待执行
    voided = _judgments(authed, "orders", "已作废")
    assert len(voided) == len(old_orders)
    assert all("缩天作废" in j["void_reason"] for j in voided)
    assert _judgments(authed, "orders", "待执行") == []

    # 作废旧单不可执行
    exe = authed.post(f"/api/judgments/{old_orders[0]['id']}/execute")
    assert exe.status_code == 409

    # 三处同动之三：分区列表 —— 现行策略列已同步到 v2/3天
    parts = [
        p for p in authed.get("/api/partitions").json()["items"]
        if p["domain_code"] == "orders"
    ]
    assert all(p["current_version"] == 2 and p["current_keep_days"] == 3 for p in parts)

    # 缩天记录已入本机库
    events = authed.get("/api/shrink-events").json()["items"]
    assert events and events[0]["domain_code"] == "orders"
    assert events[0]["from_keep_days"] == 7 and events[0]["to_keep_days"] == 3

    # 重扫：按新 3 天重新开单，绑定 v2；3 天标准下 11-10 也到期，单更多
    _scan(authed)
    new_orders = _judgments(authed, "orders", "待执行")
    assert len(new_orders) == 3
    assert all(
        j["policy_version"] == 2 and j["keep_days"] == 3 and j["version_match"]
        for j in new_orders
    )

    # 新单可正常执行，执行流水绑定新版
    exe = authed.post(f"/api/judgments/{new_orders[0]['id']}/execute")
    assert exe.status_code == 200
    assert exe.json()["run"]["ok"] == 1

    # 对账可验：版本不一致为 0
    report = authed.post(
        "/api/reconcile-reports", json={"as_of_date": "2026-11-16"}
    ).json()["report"]
    assert report["version_mismatch_count"] == 0


def test_shrink_rejects_non_shorter_days(authed):
    r = _shrink(authed, "orders", 7, 1)
    assert r.status_code == 400
    assert "仅支持缩天" in r.json()["detail"]
    r = _shrink(authed, "orders", 30, 1)
    assert r.status_code == 400
    r = _shrink(authed, "orders", 0, 1)
    assert r.status_code == 400


def test_shrink_conflict_on_stale_base_version(authed):
    assert _shrink(authed, "orders", 3, 1).status_code == 200
    # 第二名调度仍基于 v1 提交 → 串行锁下被拦，不会双成功
    r = _shrink(authed, "orders", 2, 1)
    assert r.status_code == 409
    assert "现行版本已变为 v2" in r.json()["detail"]
    # 基于现行 v2 的正常缩天仍可成功
    assert _shrink(authed, "orders", 2, 2).status_code == 200


def test_execute_blocks_version_mismatch_and_reconcile_flags_it(authed):
    _scan(authed)
    target = _judgments(authed, "orders", "待执行")[0]

    # 模拟缺作废的遗留单：直接改库让现行版前进，旧单仍在途（绕过作废重扫）
    db_path = os.environ["DATABASE_PATH"]
    with locked_db(db_path) as conn:
        conn.execute(
            "UPDATE retain_policies SET active = 0 "
            "WHERE domain_code = 'orders' AND version = 1"
        )
        conn.execute(
            "INSERT INTO retain_policies "
            "(domain_code, domain_name, keep_days, action_on_expire, version, active) "
            "VALUES ('orders', '订单明细域', 3, '归档', 2, 1)"
        )

    # 页面数据可核：该单绑定 v1、现行 v2，version_match 为假
    stale = [j for j in _judgments(authed, "orders", "待执行") if not j["version_match"]]
    assert stale, "应存在绑定版本与现行版本不一致的在途单"

    # 缩天不一致即拦
    exe = authed.post(f"/api/judgments/{target['id']}/execute")
    assert exe.status_code == 409
    assert "不一致" in exe.json()["detail"]

    # 入库扫描可验：对账报告标出版本不一致
    report = authed.post(
        "/api/reconcile-reports", json={"as_of_date": "2026-11-16"}
    ).json()["report"]
    assert report["version_mismatch_count"] >= 1
    assert any(
        m["judgment_id"] == target["id"] for m in report["version_mismatches"]
    )


def test_concurrent_shrink_serialized_by_write_lock(client):
    """两名调度同时缩天：写锁串行 + 版本校验，恰好一成一败，不会新旧各跑一单。"""
    db_path = os.environ["DATABASE_PATH"]
    results = []

    def worker():
        try:
            with locked_db(db_path) as conn:
                shrink_policy(conn, "orders", 3, 1, operator="调度")
            results.append("ok")
        except ShrinkConflict:
            results.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["conflict", "ok"]

    with sqlite3.connect(db_path) as conn:
        versions = conn.execute(
            "SELECT version, active FROM retain_policies "
            "WHERE domain_code = 'orders' ORDER BY version"
        ).fetchall()
        # 只长出一个新版本，且仅它为现行
        assert versions == [(1, 0), (2, 1)]
        events = conn.execute("SELECT COUNT(*) FROM shrink_events").fetchone()[0]
        assert events == 1
