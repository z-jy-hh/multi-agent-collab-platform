/* P2 · profile drawer: per-agent rule/skill composition.
 *
 * Pure UI: reads /api/modes (agents/profiles/rules/skills), stores the
 * per-agent plan in state.profilePlan, and startRun sends it as `profiles`.
 * Rules are grouped by their first tag; "hard" rules carry a check.
 */
(function () {
  const el = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const data = () => state.modesData || {};
  const plan = () => (state.profilePlan = state.profilePlan || {});

  function currentAgent() {
    return (el("profile-agent") && el("profile-agent").value) || "";
  }

  function groupRules(rules) {
    const groups = new Map();
    for (const r of rules) {
      const tag = (r.tags && r.tags[0]) || "未分类";
      if (!groups.has(tag)) groups.set(tag, []);
      groups.get(tag).push(r);
    }
    return groups;
  }

  function renderEditor() {
    const host = el("profile-rules");
    if (!host) return;
    const agent = currentAgent();
    const p = plan()[agent] || { rules: [], skills: [], temp: [] };
    const groups = groupRules(data().rules || []);
    const parts = [];
    for (const [tag, rules] of groups) {
      const selected = rules.filter((r) => p.rules.includes(r.id)).length;
      parts.push(`<div style="margin:6px 0 2px;font-weight:600">${esc(tag)} ` +
        `<span class="muted">${selected}/${rules.length}</span></div>`);
      for (const r of rules) {
        const hard = r.enforcement === "hard" ? ` <span class="badge">hard</span>` : "";
        parts.push(`<label style="display:flex;gap:6px;align-items:flex-start;margin:2px 0">` +
          `<input type="checkbox" data-rule="${esc(r.id)}" ${p.rules.includes(r.id) ? "checked" : ""}>` +
          `<span>${esc(r.title)}${hard}<br><span class="muted">${esc(r.text).slice(0, 120)}</span></span></label>`);
      }
    }
    host.innerHTML = parts.join("") || `<span class="muted">规则目录为空</span>`;

    const skillsHost = el("profile-skills");
    if (skillsHost) {
      const skills = data().skills || [];
      skillsHost.innerHTML = skills.map((s) => {
        const on = p.skills.includes(s.id);
        const disabled = s.installed ? "" : "disabled";
        return `<label style="display:inline-flex;gap:4px;margin:2px 8px 2px 0" class="muted">` +
          `<input type="checkbox" data-skill="${esc(s.id)}" ${on ? "checked" : ""} ${disabled}>` +
          `${esc(s.id)}${s.installed ? "" : "（未安装）"}</label>`;
      }).join("") || `<span class="muted">无技能</span>`;
    }
    if (el("profile-temp")) el("profile-temp").value = (p.temp || []).join("\n");
    if (el("profile-status")) {
      el("profile-status").textContent = agent
        ? `${agent}: ${p.rules.length} 条规则 · ${p.skills.length} 个技能 · ${p.temp.length} 条临时`
        : "选择一个 agent";
    }
    document.querySelectorAll("#profile-rules input[data-rule]").forEach((cb) => {
      cb.onchange = () => { if (el("profile-status")) el("profile-status").textContent = "有未应用的修改"; };
    });
    document.querySelectorAll("#profile-skills input[data-skill]").forEach((cb) => {
      cb.onchange = () => { if (el("profile-status")) el("profile-status").textContent = "有未应用的修改"; };
    });
  }

  function renderAgents() {
    const sel = el("profile-agent");
    if (!sel) return;
    const agents = data().agents || [];
    const cur = sel.value;
    sel.innerHTML = agents.map((a) =>
      `<option value="${esc(a.id)}">${esc(a.name)} (${esc(a.id)}@${esc(a.version)})</option>`).join("");
    if (cur && agents.some((a) => a.id === cur)) sel.value = cur;
    renderEditor();
  }

  window.renderProfileAgents = renderAgents;

  window.openProfileDrawer = async function () {
    const d = el("profile-drawer");
    const b = el("btn-profile");
    if (!d || !b) return;
    d.hidden = !d.hidden;
    b.setAttribute("aria-expanded", String(!d.hidden));
    if (d.hidden) return;
    if (!state.modesData) {
      try { state.modesData = await fetchJSON("/api/modes"); }
      catch (e) { if (el("profile-status")) el("profile-status").textContent = "目录加载失败: " + e.message; return; }
    }
    renderAgents();
  };

  window.applyProfile = function () {
    const agent = currentAgent();
    if (!agent) return;
    const p = {
      profile: agent,
      rules: [...document.querySelectorAll("#profile-rules input[data-rule]:checked")].map((x) => x.dataset.rule),
      skills: [...document.querySelectorAll("#profile-skills input[data-skill]:checked")].map((x) => x.dataset.skill),
      temp: String((el("profile-temp") && el("profile-temp").value) || "")
        .split("\n").map((s) => s.trim()).filter(Boolean).slice(0, 3),
    };
    if (!p.rules.length && !p.skills.length && !p.temp.length) delete plan()[agent];
    else plan()[agent] = p;
    if (el("profile-status")) {
      el("profile-status").textContent = plan()[agent]
        ? `${agent}: 已应用（${p.rules.length} 规则 / ${p.skills.length} 技能 / ${p.temp.length} 临时）`
        : `${agent}: 已清空（off）`;
    }
  };

  window.clearProfile = function () {
    const agent = currentAgent();
    if (!agent) return;
    delete plan()[agent];
    renderEditor();
    if (el("profile-status")) el("profile-status").textContent = `${agent}: 已清空（off）`;
  };

  const btn = el("btn-profile");
  if (btn) btn.onclick = window.openProfileDrawer;
  const sel = el("profile-agent");
  if (sel) sel.onchange = renderEditor;
})();
