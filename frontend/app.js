/* ═══════════════════════════════════════════════════════════════════════════
   app.js — AI Productivity Agent Dashboard
   ═══════════════════════════════════════════════════════════════════════════ */

'use strict';

// ── Config ─────────────────────────────────────────────────────────────────────
let API_BASE = localStorage.getItem('apiBase') || 'http://localhost:8000';
let autonomyLevel = localStorage.getItem('autonomy') || 'autonomous';
let currentSessionId = localStorage.getItem('currentSession') || null;

// ── State ──────────────────────────────────────────────────────────────────────
const state = {
    plan: null,
    monitorReport: null,
    reflection: null,
    productivityScore: null,
};

// ── Helpers ─────────────────────────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
    const url = `${API_BASE}${path}`;
    const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const d = await res.json(); msg = d.detail || d.message || msg; } catch { }
        throw new Error(msg);
    }
    return res.json();
}

function showToast(msg, type = 'info') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = `toast ${type}`;
    t.classList.remove('hidden');
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.add('hidden'), 4000);
}

function el(id) { return document.getElementById(id); }

function priorityTag(p) {
    const map = { high: 'red', medium: 'amber', low: 'green' };
    return `<span class="tag tag-${map[p] || 'cyan'}">${p}</span>`;
}

function actionTag(a) {
    const map = { deep_work: 'purple', meeting: 'cyan', review: 'amber', admin: 'cyan', research: 'green' };
    return `<span class="tag tag-${map[a] || 'cyan'}">${a.replace('_', ' ')}</span>`;
}

// ── Task Completion ────────────────────────────────────────────────────────────────────
async function markTaskDone(taskId, done) {
    try {
        await apiFetch('/task/complete', {
            method: 'POST',
            body: JSON.stringify({ task_id: taskId, done }),
        });
        showToast(done ? '✅ Task marked as done!' : '🔄 Task marked as pending.', done ? 'success' : 'info');
        // Refresh today view counts + task styling
        await loadToday();
    } catch (err) {
        showToast(`Could not update task: ${err.message}`, 'error');
    }
}

function taskCompleteBtn(taskId, isDone) {
    if (isDone) {
        return `<button class="btn-done done" onclick="markTaskDone('${taskId}', false)" title="Click to undo">✅ Done</button>`;
    }
    return `<button class="btn-done" onclick="markTaskDone('${taskId}', true)" title="Mark as complete">○ Mark Done</button>`;
}

function fmtDatetime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

// ── Clock ──────────────────────────────────────────────────────────────────────
function updateClock() {
    const now = new Date();
    el('clock').textContent = now.toLocaleTimeString(undefined, { hour12: false });
}
setInterval(updateClock, 1000);
updateClock();

// ── Mobile Sidebar ──────────────────────────────────────────────────────────────────
function toggleSidebar() {
    const sidebar = el('sidebar');
    const overlay = el('sidebarOverlay');
    sidebar.classList.toggle('sidebar-open');
    overlay.classList.toggle('active');
    document.body.classList.toggle('sidebar-locked');
}

function closeSidebar() {
    const sidebar = el('sidebar');
    const overlay = el('sidebarOverlay');
    sidebar.classList.remove('sidebar-open');
    overlay.classList.remove('active');
    document.body.classList.remove('sidebar-locked');
}

// ── Tab Switching ──────────────────────────────────────────────────────────────
const PAGE_TITLES = {
    dashboard: ['Dashboard', 'Your AI-powered productivity command centre'],
    goal: ['New Goal', 'Set a goal and let the AI plan it'],
    today: ["Today's Plan", new Date().toDateString()],
    review: ['Weekly Review', 'AI-generated insights on your productivity'],
    log: ['Action Log', 'Complete audit trail of all agent actions'],
    settings: ['Settings', 'Configure the system'],
};

