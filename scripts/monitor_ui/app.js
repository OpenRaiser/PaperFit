const app = document.getElementById('app');

let snapshot = null;
let selectedPagePath = null;
let selectedTab = 'overview';
let paused = false;
let lastError = null;

const statusTone = {
  DONE: 'good',
  CONTINUE: 'warn',
  EVALUATING: 'info',
  RUNNING: 'info',
  BLOCKED: 'bad',
  FAILED: 'bad',
  UNKNOWN: 'muted',
};

const phaseLabels = {
  INIT: '初始化',
  READY: '就绪',
  OBSERVING: '观察',
  DIAGNOSING: '诊断',
  PLANNING: '规划',
  REPAIRING: '修复',
  VERIFYING: '验证',
  DONE: '完成',
  CONTINUE: '继续',
  BLOCKED: '阻断',
};

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatValue(value, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'boolean') return value ? '是' : '否';
  return String(value);
}

function toneForStatus(value) {
  const normalized = String(value || 'UNKNOWN').toUpperCase();
  return statusTone[normalized] || 'muted';
}

function chip(value, tone = toneForStatus(value)) {
  return `<span class="chip ${tone}">${escapeHtml(formatValue(value))}</span>`;
}

function shortPath(value) {
  if (!value) return '未记录';
  const text = String(value);
  if (text.length <= 64) return text;
  return `...${text.slice(-61)}`;
}

function groupCount(items, key) {
  return items.reduce((acc, item) => {
    const value = item[key] || 'unknown';
    acc[value] = (acc[value] || 0) + 1;
    return acc;
  }, {});
}

function renderSparkCounts(counts) {
  const entries = Object.entries(counts);
  if (entries.length === 0) return '<span class="muted-text">无记录</span>';
  return entries
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `<span class="mini-stat"><b>${escapeHtml(key)}</b>${value}</span>`)
    .join('');
}

async function fetchSnapshot() {
  if (paused) return;
  try {
    const response = await fetch('/api/snapshot', { cache: 'no-store' });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    snapshot = await response.json();
    lastError = null;
    if (!selectedPagePath && snapshot.page_images?.length) {
      selectedPagePath = snapshot.page_images[0].path;
    }
    render();
  } catch (error) {
    lastError = error instanceof Error ? error.message : String(error);
    render();
  }
}

function currentStatus() {
  return snapshot?.status || {};
}

function currentImages() {
  return snapshot?.page_images || [];
}

function selectedImage() {
  const images = currentImages();
  return images.find((image) => image.path === selectedPagePath) || images[0] || null;
}

function currentFindings() {
  return snapshot?.findings || [];
}

function findingRows(limit = 12) {
  const findings = currentFindings();
  if (findings.length === 0) {
    return '<div class="empty">当前 artifact 中没有可展示的视觉缺陷。</div>';
  }
  return findings
    .slice(0, limit)
    .map((finding) => {
      const page = finding.page ? `p.${finding.page}` : 'p.?';
      const subtype = finding.subtype ? `<span class="subtle">${escapeHtml(finding.subtype)}</span>` : '';
      return `
        <button class="finding-row" data-page="${escapeHtml(finding.page || '')}">
          <span class="page-pill">${escapeHtml(page)}</span>
          <span>
            <strong>${escapeHtml(finding.family || 'unknown')}</strong>
            <small>${escapeHtml(finding.summary || '未记录说明')}</small>
          </span>
          ${subtype}
          ${chip(finding.severity || 'unknown', finding.severity === 'critical' || finding.severity === 'major' ? 'bad' : 'muted')}
        </button>
      `;
    })
    .join('');
}

function metric(label, value, tone = 'neutral') {
  return `
    <section class="metric glass">
      <span>${escapeHtml(label)}</span>
      <strong class="${tone}">${escapeHtml(formatValue(value))}</strong>
    </section>
  `;
}

