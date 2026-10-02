const API_URL = 'http://127.0.0.1:8000';

function setToken(token) {
    localStorage.setItem('travelagent_token', token);
}

function getToken() {
    return localStorage.getItem('travelagent_token');
}

function removeToken() {
    localStorage.removeItem('travelagent_token');
}

function showAuthStatus(message, isError = false) {
    const statusArea = document.getElementById('authStatusArea');
    if (!statusArea) return;
    
    statusArea.classList.remove('hidden', 'success', 'error');
    statusArea.classList.add(isError ? 'error' : 'success');
    statusArea.textContent = message;
}

async function registerUser() {
    const name = document.getElementById('name').value;
    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;

    try {
        const response = await fetch(`${API_URL}/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, password })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Registration failed');
        }

        showAuthStatus('Registration successful! Redirecting to login...');
        setTimeout(() => {
            window.location.href = 'login.html';
        }, 1500);

    } catch (error) {
        showAuthStatus(error.message, true);
    }
}

async function loginUser() {
    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;

    try {
        const response = await fetch(`${API_URL}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Login failed');
        }

        setToken(data.access_token);
        showAuthStatus('Login successful! Redirecting to dashboard...');
        setTimeout(() => {
            window.location.href = 'index.html';
        }, 1000);

    } catch (error) {
        showAuthStatus(error.message, true);
    }
}

async function checkAuthAndLoadProfile() {
    const token = getToken();
    if (!token) {
        window.location.href = 'login.html';
        return;
    }

    try {
        const response = await fetch(`${API_URL}/auth/me`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (!response.ok) {
            // Token invalid or expired
            removeToken();
            window.location.href = 'login.html';
            return;
        }

        const user = await response.json();
        
        // Show protected area, hide loading
        document.getElementById('loadingArea').classList.add('hidden');
        document.getElementById('protectedArea').classList.remove('hidden');
        
        // Update welcome message
        document.getElementById('welcomeMessage').textContent = `Welcome, ${user.name}`;

    } catch (error) {
        console.error('Auth check failed:', error);
        removeToken();
        window.location.href = 'login.html';
    }
}

async function logoutUser() {
    try {
        await fetch(`${API_URL}/auth/logout`, {
            method: 'POST'
        });
    } catch (e) {
        console.error("Logout request failed, but clearing local token anyway");
    } finally {
        removeToken();
        window.location.href = 'login.html';
    }
}