function switchTab(tab, btnEl) {
    document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    const section = el(`tab-${tab}`);
    if (section) section.classList.add('active');
    if (btnEl) btnEl.classList.add('active');

    // Auto-close sidebar on mobile after selecting a tab
    if (window.innerWidth <= 768) closeSidebar();
    const [title, sub] = PAGE_TITLES[tab] || [tab, ''];
    el('pageTitle').textContent = title;
    el('pageSubtitle').textContent = sub;

    if (tab === 'today') loadToday();
    if (tab === 'review') loadWeeklyReview();
    if (tab === 'log') loadLog();
}

// ── Auth Status ────────────────────────────────────────────────────────────────
async function checkAuth() {
    try {
        const data = await apiFetch('/auth/status');
        const dot = el('authDot');
        const lbl = el('authLabel');
        const btn = el('loginBtn');
        if (data.authenticated) {
            dot.className = 'status-dot connected';
            lbl.textContent = 'Google connected';
            btn.textContent = 'Reconnect';
        } else {
            dot.className = 'status-dot disconnected';
            lbl.textContent = 'Not connected';
            btn.textContent = 'Connect Google';
        }
    } catch { /* backend may not be running yet */ }
}

// ── Autonomy ───────────────────────────────────────────────────────────────────
function setAutonomy(level, btnEl) {
    autonomyLevel = level;
    localStorage.setItem('autonomy', level);
    document.querySelectorAll('.autonomy-btn').forEach(b => b.classList.remove('active'));
    btnEl.classList.add('active');
}

// ── Submit Goal ────────────────────────────────────────────────────────────────
async function submitGoal() {
    const goal = el('goalInput').value.trim();
    if (!goal) { showToast('Please enter a goal.', 'error'); return; }

    const btn = el('submitGoalBtn');
    const text = el('submitBtnText');
    const loader = el('submitLoader');

    btn.disabled = true;
    text.classList.add('hidden');
    loader.classList.remove('hidden');

    try {
        const deadlineRaw = el('deadlineInput').value;
        const deadline = deadlineRaw ? new Date(deadlineRaw).toISOString() : null;
        const userEmail = el('emailInput').value.trim() || null;

        const result = await apiFetch('/goal', {
            method: 'POST',
            body: JSON.stringify({ goal, deadline, user_email: userEmail, autonomy_level: autonomyLevel }),
        });

        currentSessionId = result.session_id;
        localStorage.setItem('currentSession', currentSessionId);
        state.plan = result.plan;
        state.monitorReport = result.monitor_report;

        renderPlanResult(result);
        showToast('✅ Agents executed successfully!', 'success');
        refreshDashboard();

    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    } finally {
        btn.disabled = false;
        text.classList.remove('hidden');
        loader.classList.add('hidden');
    }
}

function renderPlanResult(result) {
    const resultDiv = el('goalResult');
    resultDiv.classList.remove('hidden');

    // Status badge
    const statusMap = {
        planned: ['info', '📋 Planned'],
        executed: ['success', '⚡ Executed'],
        awaiting_approval: ['warning', '⏳ Awaiting Approval'],
        monitored: ['success', '✅ Monitored'],
        replan_needed: ['warning', '🔄 Replanning'],
        reflected: ['success', '🪞 Reflected'],
        error: ['error', '❌ Error'],
    };
    const [badgeClass, badgeText] = statusMap[result.status] || ['info', result.status];
    el('planStatus').className = `badge badge-${badgeClass}`;
    el('planStatus').textContent = badgeText;

    // Plan summary
    const plan = result.plan || {};
    el('planSummary').innerHTML = `
    <div class="plan-meta">
      <span>🎯 <b>${plan.goal || '—'}</b></span>
      <span>⏰ <b>${plan.total_estimated_hours || '?'}h</b> total</span>
      <span>📅 Deadline: <b>${fmtDatetime(plan.deadline) || 'Not set'}</b></span>
      <span>📝 <b>${(plan.subtasks || []).length}</b> subtasks</span>
    </div>
    ${plan.notes ? `<p class="muted" style="margin-top:10px;font-size:12px">${plan.notes}</p>` : ''}
  `;

    // Subtasks
    const subtaskList = el('subtaskList');
    subtaskList.innerHTML = (plan.subtasks || []).map(t => `
    <div class="subtask-item">
      <div class="subtask-title">${t.title}</div>
      <div class="subtask-desc">${t.description || ''}</div>
      <div class="subtask-meta">
        ${priorityTag(t.priority)}
        ${actionTag(t.action_type)}
        <span class="tag tag-cyan">⏱ ${t.estimated_hours}h</span>
        ${t.scheduled_start ? `<span class="tag tag-purple">📅 ${fmtDatetime(t.scheduled_start)}</span>` : ''}
      </div>
    </div>
  `).join('');

    // Approval panel (manual / assisted)
    const approvalCard = el('approvalCard');
    if (result.status === 'awaiting_approval' && result.pending_approvals?.length) {
        approvalCard.classList.remove('hidden');
        el('approvalList').innerHTML = result.pending_approvals.map(t => `
      <div class="approval-item">
        <input type="checkbox" id="approve_${t.id}" value="${t.id}" checked />
        <label for="approve_${t.id}">
          <div class="approval-title">${t.title}</div>
          <div class="approval-desc">${t.description || ''} — Est: ${t.estimated_hours}h | Priority: ${t.priority}</div>
        </label>
      </div>
    `).join('');
    } else {
        approvalCard.classList.add('hidden');
    }
}

