import sqlite3


def run_reconcile(conn: sqlite3.Connection, as_of_date: str) -> dict:
    """策略视角与实物分区对账：统计各存储态，找出「有完成裁决但状态不符」的错位，
    并核对在途裁决绑定版本与现行策略版本是否一致（缩天后可验）。"""
    counts = {
        r["storage_state"]: r["c"]
        for r in conn.execute(
            "SELECT storage_state, COUNT(*) AS c FROM partitions GROUP BY storage_state"
        ).fetchall()
    }
    online = int(counts.get("在线", 0))
    archived = int(counts.get("已归档", 0))
    destroyed = int(counts.get("已销毁", 0))
    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM judgments WHERE status IN ('待执行','执行中')"
    ).fetchone()["c"]

    mismatches = conn.execute(
        """
        SELECT j.id AS judgment_id, j.decided_action, j.status AS judgment_status,
               p.id AS partition_id, p.storage_state
        FROM judgments j
        JOIN partitions p ON p.id = j.partition_id
        WHERE j.status = '已完成'
          AND (
            (j.decided_action = '归档' AND p.storage_state != '已归档')
            OR (j.decided_action = '销毁' AND p.storage_state != '已销毁')
          )
        """
    ).fetchall()
    mismatch_count = len(mismatches)

    version_mismatches = conn.execute(
        """
        SELECT j.id AS judgment_id, j.domain_code, j.policy_version, j.keep_days,
               cur.version AS current_version, cur.keep_days AS current_keep_days
        FROM judgments j
        JOIN retain_policies cur
          ON cur.domain_code = j.domain_code AND cur.active = 1
        WHERE j.status IN ('待执行','执行中')
          AND j.policy_version != cur.version
        """
    ).fetchall()
    version_mismatch_count = len(version_mismatches)

    summary = (
        f"在线{online} / 已归档{archived} / 已销毁{destroyed} / "
        f"待执行裁决{pending} / 错位{mismatch_count} / "
        f"版本不一致{version_mismatch_count}"
    )
    cur = conn.execute(
        "INSERT INTO reconcile_reports "
        "(as_of_date, online_count, archived_count, destroyed_count, "
        "pending_judgment_count, mismatch_count, version_mismatch_count, summary) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            as_of_date,
            online,
            archived,
            destroyed,
            int(pending),
            mismatch_count,
            version_mismatch_count,
            summary,
        ),
    )
    report_id = int(cur.lastrowid)
    row = conn.execute(
        "SELECT id, as_of_date, online_count, archived_count, destroyed_count, "
        "pending_judgment_count, mismatch_count, version_mismatch_count, "
        "summary, created_at "
        "FROM reconcile_reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    report = dict(row)
    report["mismatches"] = [dict(m) for m in mismatches]
    report["version_mismatches"] = [dict(m) for m in version_mismatches]
    return report


def list_reports(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, as_of_date, online_count, archived_count, destroyed_count, "
        "pending_judgment_count, mismatch_count, version_mismatch_count, "
        "summary, created_at "
        "FROM reconcile_reports ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]
