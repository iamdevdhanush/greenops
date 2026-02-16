// GreenOps Dashboard Application

class GreenOpsApp {
    constructor() {
        this.apiUrl = window.location.origin;
        this.token = localStorage.getItem('greenops_token');
        this.currentUser = localStorage.getItem('greenops_user');
        this.machines = [];
        this.refreshInterval = null;
        
        this.init();
    }
    
    init() {
        // Check if already logged in
        if (this.token) {
            this.verifyToken();
        } else {
            this.showLogin();
        }
        
        // Setup event listeners
        this.setupEventListeners();
    }
    
    setupEventListeners() {
        // Login form
        const loginForm = document.getElementById('login-form');
        if (loginForm) {
            loginForm.addEventListener('submit', (e) => this.handleLogin(e));
        }
        
        // Logout button
        const logoutBtn = document.getElementById('logout-btn');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', () => this.handleLogout());
        }
        
        // Refresh button
        const refreshBtn = document.getElementById('refresh-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => this.loadDashboard());
        }
        
        // Status filter
        const statusFilter = document.getElementById('status-filter');
        if (statusFilter) {
            statusFilter.addEventListener('change', () => this.filterMachines());
        }
        
        // Search input
        const searchInput = document.getElementById('search-input');
        if (searchInput) {
            searchInput.addEventListener('input', () => this.filterMachines());
        }
    }
    
    showLogin() {
        document.getElementById('login-screen').classList.add('active');
        document.getElementById('dashboard-screen').classList.remove('active');
    }
    
    showDashboard() {
        document.getElementById('login-screen').classList.remove('active');
        document.getElementById('dashboard-screen').classList.add('active');
        
        // Display current user
        document.getElementById('current-user').textContent = this.currentUser;
        
        // Load dashboard data
        this.loadDashboard();
        
        // Setup auto-refresh
        this.startAutoRefresh();
    }
    
    async handleLogin(e) {
        e.preventDefault();
        
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;
        const errorDiv = document.getElementById('login-error');
        
        errorDiv.textContent = '';
        
        try {
            const response = await fetch(`${this.apiUrl}/api/auth/login`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ username, password })
            });
            
            if (response.ok) {
                const data = await response.json();
                
                // Store token and user info
                this.token = data.token;
                this.currentUser = data.username;
                localStorage.setItem('greenops_token', data.token);
                localStorage.setItem('greenops_user', data.username);
                
                // Show dashboard
                this.showDashboard();
            } else {
                const error = await response.json();
                errorDiv.textContent = error.error || 'Login failed';
            }
        } catch (err) {
            errorDiv.textContent = 'Cannot connect to server';
            console.error('Login error:', err);
        }
    }
    
    handleLogout() {
        this.token = null;
        this.currentUser = null;
        localStorage.removeItem('greenops_token');
        localStorage.removeItem('greenops_user');
        
        this.stopAutoRefresh();
        this.showLogin();
    }
    
    async verifyToken() {
        try {
            const response = await fetch(`${this.apiUrl}/api/auth/verify`, {
                headers: {
                    'Authorization': `Bearer ${this.token}`
                }
            });
            
            if (response.ok) {
                this.showDashboard();
            } else {
                this.handleLogout();
            }
        } catch (err) {
            console.error('Token verification failed:', err);
            this.showLogin();
        }
    }
    
    async loadDashboard() {
        await Promise.all([
            this.loadStats(),
            this.loadMachines()
        ]);
    }
    
    async loadStats() {
        try {
            const response = await fetch(`${this.apiUrl}/api/dashboard/stats`, {
                headers: {
                    'Authorization': `Bearer ${this.token}`
                }
            });
            
            if (response.ok) {
                const stats = await response.json();
                
                document.getElementById('stat-total').textContent = stats.total_machines;
                document.getElementById('stat-online').textContent = stats.online_machines;
                document.getElementById('stat-idle').textContent = stats.idle_machines;
                document.getElementById('stat-offline').textContent = stats.offline_machines;
                document.getElementById('stat-energy').textContent = 
                    `${stats.total_energy_wasted_kwh.toFixed(2)} kWh`;
                document.getElementById('stat-cost').textContent = 
                    `$${stats.estimated_cost_usd.toFixed(2)}`;
            }
        } catch (err) {
            console.error('Failed to load stats:', err);
        }
    }
    
    async loadMachines() {
        try {
            const response = await fetch(`${this.apiUrl}/api/machines`, {
                headers: {
                    'Authorization': `Bearer ${this.token}`
                }
            });
            
            if (response.ok) {
                const data = await response.json();
                this.machines = data.machines;
                this.renderMachines(this.machines);
            }
        } catch (err) {
            console.error('Failed to load machines:', err);
        }
    }
    
    renderMachines(machines) {
        const tbody = document.getElementById('machine-table-body');
        
        if (machines.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="loading">No machines found</td></tr>';
            return;
        }
        
        tbody.innerHTML = machines.map(machine => `
            <tr>
                <td><strong>${this.escapeHtml(machine.hostname)}</strong></td>
                <td class="text-mono">${this.escapeHtml(machine.mac_address)}</td>
                <td>${this.escapeHtml(machine.os_type)}</td>
                <td>${this.renderStatus(machine.status)}</td>
                <td>${this.formatTimestamp(machine.last_seen)}</td>
                <td>${machine.uptime_hours.toFixed(1)}</td>
                <td>${this.formatIdleTime(machine.total_idle_seconds)}</td>
                <td>${machine.energy_wasted_kwh.toFixed(3)}</td>
            </tr>
        `).join('');
    }
    
    renderStatus(status) {
        const classMap = {
            'online': 'status-online',
            'idle': 'status-idle',
            'offline': 'status-offline'
        };
        
        const className = classMap[status] || '';
        return `<span class="status-badge ${className}">${status.toUpperCase()}</span>`;
    }
    
    formatTimestamp(timestamp) {
        if (!timestamp) return 'Never';
        
        const date = new Date(timestamp);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`;
        return `${Math.floor(diffMins / 1440)}d ago`;
    }
    
    formatIdleTime(seconds) {
        if (!seconds) return '0m';
        
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        
        if (hours > 0) {
            return `${hours}h ${minutes}m`;
        }
        return `${minutes}m`;
    }
    
    filterMachines() {
        const statusFilter = document.getElementById('status-filter').value;
        const searchQuery = document.getElementById('search-input').value.toLowerCase();
        
        let filtered = this.machines;
        
        // Apply status filter
        if (statusFilter) {
            filtered = filtered.filter(m => m.status === statusFilter);
        }
        
        // Apply search filter
        if (searchQuery) {
            filtered = filtered.filter(m => 
                m.hostname.toLowerCase().includes(searchQuery) ||
                m.mac_address.toLowerCase().includes(searchQuery) ||
                m.os_type.toLowerCase().includes(searchQuery)
            );
        }
        
        this.renderMachines(filtered);
    }
    
    startAutoRefresh() {
        this.refreshInterval = setInterval(() => {
            this.loadDashboard();
        }, 30000); // Refresh every 30 seconds
    }
    
    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize app when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.app = new GreenOpsApp();
    });
} else {
    window.app = new GreenOpsApp();
}
