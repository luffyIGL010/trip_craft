let ws = null;

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('protectedArea')) return;
    
    const connectBtn = document.getElementById('wsConnectBtn');
    const disconnectBtn = document.getElementById('wsDisconnectBtn');
    const sendBtn = document.getElementById('wsSendBtn');
    const input = document.getElementById('wsInput');
    const statusText = document.getElementById('wsStatusText');
    const messagesContainer = document.getElementById('wsMessages');
    
    function updateStatus(status) {
        statusText.textContent = status;
        statusText.className = 'badge ' + status.toLowerCase().replace('...', '');
        
        const isConnected = status === 'Connected';
        connectBtn.disabled = isConnected || status === 'Connecting...';
        disconnectBtn.disabled = !isConnected;
        sendBtn.disabled = !isConnected;
        input.disabled = !isConnected;
    }
    
    function appendMessage(sender, text) {
        const msgDiv = document.createElement('div');
        msgDiv.style.marginBottom = '5px';
        msgDiv.innerHTML = `<strong>${sender}:</strong> ${text}`;
        messagesContainer.appendChild(msgDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function displayPlan(plan, transportResults) {
        let html = `<div class="plan-result">
            <h4>🗺️ Master Agent Plan</h4>
            <p><em>${plan.request_summary || ''}</em></p>
            <div class="plan-grid">
                <div><strong>📍 Destination:</strong> ${plan.destination || '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>🏠 Origin:</strong> ${plan.origin || '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>📅 Departure:</strong> ${plan.dates && plan.dates.start ? plan.dates.start : '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>📅 Duration:</strong> ${plan.duration_days ? plan.duration_days + ' days' : '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>👥 Travelers:</strong> ${plan.travelers || '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>💰 Budget:</strong> ${plan.budget && plan.budget.amount ? '₹' + plan.budget.amount.toLocaleString() : '<span class="text-muted">Not specified</span>'}</div>
            </div>`;
        
        if (plan.preferences && plan.preferences.length > 0) {
            html += `<div class="plan-section">
                <strong>🎯 Preferences:</strong>
                <ul>${plan.preferences.map(p => `<li>${p}</li>`).join('')}</ul>
            </div>`;
        }

        if (plan.tasks && plan.tasks.length > 0) {
            html += `<div class="plan-section">
                <strong>📋 Required Tasks:</strong>
                <ul>${plan.tasks.map(t => `<li>${t}</li>`).join('')}</ul>
            </div>`;
        }
        
        if (plan.missing_information && plan.missing_information.length > 0) {
            html += `<div class="plan-section missing">
                <strong>⚠️ Missing Information:</strong>
                <ul>${plan.missing_information.map(m => `<li>${m}</li>`).join('')}</ul>
            </div>`;
        }
        
        html += `</div>`;

        // Render Phase 5 Transport Results
        if (transportResults) {
            html += `<div class="plan-result" style="margin-top: 15px; border-left: 4px solid var(--primary-color);">
                <h4>✈️ Transport Options ${transportResults.is_mock ? '<span class="badge" style="background:#eab308; color:white;">Mock Data</span>' : ''}</h4>`;
            
            if (transportResults.status === "needs_information") {
                html += `<div class="plan-section missing">
                    <strong>Cannot search transport yet. Missing:</strong>
                    <ul>${transportResults.missing_information.map(m => `<li>${m}</li>`).join('')}</ul>
                </div>`;
            } else if (transportResults.status === "error") {
                 html += `<div class="plan-section missing">
                    <strong>Transport Search Error:</strong>
                    <p>${transportResults.message}</p>
                </div>`;
            } else if (transportResults.status === "success" && transportResults.options.length > 0) {
                html += `<p><strong>${transportResults.origin} → ${transportResults.destination}</strong></p>`;
                html += `<div style="display:flex; flex-direction:column; gap:10px; margin-top:10px;">`;
                
                transportResults.options.forEach((opt, idx) => {
                    html += `<div style="border:1px solid #e2e8f0; border-radius:4px; padding:10px; background:#f8fafc;">
                        <div style="display:flex; justify-content:space-between; margin-bottom:5px;">
                            <strong>Option ${idx + 1}: ${opt.provider}</strong>
                            <strong style="color:var(--primary-color)">₹${opt.price.toLocaleString()}</strong>
                        </div>
                        <div style="font-size:0.85rem; color:#475569;">
                            Departure: ${opt.departure} | Arrival: ${opt.arrival} <br>
                            Duration: ${opt.duration} | Stops: ${opt.stops}
                        </div>
                    </div>`;
                });
                
                html += `</div>`;
            } else {
                 html += `<p>No transport options found.</p>`;
            }
            html += `</div>`;
        }
        
        const msgDiv = document.createElement('div');
        msgDiv.style.marginBottom = '10px';
        msgDiv.innerHTML = html;
        messagesContainer.appendChild(msgDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    connectBtn.addEventListener('click', () => {
        const token = getToken();
        if (!token) {
            alert('Authentication required! Please log in.');
            return;
        }
        
        updateStatus('Connecting...');
        messagesContainer.innerHTML = '';
        
        const wsUrl = `ws://127.0.0.1:8000/ws?token=${encodeURIComponent(token)}`;
        ws = new WebSocket(wsUrl);
        
        ws.onopen = () => {
            updateStatus('Connected');
            appendMessage('System', '✅ Connected. Describe your travel plans below!');
        };
        
        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                
                if (data.error) {
                    appendMessage('⚠️ Error', data.error);
                } else if (data.type === 'agent_status') {
                    if (data.agent === 'master_agent' && data.status === 'processing') {
                        appendMessage('🤖 Master Agent', '<em>Analyzing your request...</em>');
                    } else if (data.agent === 'travel_agent' && data.status === 'searching_transport') {
                        appendMessage('✈️ Travel Agent', '<em>Searching for the best transport options...</em>');
                    }
                } else if (data.type === 'plan') {
                    displayPlan(data.data.plan, data.data.transport_results);
                } else {
                    appendMessage('Server', event.data);
                }
            } catch (e) {
                appendMessage('Server', event.data);
            }
        };
        
        ws.onclose = (event) => {
            updateStatus('Disconnected');
            if (event.code !== 1000 && event.code !== 1005) {
                appendMessage('System', `Connection closed (${event.code}: ${event.reason || 'Unknown'})`);
            } else {
                appendMessage('System', 'Disconnected.');
            }
            ws = null;
        };
        
        ws.onerror = () => {
            appendMessage('System', '❌ WebSocket error. Is the backend running?');
            updateStatus('Disconnected');
        };
    });
    
    disconnectBtn.addEventListener('click', () => {
        if (ws) ws.close(1000, "Client disconnect");
    });
    
    function sendMessage() {
        if (ws && ws.readyState === WebSocket.OPEN) {
            const messageText = input.value.trim();
            if (messageText) {
                const payload = { type: "message", message: messageText };
                ws.send(JSON.stringify(payload));
                appendMessage('You', messageText);
                input.value = '';
            }
        }
    }
    
    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });
});