function renderShell() {
  const status = currentStatus();
  const runtime = status.runtime || {};
  const freshness = status.artifact_freshness || {};
  const claim = snapshot?.capability_claim || {};
  const updated = new Date().toLocaleTimeString('zh-CN', { hour12: false });

  return `
    <main class="shell">
      <header class="hero glass">
        <div class="brand-block">
          <div class="mark">PF</div>
          <div>
            <h1>PaperFit Observatory</h1>
            <p>排版 Agent 运行监控台</p>
          </div>
        </div>
        <div class="hero-status">
          ${chip(status.status || 'UNKNOWN')}
          ${chip(status.gatekeeper_decision || 'Gatekeeper 未记录', toneForStatus(status.gatekeeper_decision))}
          ${chip(`Freshness: ${freshness.status || 'unknown'}`, freshness.status === 'pass' ? 'good' : 'warn')}
          ${chip(claim.level || '证据不足', claim.level === '局部 checkpoint' ? 'info' : 'muted')}
        </div>
      </header>

      <section class="project-strip glass">
        <div>
          <span>项目</span>
          <strong title="${escapeHtml(snapshot?.project_root || '')}">${escapeHtml(shortPath(snapshot?.project_root))}</strong>
        </div>
        <div>
          <span>RunResult</span>
          <strong>${escapeHtml(shortPath(status.run_result_path))}</strong>
        </div>
        <div>
          <span>Run ID</span>
          <strong>${escapeHtml(runtime.run_id || '未记录')}</strong>
        </div>
        <button id="refreshBtn" title="刷新">↻</button>
        <button id="pauseBtn" title="暂停实时刷新">${paused ? '▶' : 'Ⅱ'}</button>
        <span class="last-updated">刷新 ${updated}</span>
      </section>

      ${lastError ? `<section class="error glass">读取失败：${escapeHtml(lastError)}</section>` : ''}

      <nav class="tabs glass" aria-label="监控视图">
        ${[
          ['overview', '总览'],
          ['timeline', '运行时间线'],
          ['visual', '视觉证据'],
          ['repair', '修复决策'],
          ['safety', '安全边界'],
          ['benchmark', 'Benchmark'],
        ]
          .map(([id, label]) => `<button class="${selectedTab === id ? 'active' : ''}" data-tab="${id}">${label}</button>`)
          .join('')}
      </nav>

      <section class="content">
        ${renderTab()}
      </section>
    </main>
  `;
}

function renderOverview() {
  const status = currentStatus();
  const defect = status.defect_summary || {};
  const repair = status.repair || {};
  const runtime = status.runtime || {};
  const claim = snapshot?.capability_claim || {};
  const findings = currentFindings();

  return `
    <div class="overview-grid">
      <section class="main-panel glass">
        <div class="panel-title">
          <h2>能力声明</h2>
          <span>${chip(claim.global_status || '不能声明整体完成', 'warn')}</span>
        </div>
        <div class="claim-card">
          <div>
            <span>当前级别</span>
            <strong>${escapeHtml(claim.level || '证据不足')}</strong>
          </div>
          <div>
            <span>范围</span>
            <strong>${escapeHtml(claim.scope || '未记录')}</strong>
          </div>
          <div>
            <span>结论</span>
            <strong>${escapeHtml(claim.conclusion || '未记录')}</strong>
          </div>
        </div>
        <ul class="limit-list">
          ${(claim.limits || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('')}
        </ul>
      </section>

      <aside class="side-panel glass">
        <div class="panel-title"><h2>当前状态</h2></div>
        <div class="kv"><span>TaskSpec</span><strong>${escapeHtml(status.task_type || status.task?.type || 'unknown')}</strong></div>
        <div class="kv"><span>Runtime</span><strong>${escapeHtml(status.status || 'UNKNOWN')}</strong></div>
        <div class="kv"><span>Gatekeeper</span><strong>${escapeHtml(status.gatekeeper_decision || '未记录')}</strong></div>
        <div class="kv"><span>事件数</span><strong>${escapeHtml(runtime.event_count ?? 0)}</strong></div>
      </aside>

      <section class="metric-grid">
        ${metric('页面图像', currentImages().length)}
        ${metric('视觉 findings', findings.length)}
        ${metric('剩余缺陷', defect.remaining ?? 0)}
        ${metric('修复候选', repair.plan_candidates ?? snapshot?.repair?.total ?? 0)}
        ${metric('已应用', repair.applied_count ?? 0)}
        ${metric('B2 selected', (repair.b2_width_selected_candidates || {}).total ?? 0)}
      </section>

      <section class="main-panel glass">
        <div class="panel-title">
          <h2>缺陷分布</h2>
          <span>${findings.length} 条</span>
        </div>
        <div class="spark-row">${renderSparkCounts(groupCount(findings, 'family'))}</div>
        <div class="finding-list compact">${findingRows(8)}</div>
      </section>
    </div>
  `;
}

