document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('protectedArea')) return;

    const form = document.getElementById('preferenceForm');
    const categoryInput = document.getElementById('preferenceCategory');
    const valueInput = document.getElementById('preferenceValue');
    const valueGroup = document.getElementById('preferenceCustomGroup');
    const valueLabel = document.getElementById('preferenceValueLabel');
    const detailsInput = document.getElementById('preferenceDetails');
    const detailsGroup = document.getElementById('preferenceDetailsGroup');
    const choicesContainer = document.getElementById('preferenceChoices');
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
    const choices = {
        destination: {
            multiple: true,
            options: ['Uttarakhand', 'Himachal Pradesh', 'Goa', 'Rajasthan', 'Kerala', 'Northeast India', 'Europe', 'Southeast Asia', 'Japan'],
            image: 'destination'
        },
        accommodation: {
            options: ['budget', 'hostel', 'standard hotel', 'premium hotel', 'luxury', 'resort', 'homestay', 'guesthouse', 'vacation rental', 'camps or cottages', 'no preference'],
            image: 'stay'
        },
        interest: {
            multiple: true,
            options: ['Mountains and nature', 'Adventure and trekking', 'Beaches', 'History and culture', 'Food and local cuisine', 'Cafes and restaurants', 'Shopping', 'Nightlife', 'Spiritual places', 'Wildlife and safaris', 'Photography', 'Museums and art', 'Theme parks', 'Relaxation and wellness', 'Road trips']
        },
        transport: {
            options: ['Flight', 'Train', 'Bus', 'Private cab or car', 'Rental vehicle', 'Public transportation', 'Self-driving', 'No preference'],
            image: 'transport'
        },
        pace: {
            options: ['relaxed', 'balanced', 'packed'],
            descriptions: ['Around 1–2 main activities per day', 'Around 3–4 activities per day', 'More activities, subject to realistic travel times']
        },
        dietary: {
            options: ['No preference', 'Vegetarian', 'Vegan', 'Eggetarian', 'Non-vegetarian', 'Jain food', 'Other']
        },
        accessibility: {
            multiple: true,
            options: ['Wheelchair-accessible accommodation or transport', 'Step-free access', 'Limited walking', 'Elder-friendly arrangements', 'Stroller-friendly routes', 'Other requirements']
        }
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
            total_trip: 'total budget for the entire trip',
            per_person: 'budget per traveler',
            unspecified: 'basis needs selection'
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

    function renderChoices(category, selectedValues = []) {
        choicesContainer.replaceChildren();
        const configuration = choices[category];
        if (!configuration) return;
        const group = document.createElement('fieldset');
        group.className = configuration.image ? 'preference-choice-group image-choice-group' : 'preference-choice-group';
        const legend = document.createElement('legend');
        legend.textContent = configuration.multiple ? 'Choose any that apply' : 'Choose one';
        group.appendChild(legend);

        if (category === 'dietary') {
            const select = document.createElement('select');
            select.id = 'preferenceOptionSelect';
            select.setAttribute('aria-label', 'Dietary preference');
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Choose a dietary preference';
            select.appendChild(placeholder);
            configuration.options.forEach(option => {
                const item = document.createElement('option');
                item.value = option;
                item.textContent = option;
                item.selected = selectedValues.includes(option);
                select.appendChild(item);
            });
            group.appendChild(select);
        } else {
            const options = document.createElement('div');
            options.className = configuration.image ? 'preference-image-options' : 'preference-chip-options';
            configuration.options.forEach((option, index) => {
                const label = document.createElement('label');
                label.className = configuration.image ? 'preference-image-option' : 'preference-chip-option';
                const input = document.createElement('input');
                input.type = configuration.multiple ? 'checkbox' : 'radio';
                input.name = 'preferenceOption';
                input.value = option;
                input.checked = selectedValues.includes(option);
                if (configuration.image) {
                    const image = document.createElement('img');
                    image.src = `assets/${configuration.image}-illustration.svg`;
                    image.alt = configuration.image === 'stay'
                        ? 'Generic accommodation illustration, not a bookable property'
                        : 'Generic transportation illustration, not a specific service';
                    image.width = 180;
                    image.height = 84;
                    image.loading = 'lazy';
                    label.appendChild(image);
                }
                const title = document.createElement('span');
                title.textContent = option.charAt(0).toUpperCase() + option.slice(1);
                label.append(input, title);
                if (configuration.descriptions) {
                    const description = document.createElement('small');
                    description.textContent = configuration.descriptions[index];
                    label.appendChild(description);
                }
                options.appendChild(label);
            });
            group.appendChild(options);
        }
        choicesContainer.appendChild(group);
    }

    function updateCategoryFields(selectedValues = []) {
        const isBudget = categoryInput.value === 'budget';
        budgetFields.classList.toggle('hidden', !isBudget);
        choicesContainer.classList.toggle('hidden', isBudget || !choices[categoryInput.value]);
        valueGroup.classList.toggle('hidden', isBudget);
        detailsGroup.classList.toggle('hidden', isBudget);
        valueInput.required = !isBudget && !choices[categoryInput.value];
        valueLabel.textContent = choices[categoryInput.value]
            ? 'Custom option (optional)'
            : 'Preference';
        valueInput.placeholder = categoryInput.value === 'destination'
            ? 'Add another destination'
            : 'Add a custom preference';
        budgetBasisInput.required = isBudget;
        renderChoices(categoryInput.value, selectedValues);
    }

    function resetForm() {
        editingId = null;
        form.reset();
        currencyInput.value = 'INR';
        budgetBasisInput.value = '';
        detailsInput.value = '';
        saveButton.textContent = 'Save preference';
        cancelButton.classList.add('hidden');
        updateCategoryFields();
    }

    function startEditing(preference) {
        editingId = preference.id;
        categoryInput.value = preference.category;
        const savedValue = preference.value || '';
        const knownOptions = choices[preference.category]?.options || [];
        const selected = knownOptions.includes(savedValue) ? [savedValue] : [];
        valueInput.value = selected.length ? '' : savedValue;
        detailsInput.value = '';
        minInput.value = preference.min_amount ?? '';
        maxInput.value = preference.max_amount ?? '';
        currencyInput.value = preference.currency || 'INR';
        budgetBasisInput.value = preference.budget_basis === 'unspecified'
            ? ''
            : preference.budget_basis;
        saveButton.textContent = 'Update preference';
        cancelButton.classList.remove('hidden');
        updateCategoryFields(selected);
        form.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    async function loadPreferences() {
        const data = await request('');
        preferences = data.preferences || [];
        renderPreferences();
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

    categoryInput.addEventListener('change', () => updateCategoryFields());
    cancelButton.addEventListener('click', resetForm);

    form.addEventListener('submit', async event => {
        event.preventDefault();
        const category = categoryInput.value;
        const selectedValues = Array.from(
            choicesContainer.querySelectorAll('input[name="preferenceOption"]:checked')
        ).map(input => input.value);
        const dropdown = choicesContainer.querySelector('#preferenceOptionSelect');
        if (dropdown?.value) selectedValues.push(dropdown.value);
        const customValue = valueInput.value.trim();
        if (!selectedValues.length && !customValue && category !== 'budget') {
            showStatus('Choose or enter at least one preference.', true);
            return;
        }
        const details = detailsInput.value.trim();
        const values = [...new Set([
            ...selectedValues,
            ...(customValue ? [customValue] : [])
        ])].map(value => details ? `${value} — ${details}` : value);
        const body = { category, value: values[0] || '' };
        if (categoryInput.value === 'budget') {
            body.min_amount = minInput.value === '' ? null : Number(minInput.value);
            body.max_amount = maxInput.value === '' ? null : Number(maxInput.value);
            body.currency = currencyInput.value.toUpperCase() || 'INR';
            body.budget_basis = budgetBasisInput.value || 'unspecified';
            if (
                body.min_amount === null && body.max_amount === null
                || body.min_amount !== null && (!Number.isFinite(body.min_amount) || body.min_amount < 0)
                || body.max_amount !== null && (!Number.isFinite(body.max_amount) || body.max_amount < 0)
                || body.min_amount !== null && body.max_amount !== null && body.min_amount > body.max_amount
            ) {
                showStatus('Enter a valid non-negative budget range with minimum not greater than maximum.', true);
                return;
            }
        }

        saveButton.disabled = true;
        try {
            const valuesToSave = category === 'budget' ? [body.value] : (values.length ? values : [body.value]);
            for (let index = 0; index < valuesToSave.length; index += 1) {
                const preferenceBody = { ...body, value: valuesToSave[index] };
                const isUpdate = editingId !== null && index === 0;
                await request(isUpdate ? `/${editingId}` : '', {
                    method: isUpdate ? 'PUT' : 'POST',
                    body: JSON.stringify(preferenceBody)
                });
            }
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
    loadPreferences().catch(error => showStatus(error.message, true));
});
