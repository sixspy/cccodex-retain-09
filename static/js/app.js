(() => {
  const main = document.getElementById("main");
  let view = "policies";

  async function api(path, opts) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (res.status === 401) {
      location.href = "/login";
      throw new Error("未登录");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.error || "请求失败");
    }
    return data;
  }

  function tag(state) {
    const cls =
      state === "在线" || state === "待执行"
        ? "warn"
        : state === "已完成" || state === "已归档" || state === "已销毁" || state === "现行"
          ? "ok"
          : state === "已失败" || state === "不一致"
            ? "bad"
            : "";
    return `<span class="tag ${cls}">${state}</span>`;
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
    }[c]));
  }

  async function renderPolicies() {
    const [data, events] = await Promise.all([
      api("/api/policies"),
      api("/api/shrink-events"),
    ]);
    main.innerHTML = `
      <section class="panel">
        <h2>留存策略版本队列</h2>
        <p class="sub">每域仅一版现行。缩天走「作废重扫」：新版生效即作废旧版待执行单，重跑到期扫描后按新天数开单。</p>
        <table>
          <thead><tr><th>主题域</th><th>编码</th><th>版本</th><th>保留天</th><th>到期动作</th><th>状态</th><th>缩天</th></tr></thead>
          <tbody>
            ${data.items
              .map(
                (p) => `<tr>
              <td>${esc(p.domain_name)}</td>
              <td class="mono">${esc(p.domain_code)}</td>
              <td>v${p.version}</td>
              <td>${p.keep_days}</td>
              <td>${tag(p.action_on_expire)}</td>
              <td>${p.active ? tag("现行") : `<span class="tag">历史</span>`}</td>
              <td>${
                p.active
                  ? `<span class="shrink-form">
                      <input type="number" min="1" max="${p.keep_days - 1}" placeholder="< ${p.keep_days}"
                        data-days="${esc(p.domain_code)}" />
                      <button type="button" data-shrink="${esc(p.domain_code)}"
                        data-base="${p.version}">缩天生效</button>
                    </span>`
                  : "—"
              }</td>
            </tr>`
              )
              .join("")}
          </tbody>
        </table>
        <p class="note" id="shrink-msg"></p>
      </section>
      <section class="panel">
        <h2>缩天记录（本机库）</h2>
        <table>
          <thead><tr><th>编号</th><th>主题域</th><th>版本</th><th>保留天</th><th>作废单数</th><th>操作员</th><th>时间</th></tr></thead>
          <tbody>
            ${
              events.items.length
                ? events.items
                    .map(
                      (e) => `<tr>
              <td>${e.id}</td>
              <td class="mono">${esc(e.domain_code)}</td>
              <td>v${e.from_version} → v${e.to_version}</td>
              <td>${e.from_keep_days}天 → ${e.to_keep_days}天</td>
              <td>${e.voided_count}</td>
              <td>${esc(e.operator || "—")}</td>
              <td class="mono">${esc(e.created_at)}</td>
            </tr>`
                    )
                    .join("")
                : `<tr><td colspan="7" class="empty">尚无缩天记录</td></tr>`
            }
          </tbody>
        </table>
      </section>`;
    main.querySelectorAll("[data-shrink]").forEach((btn) => {
      btn.onclick = async () => {
        const domain = btn.dataset.shrink;
        const input = main.querySelector(`input[data-days="${domain}"]`);
        const keepDays = parseInt(input.value, 10);
        const msg = document.getElementById("shrink-msg");
        if (!Number.isInteger(keepDays) || keepDays <= 0) {
          msg.textContent = "请先填写新的保留天数（正整数，且小于现行天数）";
          return;
        }
        try {
          const r = await api(`/api/policies/${domain}/shrink`, {
            method: "POST",
            body: JSON.stringify({
              keep_days: keepDays,
              base_version: parseInt(btn.dataset.base, 10),
            }),
          });
          await renderPolicies();
          document.getElementById("shrink-msg").textContent =
            `已缩天至 v${r.to_version}/${r.policy.keep_days}天，作废旧单 ${r.voided} 张；` +
            "请到「到期扫描」重跑开单，裁决清单与分区列表已同步。";
        } catch (e) {
          msg.textContent = `缩天被拦：${e.message}`;
        }
      };
    });
  }

  async function renderPartitions() {
    const data = await api("/api/partitions");
    main.innerHTML = `
      <section class="panel">
        <h2>实物分区</h2>
        <p class="sub">在线 / 已归档 / 已销毁 / 裁撤中。每行带出本域现行策略版本与在途裁决绑定版本，与策略队列同源。</p>
        <table>
          <thead><tr><th>主题域</th><th>业务日</th><th>行数</th><th>存储态</th><th>现行策略</th><th>在途单绑定</th><th>归档地址</th></tr></thead>
          <tbody>
            ${data.items
              .map((p) => {
                const mismatch =
                  p.open_judgment_version != null &&
                  p.current_version != null &&
                  p.open_judgment_version !== p.current_version;
                return `<tr>
              <td class="mono">${esc(p.domain_code)}</td>
              <td class="mono">${esc(p.biz_date)}</td>
              <td>${p.row_count}</td>
              <td>${tag(p.storage_state)}</td>
              <td>${p.current_version != null ? `v${p.current_version} / ${p.current_keep_days}天` : "—"}</td>
              <td>${
                p.open_judgment_version != null
                  ? `v${p.open_judgment_version}${mismatch ? " " + tag("不一致") : ""}`
                  : "—"
              }</td>
              <td class="mono">${esc(p.archive_uri || "—")}</td>
            </tr>`;
              })
              .join("")}
          </tbody>
        </table>
      </section>`;
  }

  async function renderScan() {
    const jobs = await api("/api/scan-jobs");
    main.innerHTML = `
      <div class="toolbar">
        <label class="field">基准日
          <input id="as-of" value="2026-11-16" />
        </label>
        <button type="button" id="btn-scan">跑到期扫描</button>
      </div>
      <section class="panel">
        <h2>扫描任务</h2>
        <p class="sub">扫描与缩天串行落库：开单绑定的策略版本即扫描时现行版本，可核对。</p>
        <table>
          <thead><tr><th>编号</th><th>基准日</th><th>状态</th><th>到期数</th><th>说明</th></tr></thead>
          <tbody>
            ${
              jobs.items.length
                ? jobs.items
                    .map(
                      (j) => `<tr>
              <td>${j.id}</td>
              <td class="mono">${esc(j.as_of_date)}</td>
              <td>${tag(j.status)}</td>
              <td>${j.expired_count}</td>
              <td>${esc(j.message)}</td>
            </tr>`
                    )
                    .join("")
                : `<tr><td colspan="5" class="empty">尚无扫描任务</td></tr>`
            }
          </tbody>
        </table>
      </section>`;
    document.getElementById("btn-scan").onclick = async () => {
      const as_of_date = document.getElementById("as-of").value.trim();
      await api("/api/scan-jobs", {
        method: "POST",
        body: JSON.stringify({ as_of_date }),
      });
      await renderScan();
    };
  }

  async function renderJudgments() {
    const data = await api("/api/judgments");
    main.innerHTML = `
      <section class="panel">
        <h2>裁决清单</h2>
        <p class="sub">每单标出绑定版本与现行版本；缩天后旧版单已作废，残留不一致单出库即拦。</p>
        <table>
          <thead><tr><th>编号</th><th>主题域</th><th>业务日</th><th>绑定版本</th><th>现行版本</th><th>动作</th><th>状态</th><th>说明</th><th>操作</th></tr></thead>
          <tbody>
            ${
              data.items.length
                ? data.items
                    .map((j) => {
                      const stale =
                        (j.status === "待执行" || j.status === "已失败") &&
                        !j.version_match;
                      return `<tr>
              <td>${j.id}</td>
              <td class="mono">${esc(j.domain_code)}</td>
              <td class="mono">${esc(j.biz_date)}</td>
              <td>v${j.policy_version} / ${j.keep_days}天</td>
              <td>${
                j.current_version != null
                  ? `v${j.current_version} / ${j.current_keep_days}天` +
                    (j.version_match ? "" : " " + tag("不一致"))
                  : "—"
              }</td>
              <td>${esc(j.decided_action)}</td>
              <td>${tag(j.status)}</td>
              <td class="note-cell">${esc(j.void_reason || j.fail_reason || "—")}</td>
              <td>${
                j.status === "待执行" || j.status === "已失败"
                  ? stale
                    ? `<span class="tag bad">版本不一致·拦</span>`
                    : `<button type="button" data-exec="${j.id}">执行</button>`
                  : "—"
              }</td>
            </tr>`;
                    })
                    .join("")
                : `<tr><td colspan="9" class="empty">尚无裁决，请先跑到期扫描</td></tr>`
            }
          </tbody>
        </table>
        <p class="note" id="exec-msg"></p>
      </section>`;
    main.querySelectorAll("[data-exec]").forEach((btn) => {
      btn.onclick = async () => {
        try {
          await api(`/api/judgments/${btn.dataset.exec}/execute`, { method: "POST" });
          await renderJudgments();
        } catch (e) {
          document.getElementById("exec-msg").textContent = `执行被拦：${e.message}`;
        }
      };
    });
  }

  async function renderReconcile() {
    const reports = await api("/api/reconcile-reports");
    main.innerHTML = `
      <div class="toolbar">
        <label class="field">基准日
          <input id="as-of-r" value="2026-11-16" />
        </label>
        <button type="button" id="btn-rec">生成对账报告</button>
      </div>
      <section class="panel">
        <h2>对账报告</h2>
        <p class="sub">除存储态错位外，另核对在途裁决绑定版本与现行策略版本，缩天后可验。</p>
        <table>
          <thead><tr><th>编号</th><th>基准日</th><th>在线</th><th>归档</th><th>销毁</th><th>待裁决</th><th>错位</th><th>版本不一致</th><th>摘要</th></tr></thead>
          <tbody>
            ${
              reports.items.length
                ? reports.items
                    .map(
                      (r) => `<tr>
              <td>${r.id}</td>
              <td class="mono">${esc(r.as_of_date)}</td>
              <td>${r.online_count}</td>
              <td>${r.archived_count}</td>
              <td>${r.destroyed_count}</td>
              <td>${r.pending_judgment_count}</td>
              <td>${r.mismatch_count}</td>
              <td>${r.version_mismatch_count ? tag("不一致") + ` ${r.version_mismatch_count}` : "0"}</td>
              <td>${esc(r.summary)}</td>
            </tr>`
                    )
                    .join("")
                : `<tr><td colspan="9" class="empty">尚无对账报告</td></tr>`
            }
          </tbody>
        </table>
      </section>`;
    document.getElementById("btn-rec").onclick = async () => {
      const as_of_date = document.getElementById("as-of-r").value.trim();
      await api("/api/reconcile-reports", {
        method: "POST",
        body: JSON.stringify({ as_of_date }),
      });
      await renderReconcile();
    };
  }

  const renderers = {
    policies: renderPolicies,
    partitions: renderPartitions,
    scan: renderScan,
    judgments: renderJudgments,
    reconcile: renderReconcile,
  };

  async function show(name) {
    view = name;
    document.querySelectorAll("#nav button").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === name);
    });
    await renderers[name]();
  }

  document.getElementById("nav").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-view]");
    if (btn) show(btn.dataset.view);
  });

  document.getElementById("btn-logout").onclick = async () => {
    await api("/api/logout", { method: "POST" });
    location.href = "/login";
  };

  api("/api/meta").then((m) => {
    document.getElementById("lab-title").textContent = m.lab_name;
    document.getElementById("lab-sub").textContent = m.skeleton;
  });

  show(view);
})();
