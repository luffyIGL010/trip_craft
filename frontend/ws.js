let ws = null;

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('protectedArea')) return;
    
    const connectBtn = document.getElementById('wsConnectBtn');
    const disconnectBtn = document.getElementById('wsDisconnectBtn');
    const sendBtn = document.getElementById('wsSendBtn');
    const input = document.getElementById('wsInput');
    const statusText = document.getElementById('wsStatusText');
    const messagesContainer = document.getElementById('wsMessages');
    
    const statusDot = document.getElementById('wsStatusDot');
    
    function updateStatus(status) {
        statusText.textContent = status;
        const normalized = status.toLowerCase().replace('...', '');
        statusText.className = 'badge ' + normalized;
        if (statusDot) {
            statusDot.className = 'status-dot ' + normalized;
        }
        
        const isConnected = status === 'Connected';
        connectBtn.disabled = isConnected || status === 'Connecting...';
        disconnectBtn.disabled = !isConnected;
        sendBtn.disabled = !isConnected;
        input.disabled = !isConnected;
    }
    
    function appendMessage(sender, text) {
        const msgDiv = document.createElement('div');
        let bubbleClass = 'agent';
        if (sender === 'You') {
            bubbleClass = 'user';
        } else if (sender === 'System') {
            bubbleClass = 'system';
        }
        msgDiv.className = `chat-bubble ${bubbleClass}`;
        
        if (sender === 'You') {
            msgDiv.innerHTML = text;
        } else if (sender === 'System') {
            msgDiv.innerHTML = text;
        } else {
            msgDiv.innerHTML = `<strong style="display:block; margin-bottom:2px; font-size:0.85rem; color:#475569;">${sender}</strong>${text}`;
        }
        messagesContainer.appendChild(msgDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    function displayPlan(plan, transportResults, hotelResults, combinedTripCost) {
        let html = `<div class="plan-result">
            <h4>🗺️ Master Agent Plan</h4>
            <p><em>${plan.request_summary || ''}</em></p>
            <div class="plan-grid">
                <div><strong>📍 Destination:</strong> ${plan.destination || '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>🏠 Origin:</strong> ${plan.origin || '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>📅 Departure:</strong> ${plan.dates && plan.dates.start ? plan.dates.start : '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>📅 Duration:</strong> ${plan.duration_days ? plan.duration_days + ' calendar days' : '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>👥 Travelers:</strong> ${plan.travelers || '<span class="text-muted">Not specified</span>'}</div>
                <div><strong>💰 Budget:</strong> ${plan.budget && plan.budget.amount ? '₹' + plan.budget.amount.toLocaleString() : '<span class="text-muted">Not specified</span>'}</div>
            </div>`;
        
        if (plan.preferences && plan.preferences.length > 0) {
            html += `<div class="plan-section">
                <strong>🎯 Preferences:</strong>
                <ul>${plan.preferences.map(p => `<li>${escapeHtml(p)}</li>`).join('')}</ul>
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
            const priceLabelText = transportResults.price_label || "Provider price scope not specified";
            const returnFareNotice = isRoundTrip
                ? "A round-trip search was requested; a return fare is not verified by the displayed price unless that option explicitly shows both legs."
                : "A one-way search was requested; no return fare is included.";

            html += `<div class="plan-result" style="margin-top: 15px; border-left: 4px solid var(--primary-color);">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h4 style="margin:0;">✈️ Transport Options</h4>
                    <div>
                        ${isRoundTrip ? '<span class="badge" style="background:#0284c7; color:white; margin-right:4px;">Round-trip search</span>' : '<span class="badge" style="background:#64748b; color:white; margin-right:4px;">One-way search</span>'}
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
                    <strong>💰 Price Notice:</strong> ${escapeHtml(priceLabelText)}. ${returnFareNotice}
                </div>`;

                // Outbound Flights Section (Requirement 3)
                html += `<div style="margin-top:10px;">
                    <div style="font-weight:600; font-size:0.9rem; color:#1e293b; margin-bottom:6px; display:flex; align-items:center; gap:6px;">
                        <span>🛫 Flight Itinerary Options</span>
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
                                <strong style="color:var(--primary-color); font-size:1.05rem;">${opt.price === null || opt.price === undefined ? 'Price unavailable' : `₹${Number(opt.price).toLocaleString()}`}</strong>
                                ${opt.price !== null && opt.price !== undefined ? `<span style="font-size:0.75rem; color:#64748b; display:block;">${escapeHtml(opt.price_label || 'Provider price scope unspecified')}</span>` : ''}
                            </div>
                        </div>
                        <div style="font-size:0.85rem; color:#475569; line-height:1.4;">
                            <div><strong>Departure:</strong> ${opt.departure} | <strong>Arrival:</strong> ${opt.arrival}</div>
                            <div><strong>Duration:</strong> ${opt.duration} | <strong>Stops:</strong> ${opt.stops === 0 ? 'Non-stop' : opt.stops + ' stop(s)'}${airplaneInfo}</div>
                            ${opt.return_leg ? `<div style="margin-top:4px;"><strong>Return:</strong> ${escapeHtml(opt.return_leg.departure_airport || '')} → ${escapeHtml(opt.return_leg.arrival_airport || '')} | ${escapeHtml(opt.return_leg.departure_time || '')} → ${escapeHtml(opt.return_leg.arrival_time || '')} | ${escapeHtml(opt.return_leg.duration || '')}</div>` : ''}
                        </div>
                    </div>`;
                });
                html += `</div></div>`;

                // Return Flights Section if round-trip and provided (Requirement 3)
                if (transportResults.return_options && transportResults.return_options.length > 0) {
                    html += `<div style="margin-top:14px;">
                        <div style="font-weight:600; font-size:0.9rem; color:#1e293b; margin-bottom:6px; display:flex; align-items:center; gap:6px;">
                            <span>🛬 Return-leg options for the selected outbound (return fare not included)</span>
                            <span style="font-size:0.8rem; font-weight:normal; color:#64748b;">(${transportResults.destination} → ${transportResults.origin} • ${transportResults.return_date || ''})</span>
                        </div>
                        <div style="display:flex; flex-direction:column; gap:8px;">`;

                    transportResults.return_options.forEach((opt, idx) => {
                        const providerName = opt.provider || "Provider Not Specified";
                        const flightNumBadge = opt.outbound_leg && opt.outbound_leg.flight_number ? `<span style="font-size:0.75rem; background:#e2e8f0; color:#334155; padding:2px 6px; border-radius:3px; margin-left:6px;">${opt.outbound_leg.flight_number}</span>` : '';
                        const airplaneInfo = opt.outbound_leg && opt.outbound_leg.airplane ? ` • ${opt.outbound_leg.airplane}` : '';

                        const pairedOutbound = opt.paired_outbound_option_id
                            ? ` • Selected with outbound itinerary ${escapeHtml(opt.paired_outbound_option_id)}`
                            : '';
                        html += `<div style="border:1px solid #cbd5e1; border-radius:6px; padding:10px; background:#f0fdf4;">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                                <div style="display:flex; align-items:center;">
                                    <strong style="color:#0f172a;">Return Option ${idx + 1}: ${providerName}</strong>
                                    ${flightNumBadge}
                                </div>
                                <span style="font-size:0.8rem; color:#166534; font-weight:500; background:#dcfce7; padding:2px 6px; border-radius:4px;">Return Flight</span>
                            </div>
                            <div style="font-size:0.85rem; color:#475569; line-height:1.4;">
                                <div><strong>Departure:</strong> ${opt.departure} | <strong>Arrival:</strong> ${opt.arrival}${pairedOutbound}</div>
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

        // Render Phase 6 Hotel Results
        if (hotelResults) {
            html += `<div class="plan-result" style="margin-top: 15px; border-left: 4px solid #10b981;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h4 style="margin:0; color:#059669;">🏨 Accommodation Options</h4>
                    <div>
                        ${hotelResults.is_sandbox ? '<span class="badge" style="background:#eab308; color:white; font-size:0.75rem;">Sandbox Test Data</span>' : ''}
                    </div>
                </div>`;

            if (hotelResults.notice) {
                html += `<div style="font-size:0.8rem; color:#854d0e; background:#fefce8; padding:6px 10px; border-radius:4px; margin:8px 0; border:1px solid #fef08a;">
                    ℹ️ <strong>Note:</strong> ${escapeHtml(hotelResults.notice)}
                </div>`;
            }

            if (hotelResults.status === "needs_information") {
                html += `<div class="plan-section missing">
                    <strong>Cannot search hotels yet. Missing:</strong>
                    <ul>${(hotelResults.missing_information || []).map(m => `<li>${escapeHtml(m)}</li>`).join('')}</ul>
                    <p style="margin-top:6px; font-size:0.85rem;">${escapeHtml(hotelResults.message)}</p>
                </div>`;
            } else if (hotelResults.status === "error") {
                html += `<div class="plan-section missing">
                    <strong>Hotel Search Error:</strong>
                    <p>${escapeHtml(hotelResults.message)}</p>
                </div>`;
            } else if (hotelResults.status === "success" && hotelResults.options && hotelResults.options.length > 0) {
                html += `<div style="margin-top:8px; padding-bottom:6px; border-bottom:1px solid #e2e8f0;">
                    <div style="font-size:0.95rem;"><strong>📍 ${escapeHtml(hotelResults.destination || 'Destination')}</strong></div>
                    <div style="font-size:0.85rem; color:#64748b;">
                        📅 Check-in: ${escapeHtml(hotelResults.check_in || 'N/A')} | Check-out: ${escapeHtml(hotelResults.check_out || 'N/A')} ${hotelResults.nights ? `| 🌙 ${hotelResults.nights} nights` : ''} | 👥 Adults: ${hotelResults.adults || 2}
                    </div>
                </div>`;

                html += `<div style="display:flex; flex-direction:column; gap:10px; margin-top:10px;">`;

                hotelResults.options.forEach((h, idx) => {
                    const hotelName = escapeHtml(h.name || 'Unnamed Property');
                    const platformName = escapeHtml(h.platform ? h.platform.toUpperCase() : 'Hotel');
                    const propType = escapeHtml(h.property_type || 'hotel');
                    const address = escapeHtml(h.address || h.city || '');
                    
                    let ratingText = '';
                    if (h.guest_rating) {
                        const scale = h.rating_scale || 5;
                        const reviews = h.review_count ? ` (${h.review_count} reviews)` : '';
                        ratingText = `⭐ ${h.guest_rating}/${scale}${reviews}`;
                    } else if (h.star_rating) {
                        ratingText = `⭐ ${h.star_rating} star hotel`;
                    }

                    let priceHtml = '';
                    const cur = escapeHtml(h.currency || 'EUR');
                    if (h.nightly_price) {
                        let totalPart = '';
                        if (h.total_price) {
                            totalPart = ` • <strong>${cur} ${h.total_price.toLocaleString()}</strong> total`;
                        } else if (h.price_label) {
                            totalPart = ` • <span style="font-size:0.75rem; color:#854d0e; background:#fef9c3; padding:2px 6px; border-radius:3px;">${escapeHtml(h.price_label)}</span>`;
                        }
                        const nightsStr = h.nights ? ` (${h.nights} nights)` : '';
                        priceHtml = `<span style="color:#059669; font-weight:700; font-size:1.05rem;">${cur} ${h.nightly_price.toLocaleString()}</span> <span style="font-size:0.75rem; color:#64748b;">/night${totalPart}${nightsStr}</span>`;
                    } else if (h.total_price) {
                        priceHtml = `<span style="color:#059669; font-weight:700; font-size:1.05rem;">${cur} ${h.total_price.toLocaleString()}</span> <span style="font-size:0.75rem; color:#64748b;">total</span>`;
                    } else if (h.price_label) {
                        priceHtml = `<span style="font-size:0.8rem; color:#854d0e; background:#fef9c3; padding:2px 6px; border-radius:3px;">${escapeHtml(h.price_label)}</span>`;
                    } else {
                        priceHtml = `<span style="font-size:0.85rem; color:#64748b;">Price on request</span>`;
                    }

                    const amenitiesHtml = (h.amenities && h.amenities.length > 0)
                        ? `<div style="display:flex; flex-wrap:wrap; gap:4px; margin-top:6px;">
                            ${h.amenities.slice(0, 6).map(a => `<span style="background:#e0f2fe; color:#0369a1; font-size:0.75rem; padding:2px 6px; border-radius:3px;">${escapeHtml(a.replace(/_/g, ' '))}</span>`).join('')}
                           </div>`
                        : '';

                    let urlHtml = '';
                    if (h.url && (h.url.startsWith('https://') || h.url.startsWith('http://'))) {
                        const safeUrl = encodeURI(h.url);
                        urlHtml = `<div style="margin-top:6px;">
                            <a href="${safeUrl}" target="_blank" rel="noopener noreferrer" style="font-size:0.8rem; color:var(--primary-color); text-decoration:none;">🔗 View on ${platformName} →</a>
                        </div>`;
                    }

                    html += `<div style="border:1px solid #cbd5e1; border-radius:6px; padding:10px; background:#f8fafc;">
                        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px;">
                            <div>
                                <strong style="color:#0f172a; font-size:0.95rem;">Option ${idx + 1}: ${hotelName}</strong>
                                <span style="font-size:0.75rem; background:#f1f5f9; color:#475569; padding:2px 6px; border-radius:3px; margin-left:6px; text-transform:capitalize;">${platformName} • ${propType}</span>
                            </div>
                            <div style="text-align:right;">
                                ${priceHtml}
                            </div>
                        </div>
                        <div style="font-size:0.85rem; color:#475569; line-height:1.4;">
                            ${address ? `<div>📍 ${address}</div>` : ''}
                            ${ratingText ? `<div style="color:#b45309; font-weight:500;">${ratingText}</div>` : ''}
                            ${amenitiesHtml}
                            ${urlHtml}
                        </div>
                    </div>`;
                });

                html += `</div>`;
            } else {
                const hotelMsg = hotelResults.message || 'No accommodation options found for this destination.';
                html += `<div style="margin-top:10px; padding:10px 14px; background:#f8fafc; border-left:4px solid #f59e0b; border-radius:4px; font-size:0.9rem; color:#334155;">
                    ℹ️ <strong>Accommodation Status:</strong> ${escapeHtml(hotelMsg)}
                </div>`;
            }
            html += `</div>`;
        }

        // Render Combined Estimated Trip Cost Section
        if (combinedTripCost) {
            html += `<div class="plan-result" style="margin-top: 15px; border-left: 4px solid #8b5cf6;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h4 style="margin:0; color:#7c3aed;">💰 Combined Estimated Trip Cost</h4>
                    <span class="badge" style="background:#7c3aed; color:white; font-size:0.75rem;">Flights + Accommodation</span>
                </div>`;

            if (combinedTripCost.status === "available" && combinedTripCost.estimates && combinedTripCost.estimates.length > 0) {
                html += `<div style="font-size:0.85rem; color:#475569; margin: 8px 0 12px 0;">
                    Showing combined cost estimates for top flight & hotel combinations:
                </div>
                <div style="display:flex; flex-direction:column; gap:10px;">`;

                // Collect combos for post-render PayPal button injection
                const checkoutCombos = [];

                combinedTripCost.estimates.forEach((est, idx) => {
                    if (est.status === "available") {
                        const budgetComparison = est.saved_budget_comparison;
                        let budgetStatusHtml = '';
                        if (budgetComparison && budgetComparison.status) {
                            const budgetLabels = {
                                within: 'Within selected saved budget',
                                above: 'Exceeds selected saved budget',
                                below: 'Below selected saved budget',
                                not_compared_basis_unspecified: 'Budget basis needs selection before comparison',
                                not_compared_currency_mismatch: 'Budget comparison unavailable: currencies do not match'
                            };
                            const basisLabel = budgetComparison.basis === 'per_person'
                                ? `Budget per traveler (${combinedTripCost.travelers || 1} travelers)`
                                : 'Total budget for the entire trip';
                            const minBudget = budgetComparison.min_amount;
                            const maxBudget = budgetComparison.max_amount;
                            const budgetRange = minBudget !== undefined && maxBudget !== undefined
                                ? ` · INR ${Number(minBudget).toLocaleString()}–${Number(maxBudget).toLocaleString()}`
                                : minBudget !== undefined
                                    ? ` · minimum INR ${Number(minBudget).toLocaleString()}`
                                    : maxBudget !== undefined
                                        ? ` · maximum INR ${Number(maxBudget).toLocaleString()}`
                                        : '';
                            const comparedAmount = budgetComparison.comparison_label
                                ? ` · compared ${escapeHtml(budgetComparison.comparison_label)}`
                                : '';
                            budgetStatusHtml = `<div style="margin-top:6px;"><strong>${escapeHtml(basisLabel)}: ${escapeHtml(budgetLabels[budgetComparison.status] || 'not compared')}${budgetRange}${comparedAmount}</strong></div>`;
                        }
                        // Embed a sentinel div with a data-combo-idx for PayPal button mounting
                        const comboIdx = checkoutCombos.length;
                        checkoutCombos.push({
                            flight_option: est.flight_option,
                            hotel_option:  est.hotel_option,
                            flight_price:  est.flight_price,
                            hotel_price:   est.hotel_total,
                            nights:        combinedTripCost.hotel_nights || 0,
                            travelers:     combinedTripCost.travelers || 1,
                            destination:   plan ? (plan.destination || '') : ''
                        });
                        html += `<div style="border:1px solid #ddd6fe; border-radius:6px; padding:12px; background:#f5f3ff;">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                                <strong style="color:#5b21b6; font-size:0.95rem;">Combination ${idx + 1}</strong>
                                <span style="font-size:1.15rem; font-weight:700; color:#6d28d9;">${escapeHtml(est.formatted_total)}</span>
                            </div>
                            <div style="font-size:0.85rem; color:#4c1d95; line-height:1.5;">
                                <div>✈️ <strong>Flight:</strong> ${escapeHtml(est.flight_option)} • ₹${est.flight_price.toLocaleString()} <span style="font-size:0.75rem; color:#6d28d9;">(${escapeHtml(est.flight_scope)})</span></div>
                                <div>🏨 <strong>Hotel:</strong> ${escapeHtml(est.hotel_option)} • ₹${est.hotel_total.toLocaleString()} <span style="font-size:0.75rem; color:#6d28d9;">(${escapeHtml(est.hotel_scope)})</span></div>
                                ${budgetStatusHtml}
                            </div>
                            <div style="margin-top:8px; font-size:0.75rem; color:#6b7280; border-top:1px dashed #c4b5fd; padding-top:6px; line-height:1.4;">
                                <div><strong>Includes:</strong> ${escapeHtml(est.inclusions || 'Flight + Hotel')}</div>
                                <div><strong>Excludes:</strong> ${escapeHtml(est.exclusions)}</div>
                                <div style="margin-top:4px; color:#7c3aed; font-style:italic;">⚠️ ${escapeHtml(est.disclaimer || 'Search estimate only; not a guaranteed booking price.')}</div>
                            </div>
                            <div class="pp-btn-mount" data-combo-idx="${comboIdx}" style="margin-top:4px;"></div>
                        </div>`;
                    } else {
                        html += `<div style="border:1px solid #fed7aa; border-radius:6px; padding:10px; background:#fff7ed; font-size:0.85rem; color:#9a3412;">
                            <strong>${escapeHtml(est.flight_option || 'Flight')} + ${escapeHtml(est.hotel_option || 'Hotel')}:</strong>
                            <span>Combined cost unavailable (reliable price unavailable for one or both options)</span>
                        </div>`;
                    }
                });

                html += `</div>`;
            } else {
                html += `<div style="margin-top:10px; padding:10px 14px; background:#fff7ed; border-left:4px solid #f97316; border-radius:4px; font-size:0.9rem; color:#9a3412;">
                    ⚠️ <strong>Combined cost unavailable:</strong> Reliable price information could not be determined for both flight and hotel components.
                </div>`;
            }

            html += `</div>`;
        }
        
        const msgDiv = document.createElement('div');
        msgDiv.style.marginBottom = '10px';
        msgDiv.innerHTML = html;
        messagesContainer.appendChild(msgDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;

        // Mount PayPal checkout buttons into sentinel elements after DOM is attached
        if (typeof window.renderCheckoutButton === 'function' && combinedTripCost && combinedTripCost.estimates) {
            const checkoutCombos = [];
            (combinedTripCost.estimates || []).forEach(est => {
                if (est.status === 'available') {
                    checkoutCombos.push({
                        flight_option: est.flight_option,
                        hotel_option:  est.hotel_option,
                        flight_price:  est.flight_price,
                        hotel_price:   est.hotel_total,
                        nights:        combinedTripCost.hotel_nights || 0,
                        travelers:     combinedTripCost.travelers || 1,
                        destination:   plan ? (plan.destination || '') : ''
                    });
                }
            });
            msgDiv.querySelectorAll('.pp-btn-mount').forEach(mount => {
                const idx = parseInt(mount.dataset.comboIdx, 10);
                if (!isNaN(idx) && checkoutCombos[idx]) {
                    window.renderCheckoutButton(mount, checkoutCombos[idx]);
                }
            });
        }
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
                    } else if (data.agent === 'travel_agent' && data.status === 'completed') {
                        appendMessage('✈️ Travel Agent', '<em>Flight search completed.</em>');
                    } else if (data.agent === 'hotel_agent' && data.status === 'preparing') {
                        appendMessage('🏨 Hotel Agent', '<em>Preparing your hotel search...</em>');
                    } else if (data.agent === 'hotel_agent' && data.status === 'searching_hotels') {
                        appendMessage('🏨 Hotel Agent', '<em>Searching accommodation options...</em>');
                    } else if (data.agent === 'hotel_agent' && data.status === 'completed') {
                        appendMessage('🏨 Hotel Agent', '<em>Hotel search completed.</em>');
                    }
                } else if (data.type === 'plan') {
                    displayPlan(data.data.plan, data.data.transport_results, data.data.hotel_results, data.data.combined_trip_cost);
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

    // Dynamic Quick Prompt Chips handler
    const quickPromptsContainer = document.getElementById('quickPrompts');
    if (quickPromptsContainer) {
        quickPromptsContainer.addEventListener('click', (e) => {
            const chip = e.target.closest('.quick-chip');
            if (!chip) return;
            const prompt = chip.getAttribute('data-prompt');
            if (prompt) {
                input.value = prompt;
                if (!ws || ws.readyState !== WebSocket.OPEN) {
                    // Automatically trigger connection if disconnected
                    connectBtn.click();
                    // Wait briefly for connection then focus input
                    setTimeout(() => {
                        input.focus();
                    }, 500);
                } else {
                    sendMessage();
                }
            }
        });
    }
});
