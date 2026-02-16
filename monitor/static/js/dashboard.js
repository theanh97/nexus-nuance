// Auto Dev Loop - Dashboard JavaScript

// Socket.IO connection
const socket = io();

// State
let isPaused = false;
let state = {
    orion: { logs: [], thinking: '', status: {} },
    guardian: { logs: [], thinking: '', status: {} },
    project: {},
    pendingDecisions: [],
    decisionHistory: []
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initSocket();
    loadInitialState();
    setupPreferences();
});

// Socket.IO handlers
function initSocket() {
    socket.on('connect', () => {
        console.log('Connected to server');
        updateConnectionStatus(true);
        socket.emit('request_status');
    });

    socket.on('disconnect', () => {
        console.log('Disconnected from server');
        updateConnectionStatus(false);
    });

    socket.on('connected', (data) => {
        console.log('Server acknowledged:', data);
    });

    socket.on('status_update', (data) => {
        state.orion = data.orion || state.orion;
        state.guardian = data.guardian || state.guardian;
        state.project = data.project || state.project;
        updateUI();
    });

    socket.on('orion_log', (log) => {
        addOrionLog(log);
    });

    socket.on('guardian_log', (log) => {
        addGuardianLog(log);
    });

    socket.on('orion_thinking', (data) => {
        state.orion.thinking = data.thinking;
        updateOrionThinking();
    });

    socket.on('guardian_thinking', (data) => {
        state.guardian.thinking = data.thinking;
        updateGuardianThinking();
    });

    socket.on('new_decision', (decision) => {
        state.pendingDecisions.push(decision);
        updatePendingDecisions();
    });

    socket.on('decision_updated', (decision) => {
        // Remove from pending if exists
        state.pendingDecisions = state.pendingDecisions.filter(d => d.id !== decision.id);
        state.decisionHistory.push(decision);
        updatePendingDecisions();
    });

    socket.on('project_status', (status) => {
        state.project = status;
        updateProjectInfo();
    });
}

// Load initial state from API
async function loadInitialState() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();

        state.orion = data.orion || state.orion;
        state.guardian = data.guardian || state.guardian;
        state.project = data.project || state.project;
        state.pendingDecisions = data.pending_decisions || [];
        state.decisionHistory = data.decision_history || [];

        updateUI();
    } catch (error) {
        console.error('Failed to load initial state:', error);
    }
}

// Update all UI elements
function updateUI() {
    updateOrionLogs();
    updateGuardianLogs();
    updateOrionThinking();
    updateGuardianThinking();
    updateProjectInfo();
    updatePendingDecisions();
    updateLastUpdate();
}

// Update connection status
function updateConnectionStatus(connected) {
    const statusEl = document.getElementById('connection-status');
    statusEl.textContent = connected ? 'Connected' : 'Disconnected';
    statusEl.className = connected ? '' : 'disconnected';
}

// Add Orion log
function addOrionLog(log) {
    state.orion.logs.push(log);
    if (state.orion.logs.length > 100) {
        state.orion.logs = state.orion.logs.slice(-100);
    }
    updateOrionLogs();
    updateLastUpdate();
}

// Add Guardian log
function addGuardianLog(log) {
    state.guardian.logs.push(log);
    if (state.guardian.logs.length > 100) {
        state.guardian.logs = state.guardian.logs.slice(-100);
    }
    updateGuardianLogs();
    updateLastUpdate();
}

// Update Orion logs display
function updateOrionLogs() {
    const container = document.getElementById('orion-logs');
    if (state.orion.logs.length === 0) {
        container.innerHTML = '<p class="placeholder">Waiting for activity...</p>';
        return;
    }

    container.innerHTML = state.orion.logs.map(log => `
        <div class="log-entry ${log.level || 'info'}">
            <div class="log-time">${formatTime(log.timestamp)}</div>
            <div class="log-message">${escapeHtml(log.message)}</div>
        </div>
    `).join('');

    container.scrollTop = container.scrollHeight;
}

// Update Guardian logs display
function updateGuardianLogs() {
    const container = document.getElementById('guardian-logs');
    if (state.guardian.logs.length === 0) {
        container.innerHTML = '<p class="placeholder">Waiting for decisions...</p>';
        return;
    }

    container.innerHTML = state.guardian.logs.map(log => `
        <div class="log-entry ${log.level || 'info'}">
            <div class="log-time">${formatTime(log.timestamp)}</div>
            <div class="log-message">${escapeHtml(log.message)}</div>
        </div>
    `).join('');

    container.scrollTop = container.scrollHeight;
}