function renderTimeline() {
  const events = snapshot?.events || [];
  const runtime = currentStatus().runtime || {};
  const phases = ['INIT', 'READY', 'OBSERVING', 'DIAGNOSING', 'PLANNING', 'REPAIRING', 'VERIFYING', 'DONE'];
  const current = String(runtime.last_runtime_state || currentStatus().status || '').toUpperCase();
  const currentIndex = phases.indexOf(current);

  return `
    <div class="two-column">
      <section class="main-panel glass">
        <div class="panel-title">
          <h2>State Machine</h2>
          <span>${escapeHtml(runtime.last_phase || '未记录 phase')}</span>
        </div>
        <div class="state-rail">
          ${phases
            .map((phase, index) => {
              const state = index < currentIndex ? 'done' : index === currentIndex ? 'active' : 'pending';
              return `
                <div class="state-step ${state}">
                  <span></span>
                  <strong>${phaseLabels[phase] || phase}</strong>
                  <small>${phase}</small>
                </div>
              `;
            })
            .join('')}
        </div>
      </section>

      <section class="side-panel glass">
        <div class="panel-title"><h2>Runtime Actions</h2></div>
        <div class="action-map">
          ${Object.entries(runtime.actions || {})
            .map(([key, value]) => `<div class="kv"><span>${escapeHtml(key)}</span><strong>${escapeHtml(value)}</strong></div>`)
            .join('') || '<div class="empty">没有 action 摘要。</div>'}
        </div>
      </section>

      <section class="main-panel glass span-all">
        <div class="panel-title">
          <h2>事件流</h2>
          <span>${events.length} 条</span>
        </div>
        <div class="event-stream">
          ${events
            .slice()
            .reverse()
            .slice(0, 80)
            .map((event) => `
              <article class="event-card">
                <time>${escapeHtml(event.timestamp || '')}</time>
                <strong>${escapeHtml(event.type || 'event')}</strong>
                <span>${escapeHtml(event.state || event.phase || '')}</span>
                <p>${escapeHtml(event.message || event._source || '')}</p>
              </article>
            `)
            .join('') || '<div class="empty">还没有 runtime event。</div>'}
        </div>
      </section>
    </div>
  `;
}

function renderVisual() {
  const images = currentImages();
  const image = selectedImage();
  const findings = currentFindings();
  const pageFindings = image?.page ? findings.filter((finding) => finding.page === image.page) : [];
  return `
    <div class="visual-layout">
      <section class="main-panel glass viewer-panel">
        <div class="panel-title">
          <h2>页面证据</h2>
          <span>${image ? `Page ${image.page || '?'}` : '无页面'}</span>
        </div>
        ${
          image
            ? `<div class="page-canvas"><img src="${escapeHtml(image.url)}" alt="Page ${escapeHtml(image.page || '')}" /></div>`
            : '<div class="empty tall">还没有 rendered page images。</div>'
        }
      </section>

      <aside class="side-panel glass">
        <div class="panel-title"><h2>页面列表</h2><span>${images.length}</span></div>
        <div class="thumbnail-list">
          ${images
            .map((item) => `
              <button class="thumb ${item.path === image?.path ? 'active' : ''}" data-image="${escapeHtml(item.path)}">
                <img src="${escapeHtml(item.url)}" alt="Page ${escapeHtml(item.page || '')}" />
                <span>Page ${escapeHtml(item.page || '?')}</span>
              </button>
            `)
            .join('') || '<div class="empty">没有页面缩略图。</div>'}
        </div>
      </aside>

      <section class="main-panel glass span-all">
        <div class="panel-title">
          <h2>当前页 findings</h2>
          <span>${pageFindings.length}</span>
        </div>
        <div class="finding-list">
          ${
            pageFindings.length
              ? pageFindings
                  .map((finding) => `
                    <article class="finding-card">
                      ${chip(finding.family || 'unknown', 'info')}
                      <strong>${escapeHtml(finding.summary)}</strong>
                      <span>${escapeHtml(finding.subtype || finding.source || '')}</span>
                    </article>
                  `)
                  .join('')
              : '<div class="empty">当前页没有记录的 finding。</div>'
          }
        </div>
      </section>
    </div>
  `;
}

