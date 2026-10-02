let ws = null;

document.addEventListener('DOMContentLoaded', () => {
    // Only initialize if we're on the dashboard
    if (!document.getElementById('protectedArea')) return;
    
    const connectBtn = document.getElementById('wsConnectBtn');
    const disconnectBtn = document.getElementById('wsDisconnectBtn');
    const sendBtn = document.getElementById('wsSendBtn');
    const input = document.getElementById('wsInput');
    const statusText = document.getElementById('wsStatusText');
    const messagesContainer = document.getElementById('wsMessages');
    
    function updateStatus(status) {
        statusText.textContent = status;
        statusText.className = 'badge ' + status.toLowerCase().replace('.', '');
        
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

    connectBtn.addEventListener('click', () => {
        const token = getToken(); // Access token retrieved via auth.js logic
        if (!token) {
            alert('Authentication required! Please log in.');
            return;
        }
        
        updateStatus('Connecting...');
        messagesContainer.innerHTML = ''; // Clear previous messages
        
        // Pass token securely via query string (WSS should be used in production)
        const wsUrl = `ws://127.0.0.1:8000/ws?token=${encodeURIComponent(token)}`;
        ws = new WebSocket(wsUrl);
        
        ws.onopen = () => {
            updateStatus('Connected');
            appendMessage('System', 'Connected to WebSocket server.');
        };
        
        ws.onmessage = (event) => {
            appendMessage('Server', event.data);
        };
        
        ws.onclose = (event) => {
            updateStatus('Disconnected');
            if (event.code !== 1000 && event.code !== 1005) {
                appendMessage('System', `Connection closed (${event.code}: ${event.reason || 'Unknown'})`);
            } else {
                appendMessage('System', 'Disconnected from server.');
            }
            ws = null;
        };
        
        ws.onerror = (error) => {
            console.error('WebSocket Error:', error);
            appendMessage('System', 'A WebSocket error occurred. Is the backend running?');
            updateStatus('Disconnected');
        };
    });
    
    disconnectBtn.addEventListener('click', () => {
        if (ws) {
            ws.close(1000, "Client initiated disconnect");
        }
    });
    
    function sendMessage() {
        if (ws && ws.readyState === WebSocket.OPEN) {
            const messageText = input.value.trim();
            if (messageText) {
                // Determine if user typed raw JSON or plain text
                let payload;
                try {
                    payload = JSON.parse(messageText);
                } catch (e) {
                    // Wrap plain text in expected JSON structure
                    payload = {
                        type: "message",
                        message: messageText
                    };
                }
                
                ws.send(JSON.stringify(payload));
                appendMessage('You', JSON.stringify(payload));
                input.value = '';
            }
        }
    }
    
    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });
});
