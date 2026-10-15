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
            const isRoundTrip = transportResults.trip_type === "round_trip" || !!transportResults.return_date;
            const priceLabelText = transportResults.price_label || (isRoundTrip ? "Total for round trip" : "Total price");

            html += `<div class="plan-result" style="margin-top: 15px; border-left: 4px solid var(--primary-color);">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h4 style="margin:0;">✈️ Transport Options</h4>
                    <div>
                        ${isRoundTrip ? '<span class="badge" style="background:#0284c7; color:white; margin-right:4px;">Round Trip</span>' : '<span class="badge" style="background:#64748b; color:white; margin-right:4px;">One Way</span>'}
                        ${transportResults.is_mock ? '<span class="badge" style="background:#eab308; color:white;">Mock Data</span>' : ''}
                    </div>
                </div>`;
            
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
                // Trip Header Summary
                html += `<div style="margin-top:8px; padding-bottom:6px; border-bottom:1px solid #e2e8f0;">
                    <div style="font-size:0.95rem;"><strong>${transportResults.origin} ${isRoundTrip ? '⇄' : '→'} ${transportResults.destination}</strong></div>
                    <div style="font-size:0.85rem; color:#64748b;">
                        📅 ${transportResults.outbound_date ? 'Outbound: ' + transportResults.outbound_date : ''}
                        ${transportResults.return_date ? ' | Return: ' + transportResults.return_date : ''}
                        ${transportResults.travelers ? ' | 👥 ' + transportResults.travelers + ' traveler(s)' : ''}
                    </div>
                </div>`;

                // Accurate Total Price Callout (Requirement 4)
                html += `<div style="font-size:0.85rem; color:#0369a1; background:#f0f9ff; padding:6px 10px; border-radius:4px; margin:8px 0; border:1px solid #bae6fd;">
                    <strong>💰 Price Notice:</strong> All fares shown are <strong>${priceLabelText}</strong> (all requested passengers combined).
                </div>`;

                // Outbound Flights Section (Requirement 3)
                html += `<div style="margin-top:10px;">
                    <div style="font-weight:600; font-size:0.9rem; color:#1e293b; margin-bottom:6px; display:flex; align-items:center; gap:6px;">
                        <span>🛫 Outbound Flights</span>
                        <span style="font-size:0.8rem; font-weight:normal; color:#64748b;">(${transportResults.origin} → ${transportResults.destination} • ${transportResults.outbound_date || ''})</span>
                    </div>
                    <div style="display:flex; flex-direction:column; gap:8px;">`;
                
                transportResults.options.forEach((opt, idx) => {
                    const providerName = opt.provider || "Provider Not Specified";
                    const flightNumBadge = opt.outbound_leg && opt.outbound_leg.flight_number ? `<span style="font-size:0.75rem; background:#e2e8f0; color:#334155; padding:2px 6px; border-radius:3px; margin-left:6px;">${opt.outbound_leg.flight_number}</span>` : '';
                    const airplaneInfo = opt.outbound_leg && opt.outbound_leg.airplane ? ` • ${opt.outbound_leg.airplane}` : '';
                    
                    html += `<div style="border:1px solid #cbd5e1; border-radius:6px; padding:10px; background:#f8fafc;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                            <div style="display:flex; align-items:center;">
                                <strong style="color:#0f172a;">Option ${idx + 1}: ${providerName}</strong>
                                ${flightNumBadge}
                            </div>
                            <div style="text-align:right;">
                                <strong style="color:var(--primary-color); font-size:1.05rem;">₹${opt.price.toLocaleString()}</strong>
                                <span style="font-size:0.75rem; color:#64748b; display:block;">total</span>
                            </div>
                        </div>
                        <div style="font-size:0.85rem; color:#475569; line-height:1.4;">
                            <div><strong>Departure:</strong> ${opt.departure} | <strong>Arrival:</strong> ${opt.arrival}</div>
                            <div><strong>Duration:</strong> ${opt.duration} | <strong>Stops:</strong> ${opt.stops === 0 ? 'Non-stop' : opt.stops + ' stop(s)'}${airplaneInfo}</div>
                        </div>
                    </div>`;
                });
                html += `</div></div>`;

                // Return Flights Section if round-trip and provided (Requirement 3)
                if (transportResults.return_options && transportResults.return_options.length > 0) {
                    html += `<div style="margin-top:14px;">
                        <div style="font-weight:600; font-size:0.9rem; color:#1e293b; margin-bottom:6px; display:flex; align-items:center; gap:6px;">
                            <span>🛬 Return Flights</span>
                            <span style="font-size:0.8rem; font-weight:normal; color:#64748b;">(${transportResults.destination} → ${transportResults.origin} • ${transportResults.return_date || ''})</span>
                        </div>
                        <div style="display:flex; flex-direction:column; gap:8px;">`;

                    transportResults.return_options.forEach((opt, idx) => {
                        const providerName = opt.provider || "Provider Not Specified";
                        const flightNumBadge = opt.outbound_leg && opt.outbound_leg.flight_number ? `<span style="font-size:0.75rem; background:#e2e8f0; color:#334155; padding:2px 6px; border-radius:3px; margin-left:6px;">${opt.outbound_leg.flight_number}</span>` : '';
                        const airplaneInfo = opt.outbound_leg && opt.outbound_leg.airplane ? ` • ${opt.outbound_leg.airplane}` : '';

                        html += `<div style="border:1px solid #cbd5e1; border-radius:6px; padding:10px; background:#f0fdf4;">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                                <div style="display:flex; align-items:center;">
                                    <strong style="color:#0f172a;">Return Option ${idx + 1}: ${providerName}</strong>
                                    ${flightNumBadge}
                                </div>
                                <span style="font-size:0.8rem; color:#166534; font-weight:500; background:#dcfce7; padding:2px 6px; border-radius:4px;">Return Flight</span>
                            </div>
                            <div style="font-size:0.85rem; color:#475569; line-height:1.4;">
                                <div><strong>Departure:</strong> ${opt.departure} | <strong>Arrival:</strong> ${opt.arrival}</div>
                                <div><strong>Duration:</strong> ${opt.duration} | <strong>Stops:</strong> ${opt.stops === 0 ? 'Non-stop' : opt.stops + ' stop(s)'}${airplaneInfo}</div>
                            </div>
                        </div>`;
                    });
                    html += `</div></div>`;
                }
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