function renderRepair() {
  const status = currentStatus();
  const repair = status.repair || {};
  const plan = snapshot?.repair || {};
  const execution = snapshot?.reports?.repair_execution || {};
  const mutation = snapshot?.reports?.source_mutation || {};
  const selected = repair.selected_candidates || [];
  return `
    <div class="two-column">
      <section class="main-panel glass">
        <div class="panel-title">
          <h2>修复决策</h2>
          <span>${chip(repair.execution_status || '未执行')}</span>
        </div>
        <div class="repair-grid">
          <div class="kv"><span>候选总数</span><strong>${escapeHtml(repair.plan_candidates ?? plan.total ?? 0)}</strong></div>
          <div class="kv"><span>已选择</span><strong>${escapeHtml(selected.length)}</strong></div>
          <div class="kv"><span>Applied</span><strong>${escapeHtml(repair.applied_count ?? 0)}</strong></div>
          <div class="kv"><span>Risk</span><strong>${escapeHtml(repair.risk_level || '未记录')}</strong></div>
          <div class="kv"><span>需要批准</span><strong>${escapeHtml(formatValue(repair.requires_approval, '未记录'))}</strong></div>
          <div class="kv"><span>Skipped</span><strong>${escapeHtml(formatValue(repair.skipped, '否'))}</strong></div>
        </div>
        <h3>B2 宽度链路</h3>
        <div class="b2-grid">
          ${metric('B2 findings', (repair.b2_width_findings || {}).total ?? 0)}
          ${metric('Targetable', (repair.b2_width_targetable_candidates || {}).total ?? 0)}
          ${metric('Selected', (repair.b2_width_selected_candidates || {}).total ?? 0)}
          ${metric('Unmatched', repair.b2_width_unmatched_findings ?? 0)}
        </div>
      </section>

      <section class="side-panel glass">
        <div class="panel-title"><h2>Approval</h2></div>
        ${renderApproval(status.approval || {})}
      </section>

      <section class="main-panel glass span-all">
        <div class="panel-title"><h2>Selected Candidates</h2><span>${selected.length}</span></div>
        <div class="candidate-list">
          ${selected
            .map((candidate, index) => `
              <article class="candidate-card">
                <span class="index">${index + 1}</span>
                <strong>${escapeHtml(candidate.object || candidate.label || candidate.defect_id || 'candidate')}</strong>
                <span>${escapeHtml(candidate.visual_width_subtype || candidate.defect_family || '')}</span>
                <code>${escapeHtml(JSON.stringify(candidate).slice(0, 420))}</code>
              </article>
            `)
            .join('') || '<div class="empty">没有 selected candidate。</div>'}
        </div>
      </section>

      <section class="main-panel glass span-all">
        <div class="panel-title"><h2>Mutation Report</h2><span>${Object.keys(mutation).length ? 'loaded' : 'empty'}</span></div>
        <pre class="json-box">${escapeHtml(JSON.stringify(Object.keys(mutation).length ? mutation : execution, null, 2).slice(0, 6000))}</pre>
      </section>
    </div>
  `;
}

function renderApproval(approval) {
  if (!Object.keys(approval).length) return '<div class="empty">没有 approval 对象。</div>';
  return `
    <div class="approval-stack">
      <div class="kv"><span>Status</span><strong>${escapeHtml(approval.status || 'unknown')}</strong></div>
      <div class="kv"><span>Granted</span><strong>${escapeHtml(formatValue(approval.approval_granted, '未记录'))}</strong></div>
      <div class="kv"><span>Risk</span><strong>${escapeHtml(approval.risk_level || '未记录')}</strong></div>
      <div class="kv"><span>Reason</span><strong>${escapeHtml(approval.reason || '未记录')}</strong></div>
    </div>
  `;
}

