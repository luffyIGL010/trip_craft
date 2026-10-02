document.addEventListener('DOMContentLoaded', () => {
    // If we're on the main dashboard page, check authentication state first
    if (document.getElementById('protectedArea')) {
        checkAuthAndLoadProfile();
        
        // Setup logout button
        const logoutBtn = document.getElementById('logoutBtn');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', logoutUser);
        }
    }

    const checkBtn = document.getElementById('checkConnectionBtn');
    const statusArea = document.getElementById('statusArea');

    if (checkBtn) {
        checkBtn.addEventListener('click', async () => {
            // Reset status area
            statusArea.classList.remove('hidden', 'success', 'error');
            statusArea.textContent = 'Connecting...';
            
            try {
                // Send request to the FastAPI backend
                const response = await fetch('http://127.0.0.1:8000/health');
                
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                // Parse JSON response
                const data = await response.json();
                
                // Display success
                statusArea.classList.add('success');
                statusArea.textContent = JSON.stringify(data, null, 2);
            } catch (error) {
                // Display error gracefully
                statusArea.classList.add('error');
                statusArea.textContent = `Connection failed.\n\nError: ${error.message}\n\nPlease ensure the FastAPI server is running.`;
            }
        });
    }
});
