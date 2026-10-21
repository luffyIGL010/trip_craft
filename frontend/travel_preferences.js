document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('protectedArea')) return;

    const form = document.getElementById('preferenceForm');
    const categoryInput = document.getElementById('preferenceCategory');
    const valueInput = document.getElementById('preferenceValue');
    const valueGroup = document.getElementById('preferenceValueGroup');
    const budgetFields = document.getElementById('preferenceBudgetFields');
    const minInput = document.getElementById('preferenceMin');
    const maxInput = document.getElementById('preferenceMax');
    const currencyInput = document.getElementById('preferenceCurrency');
    const budgetBasisInput = document.getElementById('preferenceBudgetBasis');
    const list = document.getElementById('travelPreferencesList');
    const emptyMessage = document.getElementById('emptyPreferences');
    const status = document.getElementById('preferenceStatus');
    const saveButton = document.getElementById('savePreferenceBtn');
    const cancelButton = document.getElementById('cancelPreferenceEditBtn');
    let editingId = null;
    let preferences = [];

    const categoryNames = {
        destination: 'Destination or region',
        budget: 'Usual budget',
        accommodation: 'Accommodation',
        interest: 'Interest',
        transport: 'Transportation',
        pace: 'Trip pace',
        dietary: 'Dietary preference',
        accessibility: 'Accessibility'
    };

    function showStatus(message, isError = false) {
        status.classList.remove('hidden', 'success', 'error');
        status.classList.add(isError ? 'error' : 'success');
        status.textContent = message;
    }

    async function request(path, options = {}) {
        const token = getToken();
        if (!token) throw new Error('Your session has expired. Please sign in again.');
        const response = await fetch(`${API_URL}/api/preferences${path}`, {
            ...options,
            headers: {
                ...(options.body ? { 'Content-Type': 'application/json' } : {}),
                'Authorization': `Bearer ${token}`,
                ...options.headers
            }
        });
        if (response.status === 204) return null;
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || 'Could not update travel preferences.');
        }
        return data;
    }

    function preferenceValue(preference) {
        if (preference.category !== 'budget') return preference.value;
        const amount = value => value === null || value === undefined
            ? null
            : `${preference.currency || ''} ${Number(value).toLocaleString()}`.trim();
        const lower = amount(preference.min_amount);
        const upper = amount(preference.max_amount);
        const range = lower && upper ? `${lower} – ${upper}` : lower || upper || preference.value;
        const basis = {
            total_trip: 'total trip (all travelers combined)',
            per_person: 'per person',
            unspecified: 'meaning not set'
        }[preference.budget_basis] || 'meaning not set';
        return `${range} · ${basis}`;
    }

    function renderPreferences() {
        list.replaceChildren();
        emptyMessage.classList.toggle('hidden', preferences.length > 0);
        document.getElementById('clearPreferencesBtn').disabled = preferences.length === 0;

        preferences.forEach(preference => {
            const item = document.createElement('li');
            item.className = 'preference-item';

            const description = document.createElement('div');
            const title = document.createElement('div');
            title.className = 'preference-item-title';
            title.textContent = categoryNames[preference.category] || preference.category;
            const value = document.createElement('div');
            value.className = 'preference-item-value';
            value.textContent = preferenceValue(preference);
            description.append(title, value);

            const actions = document.createElement('div');
            actions.className = 'actions';
            const editButton = document.createElement('button');
            editButton.type = 'button';
            editButton.className = 'secondary';
            editButton.textContent = 'Edit';
            editButton.addEventListener('click', () => startEditing(preference));
            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.className = 'danger';
            deleteButton.textContent = 'Delete';
            deleteButton.addEventListener('click', () => deletePreference(preference.id));
            actions.append(editButton, deleteButton);
            item.append(description, actions);
            list.appendChild(item);
        });
    }

    function updateCategoryFields() {
        const isBudget = categoryInput.value === 'budget';
        budgetFields.classList.toggle('hidden', !isBudget);
        valueGroup.classList.toggle('hidden', isBudget);
        valueInput.required = !isBudget;
        budgetBasisInput.required = isBudget;
    }

    function resetForm() {
        editingId = null;
        form.reset();
        currencyInput.value = 'INR';
        budgetBasisInput.value = '';
        saveButton.textContent = 'Save preference';
        cancelButton.classList.add('hidden');
        updateCategoryFields();
    }

    function startEditing(preference) {
        editingId = preference.id;
        categoryInput.value = preference.category;
        valueInput.value = preference.value || '';
        minInput.value = preference.min_amount ?? '';
        maxInput.value = preference.max_amount ?? '';
        currencyInput.value = preference.currency || 'INR';
        budgetBasisInput.value = preference.budget_basis === 'unspecified'
            ? ''
            : preference.budget_basis;
        saveButton.textContent = 'Update preference';
        cancelButton.classList.remove('hidden');
        updateCategoryFields();
        form.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    async function loadPreferences() {
        try {
            const data = await request('');
            preferences = data.preferences || [];
            renderPreferences();
        } catch (error) {
            showStatus(error.message, true);
        }
    }

    async function deletePreference(id) {
        if (!window.confirm('Delete this saved preference?')) return;
        try {
            await request(`/${id}`, { method: 'DELETE' });
            preferences = preferences.filter(preference => preference.id !== id);
            renderPreferences();
            showStatus('Preference deleted.');
        } catch (error) {
            showStatus(error.message, true);
        }
    }

    categoryInput.addEventListener('change', updateCategoryFields);
    cancelButton.addEventListener('click', resetForm);

    form.addEventListener('submit', async event => {
        event.preventDefault();
        const body = {
            category: categoryInput.value,
            value: valueInput.value.trim()
        };
        if (categoryInput.value === 'budget') {
            body.min_amount = minInput.value === '' ? null : Number(minInput.value);
            body.max_amount = maxInput.value === '' ? null : Number(maxInput.value);
            body.currency = currencyInput.value.trim().toUpperCase() || 'INR';
            body.budget_basis = budgetBasisInput.value || 'unspecified';
        }

        saveButton.disabled = true;
        try {
            await request(editingId ? `/${editingId}` : '', {
                method: editingId ? 'PUT' : 'POST',
                body: JSON.stringify(body)
            });
            resetForm();
            await loadPreferences();
            showStatus('Travel preference saved.');
        } catch (error) {
            showStatus(error.message, true);
        } finally {
            saveButton.disabled = false;
        }
    });

    document.getElementById('clearPreferencesBtn').addEventListener('click', async () => {
        if (!window.confirm('Clear your entire travel preference profile?')) return;
        try {
            await request('', { method: 'DELETE' });
            preferences = [];
            renderPreferences();
            resetForm();
            showStatus('Travel preference profile cleared.');
        } catch (error) {
            showStatus(error.message, true);
        }
    });

    updateCategoryFields();
    loadPreferences();
});
