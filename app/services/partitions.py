import sqlite3


def list_partitions(
    conn: sqlite3.Connection,
    domain_code: str | None = None,
    storage_state: str | None = None,
) -> list[dict]:
    """分区列表：带出本域现行策略版本与在途裁决绑定版本，与策略队列同源核对。"""
    clauses: list[str] = []
    params: list[object] = []
    if domain_code:
        clauses.append("p.domain_code = ?")
        params.append(domain_code)
    if storage_state:
        clauses.append("p.storage_state = ?")
        params.append(storage_state)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT p.id, p.domain_code, p.biz_date, p.row_count, p.storage_state, "
        "p.archive_uri, p.created_at, "
        "cur.version AS current_version, cur.keep_days AS current_keep_days, "
        "(SELECT j.policy_version FROM judgments j "
        "  WHERE j.partition_id = p.id AND j.status IN ('待执行','执行中') "
        "  ORDER BY j.id DESC LIMIT 1) AS open_judgment_version "
        "FROM partitions p "
        "LEFT JOIN retain_policies cur "
        "  ON cur.domain_code = p.domain_code AND cur.active = 1"
        + where
        + " ORDER BY p.domain_code, p.biz_date",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_partition(conn: sqlite3.Connection, partition_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, domain_code, biz_date, row_count, storage_state, "
        "archive_uri, created_at FROM partitions WHERE id = ?",
        (partition_id,),
    ).fetchone()
    return dict(row) if row else None