function renderSafety() {
  const status = currentStatus();
  const freshness = status.artifact_freshness || {};
  const loop = status.repair_loop_policy || {};
  const terminal = status.terminal_success_guard || {};
  const integrity = status.content_integrity || {};
  const rollback = snapshot?.reports?.rollback || {};
  const gate = loop.candidate_approval_scope_gate || {};
  const readiness = loop.second_round_apply_readiness || {};
  return `
    <div class="two-column">
      <section class="main-panel glass">
        <div class="panel-title"><h2>安全边界</h2>${chip(freshness.status || 'unknown', freshness.status === 'pass' ? 'good' : 'warn')}</div>
        <div class="safety-grid">
          <div class="kv"><span>Artifact Freshness</span><strong>${escapeHtml(freshness.status || 'unknown')}</strong></div>
          <div class="kv"><span>Blocking Checks</span><strong>${escapeHtml((freshness.blocking_checks || []).join(', ') || '无')}</strong></div>
          <div class="kv"><span>Content Integrity</span><strong>${escapeHtml(integrity.validation_status || '未记录')}</strong></div>
          <div class="kv"><span>Terminal Guard</span><strong>${escapeHtml(terminal.status || '未记录')}</strong></div>
          <div class="kv"><span>Candidate Gate</span><strong>${escapeHtml(gate.status || '未记录')}</strong></div>
          <div class="kv"><span>Second Round</span><strong>${escapeHtml(readiness.status || '未记录')}</strong></div>
        </div>
      </section>

      <section class="side-panel glass">
        <div class="panel-title"><h2>Rollback</h2></div>
        <div class="kv"><span>Status</span><strong>${escapeHtml(rollback.status || rollback.validation_status || '未记录')}</strong></div>
        <div class="kv"><span>Target</span><strong>${escapeHtml(shortPath(integrity.rollback_target || rollback.rollback_target))}</strong></div>
      </section>

      <section class="main-panel glass span-all">
        <div class="panel-title"><h2>Repair Loop Policy</h2><span>${escapeHtml(loop.execution_mode || '未记录')}</span></div>
        <pre class="json-box">${escapeHtml(JSON.stringify(loop, null, 2).slice(0, 6000))}</pre>
      </section>
    </div>
  `;
}

function qualityLabel(quality) {
  if (quality?.label) return quality.label;
  const labels = {
    ready_for_repair_dry_run: '可修复干跑',
    diagnosis_only: '诊断可用',
    partial: '部分证据',
    blocked: '阻断',
    missing: '缺少证据',
  };
  return labels[quality?.status] || '未记录';
}

function qualityTone(quality) {
  if (quality?.tone) return quality.tone;
  const tones = {
    ready_for_repair_dry_run: 'good',
    diagnosis_only: 'info',
    partial: 'warn',
    blocked: 'bad',
    missing: 'muted',
  };
  return tones[quality?.status] || 'muted';
}

function triState(value) {
  if (value === true) return { label: '通过', tone: 'good' };
  if (value === false) return { label: '失败', tone: 'bad' };
  return { label: '未知', tone: 'muted' };
}

function countValue(value) {
  return value === null || value === undefined ? '-' : value;
}

function renderEvidenceQuality(cases) {
  if (!cases.length) return '';
  return `
    <div class="quality-section">
      <div class="panel-title sub-panel-title">
        <h2>Evidence Quality</h2>
        <span>证据质量与下一步可用性</span>
      </div>
      <div class="quality-grid">
        ${cases
          .map((item) => {
            const quality = item.quality || {};
            const compile = triState(quality.compile_success);
            const render = triState(quality.render_success);
            const reasons = quality.reasons || [];
            return `
              <article class="quality-card">
                <div class="quality-card-head">
                  <strong>${escapeHtml(item.conference || 'unknown')}</strong>
                  ${chip(qualityLabel(quality), qualityTone(quality))}
                </div>
                <span class="quality-case">${escapeHtml(item.name || '未记录')}</span>
                <div class="quality-kpis">
                  <div><span>Compile</span>${chip(compile.label, compile.tone)}</div>
                  <div><span>Render</span>${chip(render.label, render.tone)}</div>
                  <div><span>Pages</span><strong>${escapeHtml(countValue(quality.page_images_count))}</strong></div>
                  <div><span>Remaining</span><strong>${escapeHtml(countValue(quality.remaining_defects))}</strong></div>
                  <div><span>Candidates</span><strong>${escapeHtml(countValue(quality.repair_candidates))}</strong></div>
                  <div><span>B2 targetable</span><strong>${escapeHtml(countValue(quality.b2_targetable))}</strong></div>
                </div>
                <div class="quality-strip">
                  ${chip(`B2 findings ${quality.b2_findings ?? 0}`, quality.b2_findings > 0 ? 'info' : 'muted')}
                  ${chip(`B2 selected ${quality.b2_selected ?? 0}`, quality.b2_selected > 0 ? 'good' : 'muted')}
                  ${item.features?.source_apply ? chip('source apply', 'info') : ''}
                  ${item.features?.rollback ? chip('rollback', 'good') : ''}
                </div>
                <p class="quality-reasons">${escapeHtml(reasons.slice(0, 4).join(' · ') || '没有质量原因记录')}</p>
              </article>
            `;
          })
          .join('')}
      </div>
    </div>
  `;
}

