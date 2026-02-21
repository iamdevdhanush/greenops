// GreenOps Dashboard v2.0
// Polling every 10s, sleep/shutdown commands, password change flow

class GreenOpsApp {
  constructor() {
    this.apiUrl = window.location.origin;
    this.token = localStorage.getItem('greenops_token');
    this.currentUser = localStorage.getItem('greenops_user');
    this.machines = [];
    this.filterStatus = '';
    this.searchQuery = '';
    this.refreshInterval = null;
    this.REFRESH_MS = 10_000;

    lucide.createIcons();
    this.init();
  }

  // ── Bootstrap ────────────────────────────────────────────────────────────

  init() {
    this._bindLogin();
    this._bindChangePw();
    this._bindDashboard();

    if (this.token) {
      this._verifyToken();
    } else {
      this._showScreen('login');
    }
  }

  // ── Screen management ─────────────────────────────────────────────────────

  _showScreen(name) {
    document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
    const target = {
      login: 'login-screen',
      changepw: 'change-pw-screen',
      dashboard: 'dashboard-screen',
    }[name];
    document.getElementById(target)?.classList.add('active');
    if (name === 'dashboard') {
      lucide.createIcons();
    }
  }

  // ── Login ─────────────────────────────────────────────────────────────────

  _bindLogin() {
    document.getElementById('login-form')?.addEventListener('submit', e => {
      e.preventDefault();
      this._handleLogin();
    });
  }

  async _handleLogin() {
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    const errDiv = document.getElementById('login-error');
    const btn = document.querySelector('#login-form .btn-primary');
    const btnText = btn.querySelector('.btn-text');
    const spinner = btn.querySelector('.btn-spinner');

    errDiv.textContent = '';
    errDiv.classList.add('hidden');
    btnText.style.opacity = '0';
    spinner.classList.remove('hidden');
    btn.disabled = true;

    try {
      const res = await fetch(`${this.apiUrl}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      const data = await res.json();

      if (res.ok) {
        this.token = data.token;
        this.currentUser = data.username;
        localStorage.setItem('greenops_token', data.token);
        localStorage.setItem('greenops_user', data.username);

        if (data.must_change_password) {
          // Store password typed so user doesn't retype current
          this._loginPassword = password;
          this._showScreen('changepw');
          document.getElementById('cur-pw').value = password;
        } else {
          this._initDashboard();
        }
      } else {
        errDiv.textContent = data.error || 'Login failed';
        errDiv.classList.remove('hidden');
      }
    } catch {
      errDiv.textContent = 'Cannot connect to server';
      errDiv.classList.remove('hidden');
    } finally {
      btnText.style.opacity = '1';
      spinner.classList.add('hidden');
      btn.disabled = false;
    }
  }

  // ── Change Password ───────────────────────────────────────────────────────

  _bindChangePw() {
    document.getElementById('change-pw-form')?.addEventListener('submit', e => {
      e.preventDefault();
      this._handleChangePw();
    });
  }

  async _handleChangePw() {
    const curPw = document.getElementById('cur-pw').value;
    const newPw = document.getElementById('new-pw').value;
    const confirmPw = document.getElementById('confirm-pw').value;
    const errDiv = document.getElementById('change-pw-error');

    errDiv.textContent = '';
    errDiv.classList.add('hidden');

    if (newPw !== confirmPw) {
      errDiv.textContent = 'New passwords do not match';
      errDiv.classList.remove('hidden');
      return;
    }

    if (newPw.length < 8) {
      errDiv.textContent = 'New password must be at least 8 characters';
      errDiv.classList.remove('hidden');
      return;
    }

    try {
      const res = await fetch(`${this.apiUrl}/api/auth/change-password`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.token}`,
        },
        body: JSON.stringify({ current_password: curPw, new_password: newPw }),
      });