// Update Orion thinking display
function updateOrionThinking() {
    const container = document.getElementById('orion-thinking');
    if (state.orion.thinking) {
        container.innerHTML = `<p>${escapeHtml(state.orion.thinking)}</p>`;
    } else {
        container.innerHTML = '<p class="placeholder">Orion is thinking...</p>';
    }
}

// Update Guardian thinking display
function updateGuardianThinking() {
    const container = document.getElementById('guardian-thinking');
    if (state.guardian.thinking) {
        container.innerHTML = `<p>${escapeHtml(state.guardian.thinking)}</p>`;
    } else {
        container.innerHTML = '<p class="placeholder">Guardian is monitoring...</p>';
    }
}

// Update project info
function updateProjectInfo() {
    if (state.project.name) {
        document.getElementById('project-name').textContent = state.project.name;
    }
    if (state.project.current_iteration !== undefined) {
        document.getElementById('iteration-count').textContent = state.project.current_iteration;
    }
    if (state.project.score !== undefined) {
        document.getElementById('project-score').textContent = `${state.project.score.toFixed(1)}/10`;
    }
    if (state.project.status) {
        document.getElementById('project-status').textContent = state.project.status;
    }
}

// Update pending decisions
function updatePendingDecisions() {
    const container = document.getElementById('pending-decisions');

    if (state.pendingDecisions.length === 0) {
        container.innerHTML = '<p class="placeholder">No pending decisions</p>';
        return;
    }

    container.innerHTML = state.pendingDecisions.map(decision => `
        <div class="decision-item" data-id="${decision.id}">
            <div class="action">${escapeHtml(decision.action || 'Unknown action')}</div>
            <div class="details">
                Risk: ${decision.risk_level || 'medium'} |
                ${decision.details ? escapeHtml(JSON.stringify(decision.details).substring(0, 50)) : ''}
            </div>
            <div class="decision-actions">
                <button class="btn btn-approve" onclick="approveDecision('${decision.id}')">✓ Approve</button>
                <button class="btn btn-decline" onclick="declineDecision('${decision.id}')">✗ Decline</button>
            </div>
        </div>
    `).join('');
}

// Update last update time
function updateLastUpdate() {
    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
}

// Actions
async function togglePause() {
    isPaused = !isPaused;
    const btn = document.getElementById('pause-btn');
    btn.textContent = isPaused ? '▶ Resume' : '⏸ Pause';
    btn.className = isPaused ? 'btn btn-success' : 'btn btn-pause';

    await sendOrionCommand(isPaused ? 'resume' : 'pause');
}

async function shutdown() {
    if (confirm('Are you sure you want to shutdown the autonomous system?')) {
        await sendOrionCommand('shutdown');
    }
}

async function sendOrionCommand(command) {
    try {
        const response = await fetch('/api/orion/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command })
        });
        const data = await response.json();
        console.log('Command sent:', command, data);
    } catch (error) {
        console.error('Failed to send command:', error);
    }
}

async function approveDecision(decisionId) {
    try {
        const response = await fetch('/api/decision/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision_id: decisionId })
        });
        const data = await response.json();
        console.log('Decision approved:', data);
    } catch (error) {
        console.error('Failed to approve:', error);
    }
}

async function declineDecision(decisionId) {
    try {
        const response = await fetch('/api/decision/decline', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision_id: decisionId })
        });
        const data = await response.json();
        console.log('Decision declined:', data);
    } catch (error) {
        console.error('Failed to decline:', error);
    }
}

// Setup preferences
function setupPreferences() {
    const prefs = ['auto-approve-safe', 'auto-approve-medium', 'allow-system-cmds'];

    prefs.forEach(prefId => {
        const el = document.getElementById(prefId);
        if (el) {
            el.addEventListener('change', async (e) => {
                const prefs = {};
                prefs[prefId.replace(/-/g, '_')] = e.target.checked;

                try {
                    await fetch('/api/guardian/preferences', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(prefs)
                    });
                } catch (error) {
                    console.error('Failed to update preferences:', error);
                }
            });
        }
    });
}

// Utility functions
function formatTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return date.toLocaleTimeString();
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