function renderBenchmark() {
  const benchmark = snapshot?.benchmark || {};
  const cases = benchmark.cases || [];
  const conferences = benchmark.conferences || [];
  const totals = benchmark.totals || {};
  const hasMatrix = conferences.length > 0;
  const selectedTotal = totals.selected_case_count ?? totals.case_count ?? 0;
  const poolTotal = totals.pool_case_count ?? totals.case_count ?? 0;
  return `
    <section class="main-panel glass">
      <div class="panel-title">
        <h2>Benchmark Representative Evidence</h2>
        <span>${benchmark.available ? `${totals.evidence_case_count || 0} / ${selectedTotal} selected` : '未配置'}</span>
      </div>
      <div class="benchmark-root">
        ${escapeHtml(benchmark.root || '启动 monitor 时可传入 --benchmark-root')}
        ${
          benchmark.available
            ? ` · ${escapeHtml(benchmark.selection_mode || 'selection')} · pool ${escapeHtml(poolTotal)} · config ${escapeHtml(benchmark.representative_config?.configured_count ?? 0)}`
            : ''
        }
      </div>
      ${
        hasMatrix
          ? `
            <div class="benchmark-summary">
              ${metric('会议', totals.conference_count ?? 0)}
              ${metric('代表 Case', selectedTotal)}
              ${metric('已有证据', totals.evidence_case_count ?? 0)}
              ${metric('可干跑', totals.ready_quality_cases ?? 0)}
              ${metric('诊断可用', totals.diagnosis_quality_cases ?? 0)}
              ${metric('Freshness pass', totals.freshness_pass_cases ?? 0)}
              ${metric('B2 evidence', totals.b2_evidence_cases ?? 0)}
            </div>
            <div class="benchmark-matrix">
              <div class="matrix-head">会议</div>
              <div class="matrix-head">Evidence</div>
              <div class="matrix-head">Quality</div>
              <div class="matrix-head">Freshness</div>
              <div class="matrix-head">Gatekeeper</div>
              <div class="matrix-head">Apply</div>
              <div class="matrix-head">Rollback</div>
              <div class="matrix-head">B2</div>
              <div class="matrix-head">Approval Gate</div>
              ${conferences
                .map((item) => {
                  const selected = item.selected_case || {};
                  const quality = selected.quality || {};
                  const coverageTone = item.evidence_case_count > 0 ? 'info' : 'muted';
                  const gateText = `${item.gatekeeper_continue_cases || 0}/${item.gatekeeper_blocked_cases || 0}`;
                  const gateTone = item.gatekeeper_continue_cases > 0 ? 'good' : (item.gatekeeper_blocked_cases > 0 ? 'warn' : 'muted');
                  const approvalText = `${item.candidate_gate_pass_cases || 0}/${item.candidate_gate_blocked_cases || 0}`;
                  const approvalTone = item.candidate_gate_pass_cases > 0 ? 'good' : (item.candidate_gate_blocked_cases > 0 ? 'warn' : 'muted');
                  return `
                    <div class="matrix-cell venue">
                      <strong>${escapeHtml(item.name)}</strong>
                      <span>${escapeHtml(shortPath(selected.name || '未选择'))} · pool ${escapeHtml(item.pool_case_count || 0)}</span>
                    </div>
                    <div class="matrix-cell">${chip(`${item.evidence_case_count || 0}/${item.selected_case_count || 1}`, coverageTone)}</div>
                    <div class="matrix-cell">${chip(qualityLabel(quality), qualityTone(quality))}</div>
                    <div class="matrix-cell">${chip(item.freshness_pass_cases || 0, item.freshness_pass_cases > 0 ? 'good' : 'muted')}</div>
                    <div class="matrix-cell">${chip(gateText, gateTone)}</div>
                    <div class="matrix-cell">${chip(item.source_apply_cases || 0, item.source_apply_cases > 0 ? 'info' : 'muted')}</div>
                    <div class="matrix-cell">${chip(item.rollback_cases || 0, item.rollback_cases > 0 ? 'good' : 'muted')}</div>
                    <div class="matrix-cell">${chip(item.b2_evidence_cases || 0, item.b2_evidence_cases > 0 ? 'info' : 'muted')}</div>
                    <div class="matrix-cell">${chip(approvalText, approvalTone)}</div>
                  `;
                })
                .join('')}
            </div>
          `
          : '<div class="empty">没有可读取的 benchmark evidence。</div>'
      }
      ${renderEvidenceQuality(cases)}
      <div class="benchmark-list">
        <div class="panel-title sub-panel-title">
          <h2>Representative Cases</h2>
          <span>${cases.length}</span>
        </div>
        ${cases
          .map((item) => `
            <article class="benchmark-card">
              <strong>${escapeHtml(item.conference ? `${item.conference} · ${item.name}` : item.name)}</strong>
              <span>${escapeHtml(shortPath(item.root))}</span>
              <small>${escapeHtml(item.representative_config?.selection_reason || item.representative_reason || '未记录选择原因')}</small>
              <div>
                ${chip(qualityLabel(item.quality || {}), qualityTone(item.quality || {}))}
                ${chip(`RunResult ${item.run_results}`, 'info')}
                ${chip(`StatusView ${item.status_views}`, 'muted')}
                ${item.has_evidence ? '' : chip('no evidence', 'muted')}
                ${item.features?.freshness_pass ? chip('fresh', 'good') : ''}
                ${item.features?.source_apply ? chip('apply', 'info') : ''}
                ${item.features?.rollback ? chip('rollback', 'good') : ''}
                ${item.features?.b2_evidence ? chip('B2', 'info') : ''}
              </div>
            </article>
          `)
          .join('')}
      </div>
    </section>
  `;
}

