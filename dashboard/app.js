/**
 * GreenOps Dashboard v2.0
 * Auto-refresh every 10s | Toast notifications | Sleep/Shutdown commands
 * Uses relative URLs — works behind nginx proxy or direct.
 */

class GreenOpsApp {
  constructor() {
    this.apiUrl = '';
    this.token = localStorage.getItem('greenops_token');
    this.currentUser = localStorage.getItem('greenops_user');
    this.machines = [];
    this.filterStatus = '';
    this.searchQuery = '';
    this.refreshInterval = null;
    this.REFRESH_MS = 10_000;
    this._init();
  }

  _init() {
    this._bindLogin();
    this._bindChangePw();
    this._bindDashboard();
    if (this.token) { this._verifyToken(); } else { this._showScreen('login'); }
  }

  _showScreen(name) {
    document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
    const ids = { login: 'login-screen', changepw: 'change-pw-screen', dashboard: 'dashboard-screen' };
    document.getElementById(ids[name])?.classList.add('active');
  }

  _bindLogin() {
    document.getElementById('login-form')?.addEventListener('submit', e => {
      e.preventDefault(); this._handleLogin();
    });
  }

  async _handleLogin() {
    const username = document.getElementById('username')?.value.trim() || '';
    const password = document.getElementById('password')?.value || '';
    const errorEl  = document.getElementById('login-error');
    const errorTxt = document.getElementById('login-error-text');
    const btn      = document.getElementById('login-btn');
    if (!username || !password) return;

    this._setBtnLoading(btn, true);
    errorEl?.classList.add('hidden');

    try {
      const res  = await fetch(`${this.apiUrl}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json().catch(() => ({}));

      if (res.ok) {
        this.token = data.token;
        this.currentUser = data.username || username;
        localStorage.setItem('greenops_token', data.token);
        localStorage.setItem('greenops_user', this.currentUser);
        if (data.must_change_password) {
          this._showScreen('changepw');
          const el = document.getElementById('cur-pw');
          if (el) el.value = password;
        } else {
          this._initDashboard();
        }
      } else {
        if (errorTxt) errorTxt.textContent = data.error || 'Invalid credentials';
        errorEl?.classList.remove('hidden');
      }
    } catch {
      if (errorTxt) errorTxt.textContent = 'Cannot connect to server.';
      errorEl?.classList.remove('hidden');
    } finally {
      this._setBtnLoading(btn, false);
    }
  }

  _setBtnLoading(btn, on) {
    if (!btn) return;
    btn.disabled = on;
    const text = btn.querySelector('.btn-text');
    const spinner = btn.querySelector('.btn-spinner');
    if (text) text.style.opacity = on ? '0.5' : '1';
    if (spinner) spinner.classList.toggle('hidden', !on);
  }

  _bindChangePw() {
    document.getElementById('change-pw-form')?.addEventListener('submit', e => {
      e.preventDefault(); this._handleChangePw();
    });
  }

  async _handleChangePw() {
    const curPw = document.getElementById('cur-pw')?.value || '';
    const newPw = document.getElementById('new-pw')?.value || '';
    const cnfPw = document.getElementById('confirm-pw')?.value || '';
    const errEl = document.getElementById('change-pw-error');
    const errTx = document.getElementById('change-pw-error-text');

    errEl?.classList.add('hidden');
    if (newPw !== cnfPw) {
      if (errTx) errTx.textContent = 'Passwords do not match.';
      errEl?.classList.remove('hidden'); return;
    }
    if (newPw.length < 8) {
      if (errTx) errTx.textContent = 'Min. 8 characters.';
      errEl?.classList.remove('hidden'); return;
    }

    try {
      const res = await fetch(`${this.apiUrl}/api/auth/change-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
        body: JSON.stringify({ current_password: curPw, new_password: newPw }),
      });
      if (res.ok) {
        this._toast('Password updated.', 'ok');
        this._initDashboard();
      } else {
        const d = await res.json().catch(() => ({}));
        if (errTx) errTx.textContent = d.error || 'Failed.';
        errEl?.classList.remove('hidden');
      }
    } catch {
      if (errTx) errTx.textContent = 'Cannot connect.';
      errEl?.classList.remove('hidden');
    }
  }

  async _verifyToken() {
    try {
      const res = await fetch(`${this.apiUrl}/api/auth/verify`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (res.ok) { this._initDashboard(); } else { this._logout(); }
    } catch { this._showScreen('login'); }
  }

  _initDashboard() {
    this._showScreen('dashboard');
    const uEl = document.getElementById('sidebar-user');
    if (uEl) uEl.textContent = this.currentUser || 'admin';
    const iEl = document.getElementById('sidebar-user-initial');
    if (iEl) iEl.textContent = (this.currentUser || 'A')[0].toUpperCase();
    this._showSkeletons();
    this._loadDashboard();
    this._startRefresh();
  }

  _bindDashboard() {
    document.getElementById('logout-btn')?.addEventListener('click', () => this._logout());
    document.getElementById('refresh-btn')?.addEventListener('click', () => {
      const btn = document.getElementById('refresh-btn');
      btn?.classList.add('spinning');
      this._loadDashboard().finally(() => setTimeout(() => btn?.classList.remove('spinning'), 600));
    });
    document.getElementById('search-input')?.addEventListener('input', e => {
      this.searchQuery = e.target.value.toLowerCase().trim();
      this._renderMachines();
    });
    document.querySelectorAll('.pill').forEach(p => {
      p.addEventListener('click', () => {
        document.querySelectorAll('.pill').forEach(x => x.classList.remove('active'));
        p.classList.add('active');
        this.filterStatus = p.dataset.status || '';
        this._renderMachines();
      });
    });
  }

  _logout() {
    this.token = null; this.currentUser = null;
    localStorage.removeItem('greenops_token');
    localStorage.removeItem('greenops_user');
    this._stopRefresh();
    this._showScreen('login');
    const p = document.getElementById('password');
    if (p) p.value = '';
  }

  async _loadDashboard() {
    if (!this.token) return;
    await Promise.allSettled([this._loadStats(), this._loadMachines()]);
  }

  async _loadStats() {
    try {
      const res = await fetch(`${this.apiUrl}/api/dashboard/stats`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (!res.ok) { if (res.status === 401) { this._logout(); } return; }
      const s = await res.json();
      this._setText('stat-total',   s.total_machines   ?? '—');
      this._setText('stat-online',  s.online_machines  ?? '—');
      this._setText('stat-idle',    s.idle_machines    ?? '—');
      this._setText('stat-offline', s.offline_machines ?? '—');
      this._setText('stat-energy',  `${(s.total_energy_wasted_kwh || 0).toFixed(3)} kWh`);
      this._setText('stat-cost',    `$${(s.estimated_cost_usd || 0).toFixed(2)} estimated cost`);
      const t = s.total_machines || 1;
      this._setW('bar-online',  ((s.online_machines  || 0) / t) * 100);
      this._setW('bar-idle',    ((s.idle_machines    || 0) / t) * 100);
      this._setW('bar-offline', ((s.offline_machines || 0) / t) * 100);
    } catch { /* keep previous values on transient error */ }
  }

  async _loadMachines() {
    try {
      const res = await fetch(`${this.apiUrl}/api/machines`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (!res.ok) { if (res.status === 401) { this._logout(); } return; }
      const d = await res.json();
      this.machines = Array.isArray(d.machines) ? d.machines : [];
      this._renderMachines();
    } catch { /* keep previous render */ }
  }

  _showSkeletons() {
    const g = document.getElementById('machine-grid');
    if (g) g.innerHTML = Array.from({ length: 6 }).map(() => '<div class="skeleton-card"></div>').join('');
  }

  _renderMachines() {
    const grid = document.getElementById('machine-grid');
    if (!grid) return;
    let list = [...this.machines];
    if (this.filterStatus) list = list.filter(m => m.status === this.filterStatus);
    if (this.searchQuery) list = list.filter(m =>
      (m.hostname || '').toLowerCase().includes(this.searchQuery) ||
      (m.mac_address || '').toLowerCase().includes(this.searchQuery) ||
      (m.os_type || '').toLowerCase().includes(this.searchQuery)
    );

    if (!list.length) {
      grid.innerHTML = `<div class="empty-state">
        <div class="empty-state-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="opacity:.5"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
        </div>
        <h3>No machines found</h3>
        <p>${this.filterStatus || this.searchQuery ? 'Try adjusting your search or filter.' : 'No machines have registered yet.'}</p>
      </div>`;
      return;
    }

    grid.innerHTML = list.map((m, i) => this._cardHtml(m, i)).join('');
    grid.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', () => {
        const { machineId, action } = btn.dataset;
        if (machineId && action) this._sendCommand(parseInt(machineId, 10), action, btn);
      });
    });
  }

  _cardHtml(m, i) {
    const badge = this._badgeHtml(m.status);
    const last  = this._relTime(m.last_seen);
    const up    = this._fmtUp(m.uptime_seconds ?? m.uptime_hours);
    const idle  = this._fmtDur(m.total_idle_seconds || 0);
    const kwh   = (m.energy_wasted_kwh || 0).toFixed(3);
    const can   = m.status !== 'offline';
    const delay = `animation-delay:${Math.min(i * 0.05, 0.4)}s`;
    const sc    = `status-${m.status || 'offline'}`;

    const moonIcon  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;
    const powerIcon = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>`;
    const clockIcon = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="opacity:.7"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`;

    return `<div class="machine-card ${sc}" style="${delay}">
      <div class="card-head">
        <div style="min-width:0;flex:1">
          <div class="card-title" title="${this._e(m.hostname)}">${this._e(m.hostname || 'Unknown')}</div>
          <div class="card-os">${this._e(m.os_type || '—')}</div>
          <div class="card-mac">${this._e(m.mac_address || '—')}</div>
        </div>
        ${badge}
      </div>
      <div class="card-metrics">
        <div class="metric"><span class="metric-val">${this._e(up)}</span><span class="metric-lbl">Uptime</span></div>
        <div class="metric"><span class="metric-val">${this._e(idle)}</span><span class="metric-lbl">Idle</span></div>
        <div class="metric"><span class="metric-val">${this._e(kwh)}</span><span class="metric-lbl">kWh</span></div>
      </div>
      <div class="card-last-seen">${clockIcon}<span>Last seen ${this._e(last)}</span></div>
      <div class="card-actions">
        <button class="action-btn btn-sleep" data-action="sleep" data-machine-id="${m.id}" ${!can ? 'disabled' : ''}>
          ${moonIcon} Sleep
        </button>
        <button class="action-btn btn-shutdown" data-action="shutdown" data-machine-id="${m.id}" ${!can ? 'disabled' : ''}>
          ${powerIcon} Shutdown
        </button>
      </div>
    </div>`;
  }

  _badgeHtml(status) {
    const map = {
      online:  ['badge-online',  'online-dot',  'Online'],
      idle:    ['badge-idle',    'idle-dot',    'Idle'],
      offline: ['badge-offline', 'offline-dot', 'Offline'],
    };
    const [cls, dot, label] = map[status] || map.offline;
    return `<span class="status-badge ${cls}"><span class="status-dot ${dot}"></span>${label}</span>`;
  }

  async _sendCommand(machineId, action, btn) {
    const label = action === 'sleep' ? 'Sleep' : 'Shutdown';
    if (!confirm(`Send ${label} command?\n\nThe agent will execute this on its next heartbeat poll.`)) return;

    const prev = btn.innerHTML;
    btn.classList.add('loading'); btn.disabled = true;
    btn.innerHTML = `<svg class="spin" width="13" height="13" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="2" stroke-dasharray="30" stroke-dashoffset="10"/></svg> Sending…`;

    try {
      const res = await fetch(`${this.apiUrl}/api/machines/${machineId}/${action}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (res.ok) {
        this._toast(`${label} command queued.`, 'ok');
        setTimeout(() => this._loadDashboard(), 3000);
      } else {
        const d = await res.json().catch(() => ({}));
        this._toast(d.error || `Failed to send ${label}.`, 'err');
      }
    } catch { this._toast('Cannot connect to server.', 'err'); }
    finally {
      btn.classList.remove('loading');
      btn.innerHTML = prev;
      btn.disabled = false;
    }
  }

  _startRefresh() {
    this._stopRefresh();
    this.refreshInterval = setInterval(() => this._loadDashboard(), this.REFRESH_MS);
  }
  _stopRefresh() {
    if (this.refreshInterval) { clearInterval(this.refreshInterval); this.refreshInterval = null; }
  }

  _toast(msg, type = 'ok') {
    const c = document.getElementById('toast-container');
    if (!c) return;
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    c.appendChild(el);
    const rm = () => { el.classList.add('toast-leaving'); setTimeout(() => el.remove(), 220); };
    const t = setTimeout(rm, 3500);
    el.addEventListener('click', () => { clearTimeout(t); rm(); });
  }

  _setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
  _setW(id, pct)  { const el = document.getElementById(id); if (el) el.style.width = `${Math.max(0, Math.min(100, pct))}%`; }
  _e(t) {
    if (t == null) return '';
    const d = document.createElement('div');
    d.textContent = String(t);
    return d.innerHTML;
  }

  _relTime(ts) {
    if (!ts) return 'never';
    const d = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
    if (isNaN(d) || d < 0) return 'just now';
    if (d < 10)    return 'just now';
    if (d < 60)    return `${d}s ago`;
    if (d < 3600)  return `${Math.floor(d / 60)}m ago`;
    if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
    return `${Math.floor(d / 86400)}d ago`;
  }

  _fmtUp(val) {
    if (val == null || val === '') return '—';
    let s = Number(val);
    if (isNaN(s)) return '—';
    // If passed as hours (small float with decimal), convert to seconds
    if (s < 1000 && String(val).includes('.') && s !== 0) s = s * 3600;
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m`;
    return `${Math.floor(s)}s`;
  }

  _fmtDur(secs) {
    const s = Number(secs) || 0;
    if (!s) return '0m';
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }
}

document.addEventListener('DOMContentLoaded', () => { window.app = new GreenOpsApp(); });
