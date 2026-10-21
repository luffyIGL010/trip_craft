/**
 * Phase 6.5 – PayPal Sandbox Checkout
 *
 * Flow:
 *  1. Combined-cost card renders "Book This Combination" buttons via ws.js (injected below).
 *  2. User clicks a button → checkout panel appears with full disclaimer.
 *  3. On confirm → POST /api/payments/create-quote (backend calculates amount).
 *  4. PayPal SDK renders button → user approves in PayPal Sandbox popup.
 *  5. onApprove → POST /api/payments/capture-order (backend verifies with PayPal).
 *  6. Success/failure shown; never trust frontend success flag.
 *
 * Security notes:
 *  - Amount is NEVER sent from the frontend; quote_id is sent and backend recalculates.
 *  - PayPal client_id is fetched from backend (JWT protected) and is the PUBLIC key only.
 *  - PayPal SDK is loaded dynamically after client_id is confirmed.
 *  - Duplicate submissions are blocked while a pending operation is in flight.
 */

(function () {
    'use strict';

    // ─── State ────────────────────────────────────────────────────────────────
    let paypalClientId = null;
    let activeQuote = null;       // { quote_id, flight_option, hotel_option, total_amount_inr, ... }
    let activeOrderId = null;
    let checkoutInFlight = false; // Prevent duplicate submissions
    let sdkLoaded = false;
    let sdkLoading = false;

    // ─── DOM references (created lazily) ─────────────────────────────────────
    let checkoutPanel = null;

    // ─── Bootstrap ───────────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', async () => {
        // Only run on the protected dashboard page
        if (!document.getElementById('protectedArea')) return;
        await fetchPaypalClientId();
    });

    // ─── Fetch PayPal public client ID from backend ───────────────────────────
    async function fetchPaypalClientId() {
        const token = getToken();
        if (!token) return;
        try {
            const res = await fetch(`${API_URL}/api/payments/config/client-id`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (!res.ok) {
                console.warn('[Checkout] Could not fetch PayPal client ID:', res.status);
                return;
            }
            const data = await res.json();
            paypalClientId = data.client_id;
        } catch (e) {
            console.warn('[Checkout] PayPal config fetch failed:', e.message);
        }
    }

    // ─── Load PayPal JS SDK dynamically ──────────────────────────────────────
    function loadPaypalSdk(clientId) {
        return new Promise((resolve, reject) => {
            if (sdkLoaded) { resolve(); return; }
            if (sdkLoading) {
                // Wait for it
                const poll = setInterval(() => {
                    if (sdkLoaded) { clearInterval(poll); resolve(); }
                }, 200);
                return;
            }
            sdkLoading = true;
            const script = document.createElement('script');
            script.src = `https://www.paypal.com/sdk/js?client-id=${clientId}&currency=USD&intent=authorize`;
            script.onload = () => { sdkLoaded = true; sdkLoading = false; resolve(); };
            script.onerror = () => { sdkLoading = false; reject(new Error('PayPal SDK failed to load')); };
            document.head.appendChild(script);
        });
    }

    // ─── Public: called from ws.js combined-cost renderer ────────────────────
    /**
     * Renders a "Book This Combination (Sandbox Demo)" button inside a container.
     * @param {HTMLElement} container  The element to append the button to
     * @param {Object} combo  { flight_option, hotel_option, flight_price, hotel_price, nights, travelers, destination }
     */
    window.renderCheckoutButton = function (container, combo) {
        if (!paypalClientId) {
            const note = document.createElement('p');
            note.style.cssText = 'font-size:0.75rem;color:#9ca3af;margin-top:8px;';
            note.textContent = 'PayPal Sandbox not configured.';
            container.appendChild(note);
            return;
        }

        const btn = document.createElement('button');
        btn.textContent = '💳 Book This Combination (Sandbox Demo)';
        btn.style.cssText = `
            margin-top: 12px;
            padding: 10px 18px;
            background: #1d3557;
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 0.9rem;
            cursor: pointer;
            font-weight: 600;
            transition: background 0.2s;
        `;
        btn.onmouseover = () => btn.style.background = '#2563eb';
        btn.onmouseout = () => btn.style.background = '#1d3557';
        btn.addEventListener('click', () => openCheckoutPanel(combo));
        container.appendChild(btn);
    };

    // ─── Checkout panel ───────────────────────────────────────────────────────
    function openCheckoutPanel(combo) {
        // Remove any stale panel
        closeCheckoutPanel();

        const totalInr = combo.flight_price + combo.hotel_price;
        // Approximate USD for display only; backend recalculates authoritatively
        const approxUsd = (totalInr * 0.012).toFixed(2);

        checkoutPanel = document.createElement('div');
        checkoutPanel.id = 'paypalCheckoutPanel';
        checkoutPanel.style.cssText = `
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.6); z-index: 9999;
            display: flex; align-items: center; justify-content: center;
            padding: 16px;
        `;

        checkoutPanel.innerHTML = `
          <div style="background:#fff;border-radius:10px;max-width:520px;width:100%;padding:28px;
                      box-shadow:0 20px 60px rgba(0,0,0,0.3);font-family:sans-serif;max-height:90vh;overflow-y:auto;">

            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;">
              <h3 style="margin:0;color:#1e293b;font-size:1.15rem;">💳 Trip Checkout — Sandbox Demo</h3>
              <button id="ppCloseBtn" style="background:none;border:none;font-size:1.4rem;cursor:pointer;color:#64748b;">✕</button>
            </div>

            <div style="background:#fef3c7;border:1px solid #fde68a;border-radius:6px;padding:12px;margin-bottom:16px;font-size:0.8rem;color:#92400e;line-height:1.5;">
              ⚠️ <strong>Sandbox Demo Only.</strong> This is a PayPal Sandbox test payment. No real money is charged.
              This payment does <strong>not</strong> reserve or guarantee any flight or hotel booking. Prices shown are
              search estimates and are not locked in at the time of payment. Travel inventory is NOT being held.
            </div>

            <div style="margin-bottom:16px;">
              <div style="font-weight:600;color:#1e293b;margin-bottom:10px;">📋 Trip Summary</div>
              <table style="width:100%;font-size:0.875rem;border-collapse:collapse;">
                <tr style="border-bottom:1px solid #e2e8f0;">
                  <td style="padding:6px 0;color:#64748b;">✈️ Flight</td>
                  <td style="padding:6px 0;text-align:right;font-weight:500;">${escapeHtml(combo.flight_option)}</td>
                </tr>
                <tr style="border-bottom:1px solid #e2e8f0;">
                  <td style="padding:6px 0;color:#64748b;">🏨 Hotel</td>
                  <td style="padding:6px 0;text-align:right;font-weight:500;">${escapeHtml(combo.hotel_option)}</td>
                </tr>
                <tr style="border-bottom:1px solid #e2e8f0;">
                  <td style="padding:6px 0;color:#64748b;">📍 Destination</td>
                  <td style="padding:6px 0;text-align:right;">${escapeHtml(combo.destination || 'N/A')}</td>
                </tr>
                <tr style="border-bottom:1px solid #e2e8f0;">
                  <td style="padding:6px 0;color:#64748b;">🌙 Hotel nights</td>
                  <td style="padding:6px 0;text-align:right;">${combo.nights}</td>
                </tr>
                <tr style="border-bottom:1px solid #e2e8f0;">
                  <td style="padding:6px 0;color:#64748b;">👥 Travelers</td>
                  <td style="padding:6px 0;text-align:right;">${combo.travelers}</td>
                </tr>
                <tr style="border-bottom:1px solid #e2e8f0;">
                  <td style="padding:6px 0;color:#64748b;">✈️ Flight estimate (INR)</td>
                  <td style="padding:6px 0;text-align:right;">₹${Number(combo.flight_price).toLocaleString()}</td>
                </tr>
                <tr style="border-bottom:1px solid #e2e8f0;">
                  <td style="padding:6px 0;color:#64748b;">🏨 Hotel estimate (INR)</td>
                  <td style="padding:6px 0;text-align:right;">₹${Number(combo.hotel_price).toLocaleString()}</td>
                </tr>
                <tr>
                  <td style="padding:8px 0;font-weight:700;color:#1e293b;">💰 Total estimate (INR)</td>
                  <td style="padding:8px 0;text-align:right;font-weight:700;color:#7c3aed;font-size:1.05rem;">₹${Number(totalInr).toLocaleString()}</td>
                </tr>
                <tr>
                  <td style="padding:4px 0;font-size:0.75rem;color:#64748b;">Approx. USD (charged)</td>
                  <td style="padding:4px 0;text-align:right;font-size:0.75rem;color:#64748b;">~$${approxUsd} USD*</td>
                </tr>
              </table>
              <p style="font-size:0.72rem;color:#94a3b8;margin-top:6px;line-height:1.4;">
                * Sandbox payments are processed in USD at a fixed demo rate (1 INR ≈ $0.012 USD).
                Actual conversion rates apply for real bookings. Excludes transfers, meals, visa, and personal expenses.
              </p>
            </div>

            <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:10px;font-size:0.8rem;color:#166534;margin-bottom:16px;">
              ✅ <strong>What this covers (Sandbox Demo):</strong> Round-trip airfare estimate + hotel stay estimate.
              <br>❌ <strong>Does NOT include:</strong> Local transport, meals, attraction fees, visa, or any guaranteed booking.
            </div>

            <div id="ppButtonContainer" style="min-height:50px;"></div>
            <div id="ppStatusMsg" style="margin-top:12px;font-size:0.875rem;display:none;"></div>
          </div>
        `;

        document.body.appendChild(checkoutPanel);
        document.getElementById('ppCloseBtn').addEventListener('click', closeCheckoutPanel);

        // Render PayPal button
        renderPaypalButton(combo);
    }

    function closeCheckoutPanel() {
        const existing = document.getElementById('paypalCheckoutPanel');
        if (existing) existing.remove();
        checkoutPanel = null;
        checkoutInFlight = false;
    }

    function setPpStatus(msg, type) {
        // type: 'info' | 'success' | 'error' | 'warning'
        const el = document.getElementById('ppStatusMsg');
        if (!el) return;
        const colors = {
            info:    { bg: '#eff6ff', border: '#bfdbfe', text: '#1e40af' },
            success: { bg: '#f0fdf4', border: '#bbf7d0', text: '#166534' },
            error:   { bg: '#fef2f2', border: '#fecaca', text: '#991b1b' },
            warning: { bg: '#fefce8', border: '#fde68a', text: '#854d0e' }
        };
        const c = colors[type] || colors.info;
        el.style.cssText = `display:block;padding:10px 14px;border-radius:6px;background:${c.bg};border:1px solid ${c.border};color:${c.text};line-height:1.4;`;
        el.innerHTML = msg;
    }

    async function renderPaypalButton(combo) {
        if (!paypalClientId) {
            setPpStatus('❌ PayPal Sandbox is not configured on this server.', 'error');
            return;
        }

        setPpStatus('⏳ Loading PayPal Sandbox...', 'info');
        try {
            await loadPaypalSdk(paypalClientId);
        } catch (e) {
            setPpStatus(`❌ Could not load PayPal SDK: ${escapeHtml(e.message)}`, 'error');
            return;
        }
        setPpStatus('', 'info');
        document.getElementById('ppStatusMsg').style.display = 'none';

        const container = document.getElementById('ppButtonContainer');
        if (!container) return;

        paypal.Buttons({
            style: {
                layout: 'vertical',
                color: 'blue',
                shape: 'rect',
                label: 'pay'
            },

            // Step 1: Create a trusted quote on backend, then create PayPal order
            createOrder: async () => {
                if (checkoutInFlight) throw new Error('A payment is already in progress.');
                checkoutInFlight = true;
                setPpStatus('⏳ Creating secure quote on server...', 'info');

                const token = getToken();
                if (!token) {
                    checkoutInFlight = false;
                    throw new Error('Not authenticated.');
                }

                // Create server-side quote (amount calculated on backend, NOT from frontend)
                let quote;
                try {
                    const qRes = await fetch(`${API_URL}/api/payments/create-quote`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${token}`
                        },
                        body: JSON.stringify({
                            flight_option: combo.flight_option,
                            hotel_option:  combo.hotel_option,
                            flight_price:  combo.flight_price,
                            hotel_price:   combo.hotel_price,
                            nights:        combo.nights,
                            travelers:     combo.travelers,
                            destination:   combo.destination || ''
                        })
                    });
                    if (!qRes.ok) {
                        const e = await qRes.json().catch(() => ({ detail: 'Quote creation failed' }));
                        throw new Error(e.detail || 'Quote creation failed');
                    }
                    quote = await qRes.json();
                    activeQuote = quote;
                } catch (e) {
                    checkoutInFlight = false;
                    setPpStatus(`❌ ${escapeHtml(e.message)}`, 'error');
                    throw e;
                }

                // Create PayPal order on backend
                setPpStatus('⏳ Creating PayPal order...', 'info');
                try {
                    const oRes = await fetch(`${API_URL}/api/payments/create-order`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${token}`
                        },
                        body: JSON.stringify({ quote_id: quote.quote_id })
                    });
                    if (!oRes.ok) {
                        const e = await oRes.json().catch(() => ({ detail: 'Order creation failed' }));
                        throw new Error(e.detail || 'Order creation failed');
                    }
                    const order = await oRes.json();
                    activeOrderId = order.order_id;
                    setPpStatus('✅ PayPal order created. Please complete payment in the popup.', 'info');
                    return activeOrderId;
                } catch (e) {
                    checkoutInFlight = false;
                    setPpStatus(`❌ ${escapeHtml(e.message)}`, 'error');
                    throw e;
                }
            },

            // Step 2: User approved in PayPal → capture on backend
            onApprove: async (data) => {
                console.log("[PayPal] onApprove fired", data.orderID);
                setPpStatus('⏳ Verifying payment with server... Please wait.', 'info');
                const token = getToken();
                try {
                    const cRes = await fetch(`${API_URL}/api/payments/capture-order`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${token}`
                        },
                        body: JSON.stringify({ order_id: data.orderID })
                    });
                    const result = await cRes.json();
                    if (!cRes.ok || result.status !== 'success') {
                        const msg = result.detail || result.message || 'Capture failed';
                        setPpStatus(`❌ Payment capture failed: ${escapeHtml(msg)}`, 'error');
                        checkoutInFlight = false;
                        return;
                    }
                    // SUCCESS — only shown after backend confirms COMPLETED capture
                    setPpStatus(`
                        ✅ <strong>Sandbox Payment Captured Successfully!</strong><br>
                        <span style="font-size:0.8rem;">
                          PayPal Order ID: <code>${escapeHtml(data.orderID)}</code><br>
                          Capture ID: <code>${escapeHtml(result.capture_id || 'N/A')}</code><br>
                          Amount charged: <strong>$${escapeHtml(String(result.amount_charged_usd || ''))} USD</strong> (Sandbox)<br>
                          <em>No real money was charged. Travel inventory is not reserved.</em>
                        </span>
                    `, 'success');
                    // Disable PayPal button container after success
                    const ppContainer = document.getElementById('ppButtonContainer');
                    if (ppContainer) ppContainer.innerHTML = '<p style="color:#166534;font-weight:600;">✅ Payment completed.</p>';
                    checkoutInFlight = false;
                } catch (e) {
                    setPpStatus(`❌ Network error during capture: ${escapeHtml(e.message)}`, 'error');
                    checkoutInFlight = false;
                }
            },

            onCancel: () => {
                setPpStatus('⚠️ Payment cancelled. No charge was made.', 'warning');
                checkoutInFlight = false;
            },

            onError: (err) => {
                console.log("[PayPal] onError fired", err);
                console.error('[PayPal] SDK error:', err);
                setPpStatus('❌ PayPal encountered an error. Please try again.', 'error');
                checkoutInFlight = false;
            }
        }).render('#ppButtonContainer');
    }

    // ─── Helper: safe HTML escape (reuse from ws.js scope via closure) ────────
    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }
})();
