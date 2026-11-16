import sqlite3


class ShrinkInvalid(ValueError):
    """缩天请求本身不合法（天数未缩短、域不存在等）→ 400。"""


class ShrinkConflict(ValueError):
    """缩天基于的现行版本已被人抢先换掉 → 409（串行锁下的乐观版本校验）。"""


def list_policies(conn: sqlite3.Connection, active_only: bool = False) -> list[dict]:
    sql = (
        "SELECT id, domain_code, domain_name, keep_days, action_on_expire, "
        "version, active, created_at FROM retain_policies"
    )
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY domain_code, version DESC"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def get_active_policy(conn: sqlite3.Connection, domain_code: str) -> dict | None:
    row = conn.execute(
        "SELECT id, domain_code, domain_name, keep_days, action_on_expire, "
        "version, active, created_at FROM retain_policies "
        "WHERE domain_code = ? AND active = 1 "
        "ORDER BY version DESC LIMIT 1",
        (domain_code,),
    ).fetchone()
    return dict(row) if row else None


def shrink_policy(
    conn: sqlite3.Connection,
    domain_code: str,
    new_keep_days: int,
    base_version: int,
    operator: str = "",
) -> dict:
    """缩天换版（作废重扫路径，须在 locked_db 写锁内调用）。

    选定路径：作废重扫 —— 缩天先生效新版，并把本域所有可执行旧单
    （待执行 / 已失败）作废，操作员重跑到期扫描后按新版天数重新开单；
    不选「锁旧快照执行」，旧单不允许再按旧天数出库。

    原子四步：校验现行版本 → 新版生效旧版停用 → 作废旧单 → 记缩天事件。
    base_version 是调用方看到的现行版本号，与库内不一致即冲突（两名
    调度串行抢写时，后到者必在此被拦，不会双成功各跑一单）。
    """
    current = get_active_policy(conn, domain_code)
    if not current:
        raise ShrinkInvalid(f"主题域 {domain_code} 无现行策略")
    if int(base_version) != int(current["version"]):
        raise ShrinkConflict(
            f"现行版本已变为 v{current['version']}（基于 v{base_version} 的缩天被拦），"
            "请刷新后重试"
        )
    new_keep_days = int(new_keep_days)
    if new_keep_days <= 0:
        raise ShrinkInvalid("保留天数必须为正整数")
    if new_keep_days >= int(current["keep_days"]):
        raise ShrinkInvalid(
            f"仅支持缩天：新保留天 {new_keep_days} 须小于现行 {current['keep_days']}"
        )

    from_version = int(current["version"])
    to_version = from_version + 1
    conn.execute(
        "UPDATE retain_policies SET active = 0 WHERE id = ?", (current["id"],)
    )
    cur = conn.execute(
        "INSERT INTO retain_policies "
        "(domain_code, domain_name, keep_days, action_on_expire, version, active) "
        "VALUES (?,?,?,?,?,1)",
        (
            domain_code,
            current["domain_name"],
            new_keep_days,
            current["action_on_expire"],
            to_version,
        ),
    )
    new_policy_id = int(cur.lastrowid)

    void_reason = (
        f"缩天作废：v{from_version}/{current['keep_days']}天 → "
        f"v{to_version}/{new_keep_days}天，请重跑到期扫描开单"
    )
    void_cur = conn.execute(
        "UPDATE judgments SET status = '已作废', void_reason = ? "
        "WHERE domain_code = ? AND status IN ('待执行','已失败')",
        (void_reason, domain_code),
    )
    voided = int(void_cur.rowcount)

    conn.execute(
        "INSERT INTO shrink_events "
        "(domain_code, from_version, to_version, from_keep_days, to_keep_days, "
        "voided_count, operator) VALUES (?,?,?,?,?,?,?)",
        (
            domain_code,
            from_version,
            to_version,
            int(current["keep_days"]),
            new_keep_days,
            voided,
            operator,
        ),
    )

    row = conn.execute(
        "SELECT id, domain_code, domain_name, keep_days, action_on_expire, "
        "version, active, created_at FROM retain_policies WHERE id = ?",
        (new_policy_id,),
    ).fetchone()
    return {
        "policy": dict(row),
        "voided": voided,
        "from_version": from_version,
        "to_version": to_version,
    }


def list_shrink_events(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, domain_code, from_version, to_version, from_keep_days, "
        "to_keep_days, voided_count, operator, created_at "
        "FROM shrink_events ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]