      if (res.ok) {
        this._toast('Password updated successfully', 'ok');
        this._initDashboard();
      } else {
        const data = await res.json();
        errDiv.textContent = data.error || 'Failed to change password';
        errDiv.classList.remove('hidden');
      }
    } catch {
      errDiv.textContent = 'Cannot connect to server';
      errDiv.classList.remove('hidden');
    }
  }

  // ── Token verification ────────────────────────────────────────────────────

  async _verifyToken() {
    try {
      const res = await fetch(`${this.apiUrl}/api/auth/verify`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (res.ok) {
        this._initDashboard();
      } else {
        this._logout();
      }
    } catch {
      this._showScreen('login');
    }
  }

  // ── Dashboard init ────────────────────────────────────────────────────────

  _initDashboard() {
    this._showScreen('dashboard');
    const userEl = document.getElementById('sidebar-user');
    if (userEl) userEl.textContent = this.currentUser || 'admin';
    lucide.createIcons();
    this._loadDashboard();
    this._startRefresh();
  }

  _bindDashboard() {
    document.getElementById('logout-btn')?.addEventListener('click', () => this._logout());

    document.getElementById('refresh-btn')?.addEventListener('click', () => {
      const btn = document.getElementById('refresh-btn');
      btn.classList.add('spinning');
      this._loadDashboard().finally(() => {
        setTimeout(() => btn.classList.remove('spinning'), 600);
      });
    });

    document.getElementById('search-input')?.addEventListener('input', e => {
      this.searchQuery = e.target.value.toLowerCase();
      this._renderMachines();
    });

    document.querySelectorAll('.pill').forEach(pill => {
      pill.addEventListener('click', () => {
        document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
        this.filterStatus = pill.dataset.status || '';
        this._renderMachines();
      });
    });
  }

  _logout() {
    this.token = null;
    this.currentUser = null;
    localStorage.removeItem('greenops_token');
    localStorage.removeItem('greenops_user');
    this._stopRefresh();
    this._showScreen('login');
  }

  // ── Data loading ──────────────────────────────────────────────────────────

  async _loadDashboard() {
    await Promise.all([this._loadStats(), this._loadMachines()]);
  }

  async _loadStats() {
    try {
      const res = await fetch(`${this.apiUrl}/api/dashboard/stats`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (!res.ok) return;
      const s = await res.json();

      this._setText('stat-total', s.total_machines);
      this._setText('stat-online', s.online_machines);
      this._setText('stat-idle', s.idle_machines);
      this._setText('stat-offline', s.offline_machines);
      this._setText('stat-energy', `${(s.total_energy_wasted_kwh || 0).toFixed(2)} kWh`);
      this._setText('stat-cost', `$${(s.estimated_cost_usd || 0).toFixed(2)}`);
    } catch { /* network error; keep previous values */ }
  }

  async _loadMachines() {
    try {
      const res = await fetch(`${this.apiUrl}/api/machines`, {
        headers: { 'Authorization': `Bearer ${this.token}` },
      });
      if (!res.ok) {
        if (res.status === 401) { this._logout(); return; }
        return;
      }
      const data = await res.json();
      this.machines = data.machines || [];
      this._renderMachines();
    } catch { /* keep previous render */ }
  }

  // ── Rendering ─────────────────────────────────────────────────────────────

  _renderMachines() {
    const grid = document.getElementById('machine-grid');
    if (!grid) return;

    let list = this.machines;

    if (this.filterStatus) {
      list = list.filter(m => m.status === this.filterStatus);
    }

    if (this.searchQuery) {
      list = list.filter(m =>
        (m.hostname || '').toLowerCase().includes(this.searchQuery) ||
        (m.mac_address || '').toLowerCase().includes(this.searchQuery) ||
        (m.os_type || '').toLowerCase().includes(this.searchQuery)
      );
    }

    if (list.length === 0) {
      grid.innerHTML = `<div class="no-machines">
        <p style="color:var(--text-muted);font-size:14px;">No machines match your filter.</p>
      </div>`;
      return;
    }

    grid.innerHTML = list.map((m, i) => this._cardHtml(m, i)).join('');
    lucide.createIcons();

    // Bind action buttons
    grid.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', () => {
        const { machineId, action } = btn.dataset;
        this._sendCommand(parseInt(machineId), action, btn);
      });
    });
  }

  _cardHtml(m, i) {
    const badge = this._badgeHtml(m.status);
    const lastSeen = this._relativeTime(m.last_seen);
    const uptime = this._fmtUptime(m.uptime_seconds || m.uptime_hours);
    const idle = this._fmtDuration(m.total_idle_seconds || 0);
    const energy = (m.energy_wasted_kwh || 0).toFixed(3);
    const canAct = m.status !== 'offline';
    const delay = `animation-delay:${i * 0.04}s`;

    return `
    <div class="machine-card" style="${delay}">
      <div class="card-head">
        <div>
          <div class="card-title">${this._esc(m.hostname)}</div>
          <div class="card-os">${this._esc(m.os_type)}</div>
          <div class="card-mac">${this._esc(m.mac_address)}</div>
        </div>
        ${badge}
      </div>

      <div class="card-metrics">
        <div class="metric">
          <span class="metric-val">${uptime}</span>
          <span class="metric-lbl">Uptime</span>
        </div>
        <div class="metric">
          <span class="metric-val">${idle}</span>
          <span class="metric-lbl">Idle</span>
        </div>
        <div class="metric">
          <span class="metric-val">${energy}</span>
          <span class="metric-lbl">kWh</span>
        </div>
      </div>

      <div class="card-last-seen">
        <i data-lucide="clock"></i>
        <span>Last seen ${lastSeen}</span>
      </div>

      <div class="card-actions">
        <button class="action-btn btn-sleep" data-action="sleep" data-machine-id="${m.id}" ${!canAct ? 'disabled' : ''}>
          <i data-lucide="moon"></i>
          Sleep
        </button>
        <button class="action-btn btn-shutdown" data-action="shutdown" data-machine-id="${m.id}" ${!canAct ? 'disabled' : ''}>
          <i data-lucide="power"></i>
          Shutdown
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
    return `<span class="status-badge ${cls}">
      <span class="status-dot ${dot}"></span>${label}
    </span>`;
  }

  // ── Commands ──────────────────────────────────────────────────────────────

  async _sendCommand(machineId, action, btn) {
    const label = action === 'sleep' ? 'Sleep' : 'Shutdown';
    if (!confirm(`Send ${label} command to this machine?\n\nThe machine must have the agent running and will execute the command on its next heartbeat poll.`)) return;

    btn.classList.add('loading');
    btn.disabled = true;
    const prevHtml = btn.innerHTML;
    btn.innerHTML = `<i data-lucide="loader"></i> Sending…`;
    lucide.createIcons();

    try {
      const res = await fetch(`${this.apiUrl}/api/machines/${machineId}/${action}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${this.token}` },
      });

      if (res.ok) {
        this._toast(`${label} command queued. Agent will execute on next poll.`, 'ok');
        // Reload in 3s to see any status changes
        setTimeout(() => this._loadDashboard(), 3000);
      } else {
        const data = await res.json().catch(() => ({}));
        this._toast(data.error || `Failed to queue ${label} command`, 'err');
      }
    } catch {
      this._toast('Cannot connect to server', 'err');
    } finally {
      btn.classList.remove('loading');
      btn.innerHTML = prevHtml;
      btn.disabled = false;
      lucide.createIcons();
    }
  }

  // ── Auto-refresh ──────────────────────────────────────────────────────────

  _startRefresh() {
    this._stopRefresh();
    this.refreshInterval = setInterval(() => this._loadDashboard(), this.REFRESH_MS);
  }

  _stopRefresh() {
    if (this.refreshInterval) {
      clearInterval(this.refreshInterval);
      this.refreshInterval = null;
    }
  }

  // ── Toast ─────────────────────────────────────────────────────────────────

  _toast(msg, type = 'ok') {
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  _esc(text) {
    const d = document.createElement('div');
    d.textContent = text || '';
    return d.innerHTML;
  }

  _relativeTime(ts) {
    if (!ts) return 'never';
    const diff = Math.floor((Date.now() - new Date(ts)) / 1000);
    if (diff < 10) return 'just now';
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  _fmtUptime(val) {
    // val can be uptime_seconds (int) or uptime_hours (float) from old API
    if (!val && val !== 0) return '—';
    let secs = val;
    // If it looks like hours (small float), convert
    if (secs < 1000 && String(val).includes('.')) secs = val * 3600;
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  _fmtDuration(seconds) {
    if (!seconds) return '0m';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }
}

// Boot
document.addEventListener('DOMContentLoaded', () => {
  window.app = new GreenOpsApp();
});