function renderArtifacts() {
  return `
    <section class="main-panel glass">
      <div class="panel-title"><h2>Artifacts</h2><span>${snapshot?.artifacts?.length || 0}</span></div>
      <div class="artifact-list">
        ${(snapshot?.artifacts || [])
          .map((artifact) => `
            <div class="artifact-row">
              ${chip(artifact.exists ? 'found' : 'missing', artifact.exists ? 'good' : 'muted')}
              <strong>${escapeHtml(artifact.key)}</strong>
              <code>${escapeHtml(artifact.path || '未记录')}</code>
            </div>
          `)
          .join('')}
      </div>
    </section>
  `;
}

function renderTab() {
  if (!snapshot) {
    return '<section class="main-panel glass"><div class="empty tall">正在读取 PaperFit artifacts...</div></section>';
  }
  switch (selectedTab) {
    case 'timeline':
      return renderTimeline();
    case 'visual':
      return renderVisual();
    case 'repair':
      return renderRepair();
    case 'safety':
      return renderSafety();
    case 'benchmark':
      return renderBenchmark();
    case 'overview':
    default:
      return `${renderOverview()}${renderArtifacts()}`;
  }
}

function bindEvents() {
  document.querySelectorAll('[data-tab]').forEach((button) => {
    button.addEventListener('click', () => {
      selectedTab = button.dataset.tab;
      render();
    });
  });
  document.querySelectorAll('[data-image]').forEach((button) => {
    button.addEventListener('click', () => {
      selectedPagePath = button.dataset.image;
      render();
    });
  });
  document.querySelectorAll('[data-page]').forEach((button) => {
    button.addEventListener('click', () => {
      const page = Number(button.dataset.page);
      const target = currentImages().find((image) => image.page === page);
      if (target) {
        selectedPagePath = target.path;
        selectedTab = 'visual';
        render();
      }
    });
  });
  document.getElementById('refreshBtn')?.addEventListener('click', fetchSnapshot);
  document.getElementById('pauseBtn')?.addEventListener('click', () => {
    paused = !paused;
    render();
  });
}

function render() {
  app.innerHTML = renderShell();
  bindEvents();
}

render();
fetchSnapshot();
setInterval(fetchSnapshot, 1000);