// ── Approve Actions ────────────────────────────────────────────────────────────
async function approveActions() {
    if (!currentSessionId) { showToast('No active session.', 'error'); return; }
    const checked = [...document.querySelectorAll('#approvalList input[type=checkbox]:checked')]
        .map(cb => cb.value);
    if (!checked.length) { showToast('Select at least one task.', 'error'); return; }

    try {
        const result = await apiFetch('/approve-action', {
            method: 'POST',
            body: JSON.stringify({ session_id: currentSessionId, approved_task_ids: checked }),
        });
        renderPlanResult(result);
        refreshDashboard();
        showToast('✅ Actions approved & executed!', 'success');
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

// ── Today's Plan ───────────────────────────────────────────────────────────────
async function loadToday() {
    try {
        const data = await apiFetch('/today');

        // Dashboard stats
        const plan = data.plan || {};
        const subtasks = plan.subtasks || [];
        const active = subtasks.filter(t => t.status !== 'done');
        const completed = subtasks.filter(t => t.status === 'done');

        el('statGoalVal').textContent = plan.goal
            ? (plan.goal.length > 30 ? plan.goal.slice(0, 30) + '…' : plan.goal)
            : '—';
        el('statDone').textContent = completed.length;
        el('statPending').textContent = active.length;
        el('statScore').textContent = data.productivity_score ? `${data.productivity_score}/10` : '—';
        el('topScore').textContent = data.productivity_score || '—';

        // ── Dashboard quick-view chips ──────────────────────────────────────
        const taskListEl = el('todayTaskList');
        if (!subtasks.length) {
            taskListEl.innerHTML = '<p class="muted">No tasks found. Submit a goal first.</p>';
        } else {
            const activeChips = active.map(t => `
          <div class="task-chip">
            <span class="priority-dot priority-${t.priority}"></span>
            <span style="flex:1">${t.title}</span>
            <span class="tag tag-cyan">${t.estimated_hours}h</span>
            ${taskCompleteBtn(t.id, false)}
          </div>`).join('');

            const doneChips = completed.length ? `
          <div class="section-divider"><span>✅ Completed (${completed.length})</span></div>
          ${completed.map(t => `
          <div class="task-chip task-done">
            <span class="priority-dot priority-${t.priority}"></span>
            <span style="flex:1;text-decoration:line-through;opacity:0.45">${t.title}</span>
            <span class="tag tag-green">Done</span>
            ${taskCompleteBtn(t.id, true)}
          </div>`).join('')}` : '';

            taskListEl.innerHTML = activeChips + doneChips;
        }

        // ── Today's Plan tab — full cards ───────────────────────────────────
        const todayFull = el('todayFull');
        if (todayFull) {
            if (!subtasks.length) {
                todayFull.innerHTML = '<p class="muted">No tasks. Add a goal to get started.</p>';
            } else {
                const renderCard = (t, isDone) => `
            <div class="subtask-item ${isDone ? 'subtask-done' : ''}">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
                <div style="flex:1">
                  <div class="subtask-title" style="${isDone ? 'text-decoration:line-through;opacity:0.5' : ''}">${t.title}</div>
                  <div class="subtask-desc">${t.description || ''}</div>
                  <div class="subtask-meta">
                    ${priorityTag(t.priority)} ${actionTag(t.action_type)}
                    <span class="tag tag-cyan">⏱ ${t.estimated_hours}h</span>
                    ${isDone ? '<span class="tag tag-green">✓ Completed</span>' : ''}
                  </div>
                </div>
                ${taskCompleteBtn(t.id, isDone)}
              </div>
            </div>`;

                const activeSection = active.length
                    ? active.map(t => renderCard(t, false)).join('')
                    : '<p class="muted" style="padding:12px 0">All tasks completed! 🎉</p>';

                const doneSection = completed.length ? `
            <div class="completed-section">
              <button class="completed-toggle" onclick="this.parentElement.classList.toggle('open')">
                <span>✅ Completed Tasks (${completed.length})</span>
                <span class="toggle-arrow">▸</span>
              </button>
              <div class="completed-body">
                ${completed.map(t => renderCard(t, true)).join('')}
              </div>
            </div>` : '';

                todayFull.innerHTML = activeSection + doneSection;
            }
        }

        // Calendar events
        renderCalendarEvents(data.calendar_events || [], 'calendarSection');
        renderCalendarEvents(data.calendar_events || [], 'todayCalendarFull');

    } catch (err) {
        showToast(`Could not load today's plan: ${err.message}`, 'error');
    }

}

function renderCalendarEvents(events, targetId) {
    const container = el(targetId);
    if (!container) return;
    if (!events.length) {
        container.innerHTML = '<p class="muted">No calendar events found (authentication required).</p>';
        return;
    }
    container.innerHTML = events.map(e => {
        const start = e.start?.dateTime || e.start?.date || '';
        return `
      <div class="cal-event">
        <div class="cal-time">${start ? fmtTime(start) : '—'}</div>
        <div>
          <div class="cal-title">${e.summary || '(no title)'}</div>
          <div class="cal-desc">${e.description || ''}</div>
        </div>
      </div>`;
    }).join('');
}

// ── Weekly Review ──────────────────────────────────────────────────────────────
async function loadWeeklyReview() {
    const container = el('reviewContent');
    try {
        const rev = await apiFetch('/weekly-review');
        state.reflection = rev;

        const recs = Array.isArray(rev.recommendations)
            ? rev.recommendations
            : (rev.recommendations || '').split('\n').filter(Boolean);
        const patterns = Array.isArray(rev.patterns) ? rev.patterns : [];

        container.innerHTML = `
      <div class="card">
        <div class="card-header">
          <h2>🔍 ${rev.week_label || 'Weekly Review'}</h2>
          <span class="badge badge-success">Completed</span>
        </div>
        <div class="review-hero">
          <div class="review-stat">
            <div class="review-stat-value">${rev.productivity_score || '—'}</div>
            <div class="review-stat-label">Productivity Score</div>
          </div>
          <div class="review-stat">
            <div class="review-stat-value">${rev.completed_tasks ?? '—'}</div>
            <div class="review-stat-label">Tasks Completed</div>
          </div>
          <div class="review-stat">
            <div class="review-stat-value">${rev.incomplete_tasks ?? '—'}</div>
            <div class="review-stat-label">Incomplete</div>
          </div>
        </div>

        <h3 style="font-size:14px;font-weight:700;margin-bottom:10px">🏆 Accomplishments</h3>
        <div class="insight-block">${rev.accomplishments || '—'}</div>

        <h3 style="font-size:14px;font-weight:700;margin:16px 0 10px">💡 Insights</h3>
        <div class="insight-block">${(rev.insights || '').replace(/\n/g, '<br>')}</div>

        ${patterns.length ? `
        <h3 style="font-size:14px;font-weight:700;margin:16px 0 10px">🔄 Patterns</h3>
        <ul class="rec-list">${patterns.map(p => `<li>${p}</li>`).join('')}</ul>
        ` : ''}

        <h3 style="font-size:14px;font-weight:700;margin:16px 0 10px">🎯 Recommendations</h3>
        <ul class="rec-list">${recs.map(r => `<li>${r}</li>`).join('')}</ul>

        ${rev.habit_suggestion ? `
        <h3 style="font-size:14px;font-weight:700;margin:16px 0 8px">💡 Habit Suggestion</h3>
        <div class="insight-block">${rev.habit_suggestion}</div>
        ` : ''}

        ${rev.next_week_focus ? `
        <h3 style="font-size:14px;font-weight:700;margin:16px 0 8px">🚀 Next Week Focus</h3>
        <div class="insight-block">${rev.next_week_focus}</div>
        ` : ''}
      </div>`;

    } catch (err) {
        container.innerHTML = `
      <div class="card">
        <div class="card-header"><h2>🔍 Weekly Review</h2></div>
        <p class="muted" style="text-align:center;padding:40px">
          No weekly review yet. Submit a goal to generate your first review.
        </p>
      </div>`;
    }
}

// ── Monitor ────────────────────────────────────────────────────────────────────
async function loadMonitorReport() {
    try {
        const scoreData = await apiFetch('/productivity-score');
        el('statScore').textContent = scoreData.productivity_score != null
            ? `${scoreData.productivity_score}/10` : '—';
        el('topScore').textContent = scoreData.productivity_score ?? '—';

        const health = scoreData.health || 'unknown';
        const healthBadge = el('healthBadge');
        if (healthBadge) {
            healthBadge.textContent = health.replace('_', ' ');
            healthBadge.className = `health-badge health-${health}`;
        }
    } catch { /* ok */ }
}

// ── Action Log ─────────────────────────────────────────────────────────────────
async function loadLog() {
    const container = el('logContent');
    try {
        const data = await apiFetch('/action-log');
        const logs = data.action_log || [];
        if (!logs.length) {
            container.innerHTML = '<p class="muted">No actions logged yet.</p>';
            return;
        }
        container.innerHTML = [...logs].reverse().map(entry => {
            const success = entry.success;
            const cls = success === true ? 'log-success' : success === false ? 'log-error' : 'log-info';
            const statusText = success === true ? '✅ OK' : success === false ? '❌ Fail' : 'ℹ';
            const action = entry.action || JSON.stringify(entry);
            const detail = entry.event_link
                ? `<a href="${entry.event_link}" target="_blank" style="color:var(--accent)">Open →</a>`
                : (entry.error || entry.email || '');
            return `
        <div class="log-entry ${cls}">
          <span class="log-status">${statusText}</span>
          <span class="log-action">${action}${entry.task_id ? ` (${entry.task_id})` : ''}</span>
          <span style="color:var(--text-muted);font-size:11px">${detail}</span>
        </div>`;
        }).join('');
    } catch (err) {
        container.innerHTML = `<p class="muted">Error loading log: ${err.message}</p>`;
    }
}

// ── Dashboard Refresh ──────────────────────────────────────────────────────────
async function refreshDashboard() {
    await Promise.allSettled([loadToday(), loadMonitorReport()]);
}

// ── Settings Persistence ───────────────────────────────────────────────────────
el('defaultAutonomy').addEventListener('change', function () {
    autonomyLevel = this.value;
    localStorage.setItem('autonomy', this.value);
});

el('apiBaseUrl').addEventListener('blur', function () {
    API_BASE = this.value.replace(/\/$/, '');
    localStorage.setItem('apiBase', API_BASE);
    showToast('API base URL updated.', 'success');
});

// ── Startup ────────────────────────────────────────────────────────────────────
(async function init() {
    // Restore settings
    el('defaultAutonomy').value = autonomyLevel;
    el('apiBaseUrl').value = API_BASE;

    // Restore autonomy button
    document.querySelectorAll('.autonomy-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.level === autonomyLevel);
    });

    await checkAuth();
    await refreshDashboard();
    setInterval(checkAuth, 60_000);
})();
