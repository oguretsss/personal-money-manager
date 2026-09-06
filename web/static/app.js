/* ── Utilities ───────────────────────────────────────────────────────── */

function todayDate() {
    return new Date().toISOString().slice(0, 10);
}

function parseAmount(value) {
    if (typeof value === 'number') return value;
    return parseFloat(String(value).replace(',', '.'));
}

function formatMoney(value) {
    return `€${Number(value || 0).toFixed(2)}`;
}

function limitStatusForPercentage(percentage) {
    if (percentage >= 100) return 'exceeded';
    if (percentage >= 70) return 'caution';
    if (percentage >= 50) return 'warning';
    return 'safe';
}

function projectedLimitFor(limits, form) {
    if (!form || form.type !== 'expense' || !form.category_name) return null;
    const amount = parseAmount(form.amount);
    if (!Number.isFinite(amount) || amount <= 0) return null;

    const limit = (limits || []).find(item => item.category === form.category_name);
    if (!limit) return null;
    const transactionMonth = String(form.happened_at || todayDate()).slice(0, 7);
    if (transactionMonth !== limit.period) return null;

    const projectedSpent = Number(limit.spent || 0) + amount;
    const percentage = limit.limit > 0 ? (projectedSpent / limit.limit) * 100 : 0;
    const status = limitStatusForPercentage(percentage);
    const overBy = Math.max(projectedSpent - limit.limit, 0);
    let title = 'Within monthly limit';
    let message = `This expense would use ${Math.round(percentage)}% of the ${limit.category} monthly limit (${formatMoney(projectedSpent)} of ${formatMoney(limit.limit)}).`;

    if (status === 'warning') title = '50% threshold';
    if (status === 'caution') title = '70% threshold';
    if (status === 'exceeded') {
        title = 'Limit reached';
        message = overBy > 0
            ? `This expense would exceed the ${limit.category} limit by ${formatMoney(overBy)}.`
            : `This expense would use the full ${limit.category} limit.`;
    }

    return {
        ...limit,
        spent: projectedSpent,
        percentage,
        over_by: overBy,
        status,
        title,
        message,
    };
}

function announceLimitStatus(result) {
    const status = result && result.limit_status;
    if (!status || status.status === 'safe') return false;
    const toastType = status.status === 'exceeded'
        ? 'error'
        : (status.status === 'caution' ? 'caution' : 'warning');
    Alpine.store('toast').add(status.message, toastType);
    return true;
}

function parseBankAmount(value) {
    if (typeof value === 'number') return value;
    const raw = String(value || '').trim();
    if (!raw) return NaN;

    let normalized = raw
        .replace(/\u2212/g, '-')
        .replace(/[\s\u00A0]/g, '')
        .replace(/[^\d.,+\-]/g, '');
    if (!/\d/.test(normalized)) return NaN;

    const commaIndex = normalized.lastIndexOf(',');
    const dotIndex = normalized.lastIndexOf('.');
    if (commaIndex !== -1 && dotIndex !== -1) {
        if (commaIndex > dotIndex) {
            normalized = normalized.replace(/\./g, '').replace(',', '.');
        } else {
            normalized = normalized.replace(/,/g, '');
        }
    } else if (commaIndex !== -1) {
        normalized = normalized.replace(',', '.');
    }

    return Number(normalized);
}

function parseCsv(text) {
    const rows = [];
    let row = [];
    let field = '';
    let inQuotes = false;

    for (let i = 0; i < text.length; i++) {
        const ch = text[i];
        const next = text[i + 1];

        if (ch === '"') {
            if (inQuotes && next === '"') {
                field += '"';
                i++;
            } else {
                inQuotes = !inQuotes;
            }
            continue;
        }

        if (ch === ',' && !inQuotes) {
            row.push(field);
            field = '';
            continue;
        }

        if ((ch === '\n' || ch === '\r') && !inQuotes) {
            if (ch === '\r' && next === '\n') i++;
            row.push(field);
            if (row.some(cell => cell !== '')) rows.push(row);
            row = [];
            field = '';
            continue;
        }

        field += ch;
    }

    row.push(field);
    if (row.some(cell => cell !== '')) rows.push(row);
    return rows;
}

function normalizeCsvHeader(value) {
    return String(value || '').replace(/^\uFEFF/, '').trim().toLowerCase();
}

function parseBankDate(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';

    const statementDate = raw.match(/^(\d{1,2})-([A-Za-z]{3})-(\d{2}|\d{4})$/);
    if (statementDate) {
        const months = {
            jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5,
            jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11,
        };
        const day = Number(statementDate[1]);
        const month = months[statementDate[2].toLowerCase()];
        const shortYear = Number(statementDate[3]);
        const year = statementDate[3].length === 2
            ? (shortYear < 70 ? 2000 + shortYear : 1900 + shortYear)
            : shortYear;
        if (month === undefined) return '';

        const parsed = new Date(Date.UTC(year, month, day));
        if (parsed.getUTCFullYear() !== year
            || parsed.getUTCMonth() !== month
            || parsed.getUTCDate() !== day) {
            return '';
        }
        return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}T00:00`;
    }

    const normalized = raw.replace(' ', 'T');
    const match = normalized.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2}))?/);
    if (match) {
        return `${match[1]}T${match[2]}`;
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
        return `${raw}T00:00`;
    }
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return '';
    const pad = n => String(n).padStart(2, '0');
    return [
        parsed.getFullYear(),
        '-',
        pad(parsed.getMonth() + 1),
        '-',
        pad(parsed.getDate()),
        'T',
        pad(parsed.getHours()),
        ':',
        pad(parsed.getMinutes()),
    ].join('');
}

async function apiCall(method, url, body) {
    const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const resp = await fetch(url, opts);
    if (resp.status === 401 || resp.redirected) {
        window.location.href = '/login';
        return null;
    }
    const contentType = resp.headers.get('content-type') || '';
    let data = {};
    if (contentType.includes('application/json')) {
        data = await resp.json();
    } else {
        const text = await resp.text();
        data = text ? { error: text } : {};
    }
    if (!resp.ok) {
        const msg = data.error || data.detail || 'Something went wrong';
        Alpine.store('toast').add(msg, 'error');
        return null;
    }
    return data;
}

/* ── Alpine stores (registered before Alpine starts) ────────────────── */

document.addEventListener('alpine:init', () => {

    // Toast notification store
    Alpine.store('toast', {
        messages: [],
        add(message, type = 'info') {
            const id = Date.now() + Math.random();
            this.messages.push({ id, message, type });
            setTimeout(() => {
                this.messages = this.messages.filter(m => m.id !== id);
            }, 3500);
        },
    });

    // Shared app data store (categories, users — cached)
    Alpine.store('app', {
        categories: null,
        users: null,
        limits: null,
        async fetchCategories() {
            if (this.categories) return this.categories;
            const data = await apiCall('GET', '/api/categories');
            if (data) this.categories = data;
            return this.categories || [];
        },
        async fetchUsers() {
            if (this.users) return this.users;
            const data = await apiCall('GET', '/api/users');
            if (data) this.users = data;
            return this.users || [];
        },
        async fetchLimits() {
            if (this.limits) return this.limits;
            const data = await apiCall('GET', '/api/limits');
            if (data) this.limits = data;
            return this.limits || [];
        },
        invalidate() {
            this.categories = null;
            this.users = null;
            this.limits = null;
        },
    });

});

/* ── FAB Quick Expense component ────────────────────────────────────── */

function fabExpense() {
    return {
        showModal: false,
        loading: false,
        categories: [],
        users: [],
        limits: [],
        form: {
            type: 'expense',
            amount: '',
            category_name: '',
            created_by_telegram_id: '',
            happened_at: todayDate(),
            note: '',
        },

        async openModal() {
            this.form.happened_at = todayDate();
            this.form.amount = '';
            this.form.category_name = '';
            this.form.note = '';
            this.form.type = 'expense';
            this.categories = await Alpine.store('app').fetchCategories();
            this.users = await Alpine.store('app').fetchUsers();
            this.limits = await Alpine.store('app').fetchLimits();
            if (this.users.length && !this.form.created_by_telegram_id) {
                this.form.created_by_telegram_id = this.users[0].telegram_id;
            }
            this.showModal = true;
        },

        projectedLimit() {
            return projectedLimitFor(this.limits, this.form);
        },

        async submit() {
            if (!this.form.amount || !this.form.category_name) {
                Alpine.store('toast').add('Please fill amount and category', 'error');
                return;
            }
            this.loading = true;
            const payload = {
                type: this.form.type,
                amount: parseAmount(this.form.amount),
                category_name: this.form.category_name,
                created_by_telegram_id: parseInt(this.form.created_by_telegram_id),
                note: this.form.note || '',
            };
            if (this.form.happened_at) {
                payload.happened_at = this.form.happened_at + 'T00:00:00';
            }
            const result = await apiCall('POST', '/api/transactions', payload);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Transaction created', 'success');
                const hasLimitAlert = announceLimitStatus(result);
                this.showModal = false;
                Alpine.store('app').invalidate();
                // Reload page to reflect new data
                setTimeout(() => window.location.reload(), hasLimitAlert ? 1800 : 500);
            }
        },
    };
}

/* ── Dashboard category limit details ───────────────────────────────── */

function categoryLimitCard(category, period) {
    // Use the card's month, even if the page stays open into the next month.
    const [year, month] = period.split('-').map(Number);
    const nextMonth = new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 10);
    return {
        expanded: false,
        loading: false,
        loaded: false,
        error: false,
        transactions: [],
        total: 0,
        page: 0,

        async toggle() {
            this.expanded = !this.expanded;
            if (this.expanded && !this.loaded) await this.loadTransactions();
        },

        async loadTransactions() {
            if (this.loading) return;
            this.loading = true;
            this.error = false;
            const params = new URLSearchParams({
                type: 'expense',
                category,
                start: `${period}-01T00:00:00`,
                end: `${nextMonth}T00:00:00`,
                page: this.page + 1,
                per_page: 50,
            });
            try {
                const data = await apiCall('GET', `/api/transactions?${params}`);
                if (!data) {
                    this.error = true;
                    return;
                }
                this.transactions.push(...data.items);
                this.total = data.total;
                this.page = data.page;
                this.loaded = true;
            } catch {
                this.error = true;
            } finally {
                this.loading = false;
            }
        },

        transactionDate(value) {
            // Preserve the recorded calendar date without timezone conversion.
            return new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, {
                day: 'numeric', month: 'short', year: 'numeric',
            });
        },
    };
}

/* ── Transactions page component ────────────────────────────────────── */

function transactionsPage(initialData, initialCategories, initialUsers, initialLimits = [], initialSpaces = []) {
    return {
        transactions: initialData.items || [],
        total: initialData.total || 0,
        page: initialData.page || 1,
        perPage: initialData.per_page || 50,
        categories: initialCategories || [],
        users: initialUsers || [],
        limits: initialLimits || [],
        spaces: initialSpaces || [],
        csvRows: [],
        csvErrors: [],
        csvFileName: '',
        csvSkippedIncomeCount: 0,
        csvSkippedInvalidCount: 0,
        csvDragOver: false,
        csvImportUserId: '',

        // Add modal
        showAdd: false,
        addForm: {
            type: 'expense',
            amount: '',
            category_name: '',
            created_by_telegram_id: '',
            happened_at: todayDate(),
            note: '',
        },

        // Edit modal
        showEdit: false,
        editForm: { id: null, type: '', amount: '', category_name: '', happened_at: '', note: '' },

        // CSV import modal
        showCsvImport: false,

        // Confirm delete
        showConfirm: false,
        confirmId: null,

        // Income sorter
        showIncomeSort: false,
        incomeSortTransaction: null,
        incomeSortAllocations: [],
        saveIncomeSortTemplate: true,
        showUndoSortConfirm: false,
        undoSortId: null,

        loading: false,

        init() {
            if (this.users.length && !this.addForm.created_by_telegram_id) {
                this.addForm.created_by_telegram_id = this.users[0].telegram_id;
            }
            if (this.users.length && !this.csvImportUserId) {
                this.csvImportUserId = this.users[0].telegram_id;
            }
        },

        expenseCategories() {
            return this.categories.filter(c => c.type === 'expense');
        },

        formatMoney,

        incomeSortUserName() {
            if (!this.incomeSortTransaction) return '';
            const user = this.users.find(
                item => Number(item.telegram_id) === Number(this.incomeSortTransaction.created_by_telegram_id),
            );
            return user ? user.name : String(this.incomeSortTransaction.created_by_telegram_id);
        },

        incomeSortPayload() {
            return this.incomeSortAllocations
                .map(allocation => ({
                    space_id: allocation.space_id,
                    amount: parseAmount(allocation.amount),
                }))
                .filter(allocation => Number.isFinite(allocation.amount) && allocation.amount > 0)
                .map(allocation => ({
                    space_id: allocation.space_id,
                    amount: Math.round(allocation.amount * 100) / 100,
                }));
        },

        incomeSortAllocated() {
            const cents = this.incomeSortPayload().reduce(
                (total, allocation) => total + Math.round(allocation.amount * 100),
                0,
            );
            return cents / 100;
        },

        incomeSortRemaining() {
            if (!this.incomeSortTransaction) return 0;
            const incomeCents = Math.round(Number(this.incomeSortTransaction.amount || 0) * 100);
            const allocatedCents = Math.round(this.incomeSortAllocated() * 100);
            return (incomeCents - allocatedCents) / 100;
        },

        incomeSortValidationMessage() {
            if (!this.incomeSortTransaction) return 'No income selected.';
            const invalid = this.incomeSortAllocations.some(allocation => {
                const raw = String(allocation.amount ?? '').trim();
                if (!raw) return false;
                const amount = parseAmount(raw);
                return !Number.isFinite(amount) || amount < 0;
            });
            if (invalid) return 'Every amount must be zero or a positive number.';
            if (!this.incomeSortPayload().length) return 'Enter an amount for at least one Space.';
            if (this.incomeSortRemaining() < 0) return 'Allocated amount cannot exceed this income.';
            return '';
        },

        async openIncomeSort(transaction) {
            if (!this.spaces.length) {
                Alpine.store('toast').add('Create a Space before sorting income', 'error');
                return;
            }
            this.incomeSortTransaction = transaction;
            this.incomeSortAllocations = this.spaces.map(space => ({
                space_id: space.id,
                space_name: space.name,
                amount: '',
            }));
            this.saveIncomeSortTemplate = true;
            this.showIncomeSort = true;
            this.loading = true;
            const template = await apiCall(
                'GET',
                `/api/income-sort/templates/${transaction.created_by_telegram_id}`,
            );
            this.loading = false;
            if (!template) return;
            const amountBySpace = new Map(
                (template.allocations || []).map(item => [Number(item.space_id), Number(item.amount)]),
            );
            this.incomeSortAllocations.forEach(allocation => {
                if (amountBySpace.has(Number(allocation.space_id))) {
                    allocation.amount = amountBySpace.get(Number(allocation.space_id)).toFixed(2);
                }
            });
        },

        async saveCurrentIncomeSortTemplate() {
            const validationError = this.incomeSortValidationMessage();
            if (validationError) {
                Alpine.store('toast').add(validationError, 'error');
                return;
            }
            this.loading = true;
            const result = await apiCall(
                'PUT',
                `/api/income-sort/templates/${this.incomeSortTransaction.created_by_telegram_id}`,
                { allocations: this.incomeSortPayload() },
            );
            this.loading = false;
            if (result) Alpine.store('toast').add('Income sort template saved', 'success');
        },

        async submitIncomeSort() {
            const validationError = this.incomeSortValidationMessage();
            if (validationError) {
                Alpine.store('toast').add(validationError, 'error');
                return;
            }
            this.loading = true;
            const result = await apiCall(
                'POST',
                `/api/transactions/${this.incomeSortTransaction.id}/income-sort`,
                {
                    allocations: this.incomeSortPayload(),
                    save_template: this.saveIncomeSortTemplate,
                },
            );
            this.loading = false;
            if (result) {
                Alpine.store('toast').add(
                    `Income sorted: ${formatMoney(result.allocated_amount)} allocated`,
                    'success',
                );
                this.showIncomeSort = false;
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },

        confirmUndoIncomeSort(id) {
            this.undoSortId = id;
            this.showUndoSortConfirm = true;
        },

        async doUndoIncomeSort() {
            this.loading = true;
            const result = await apiCall('DELETE', `/api/transactions/${this.undoSortId}/income-sort`);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Income sort undone', 'success');
                this.showUndoSortConfirm = false;
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },

        openAdd() {
            this.addForm.happened_at = todayDate();
            this.addForm.amount = '';
            this.addForm.category_name = '';
            this.addForm.note = '';
            this.addForm.type = 'expense';
            if (this.users.length && !this.addForm.created_by_telegram_id) {
                this.addForm.created_by_telegram_id = this.users[0].telegram_id;
            }
            this.showAdd = true;
        },

        projectedLimit() {
            return projectedLimitFor(this.limits, this.addForm);
        },

        openCsvImport() {
            this.csvErrors = [];
            this.csvDragOver = false;
            if (this.users.length && !this.csvImportUserId) {
                this.csvImportUserId = this.users[0].telegram_id;
            }
            this.showCsvImport = true;
        },

        closeCsvImport() {
            this.showCsvImport = false;
            this.csvDragOver = false;
        },

        csvSummaryText() {
            const parts = [`${this.csvRows.length} expenses ready`];
            if (this.csvSkippedIncomeCount) parts.push(`${this.csvSkippedIncomeCount} income skipped`);
            if (this.csvSkippedInvalidCount) parts.push(`${this.csvSkippedInvalidCount} invalid skipped`);
            return parts.join(', ');
        },

        allCsvRowsSelected() {
            const eligible = this.csvRows.filter(row => !row.imported);
            return eligible.length > 0 && eligible.every(row => row.selected);
        },

        setAllCsvRows(selected) {
            this.csvRows.forEach(row => {
                if (!row.imported) row.selected = selected;
            });
        },

        selectedCsvRowsCount() {
            return this.csvRows.filter(row => row.selected && !row.imported).length;
        },

        missingCsvCategoryCount() {
            return this.csvRows.filter(row => row.selected && !row.imported && !row.category_name).length;
        },

        handleCsvDrop(event) {
            this.csvDragOver = false;
            const file = event.dataTransfer.files && event.dataTransfer.files[0];
            this.handleCsvFile(file);
        },

        async handleCsvFile(file) {
            if (!file) return;
            this.csvFileName = file.name;
            this.csvRows = [];
            this.csvErrors = [];
            this.csvSkippedIncomeCount = 0;
            this.csvSkippedInvalidCount = 0;

            try {
                const text = await file.text();
                const rows = parseCsv(text);
                if (rows.length < 2) {
                    this.csvErrors.push('CSV has no transaction rows.');
                    return;
                }

                const header = rows[0].map(normalizeCsvHeader);
                const indexes = {
                    startedDate: header.indexOf('started date'),
                    amount: header.indexOf('amount'),
                    description: header.indexOf('description'),
                };
                const missing = [];
                if (indexes.startedDate === -1) missing.push('Started Date');
                if (indexes.amount === -1) missing.push('Amount');
                if (missing.length) {
                    this.csvErrors.push(`Missing required column(s): ${missing.join(', ')}`);
                    return;
                }

                const parsedRows = [];
                rows.slice(1).forEach((cells, idx) => {
                    const amount = parseBankAmount(cells[indexes.amount]);
                    const happenedAt = parseBankDate(cells[indexes.startedDate]);
                    if (!Number.isFinite(amount) || amount === 0 || !happenedAt) {
                        this.csvSkippedInvalidCount++;
                        return;
                    }
                    if (amount > 0) {
                        this.csvSkippedIncomeCount++;
                        return;
                    }

                    parsedRows.push({
                        id: `${Date.now()}-${idx}`,
                        selected: true,
                        imported: false,
                        happened_at: happenedAt,
                        amount: Math.abs(amount).toFixed(2),
                        category_name: '',
                        note: indexes.description === -1 ? '' : String(cells[indexes.description] || '').trim(),
                    });
                });

                this.csvRows = parsedRows;
                if (!parsedRows.length) {
                    this.csvErrors.push('No expense rows were found in this CSV.');
                }
            } catch (err) {
                this.csvErrors.push('Could not read this CSV file.');
            } finally {
                if (this.$refs.csvInput) this.$refs.csvInput.value = '';
            }
        },

        validateCsvImportRows(rows) {
            if (!this.csvImportUserId) return 'Please select a user.';
            const categoryNames = new Set(this.expenseCategories().map(c => c.name));
            for (const row of rows) {
                const amount = parseAmount(row.amount);
                if (!Number.isFinite(amount) || amount <= 0) return 'Every selected row needs a positive amount.';
                if (!row.happened_at) return 'Every selected row needs a date.';
                if (!row.category_name || !categoryNames.has(row.category_name)) {
                    return 'Every selected row needs an existing expense category.';
                }
            }
            return '';
        },

        async submitCsvImport() {
            const rows = this.csvRows.filter(row => row.selected && !row.imported);
            if (!rows.length) {
                Alpine.store('toast').add('Select at least one expense to import', 'error');
                return;
            }
            const validationError = this.validateCsvImportRows(rows);
            if (validationError) {
                Alpine.store('toast').add(validationError, 'error');
                return;
            }

            this.loading = true;
            let created = 0;
            let failed = 0;
            for (const row of rows) {
                const payload = {
                    type: 'expense',
                    amount: parseAmount(row.amount),
                    category_name: row.category_name,
                    created_by_telegram_id: parseInt(this.csvImportUserId),
                    happened_at: row.happened_at,
                    note: row.note || '',
                };
                const result = await apiCall('POST', '/api/transactions', payload);
                if (result) {
                    row.imported = true;
                    row.selected = false;
                    created++;
                } else {
                    failed++;
                }
            }
            this.loading = false;

            if (created && !failed) {
                Alpine.store('toast').add(`${created} expense(s) imported`, 'success');
                this.showCsvImport = false;
                Alpine.store('app').invalidate();
                window.location.reload();
                return;
            }
            if (created) {
                Alpine.store('toast').add(`${created} imported, ${failed} failed`, 'info');
            }
        },

        async submitAdd() {
            if (!this.addForm.amount || !this.addForm.category_name) {
                Alpine.store('toast').add('Please fill amount and category', 'error');
                return;
            }
            this.loading = true;
            const payload = {
                type: this.addForm.type,
                amount: parseAmount(this.addForm.amount),
                category_name: this.addForm.category_name,
                created_by_telegram_id: parseInt(this.addForm.created_by_telegram_id),
                note: this.addForm.note || '',
            };
            if (this.addForm.happened_at) {
                payload.happened_at = this.addForm.happened_at + 'T00:00:00';
            }
            const result = await apiCall('POST', '/api/transactions', payload);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Transaction created', 'success');
                const hasLimitAlert = announceLimitStatus(result);
                this.showAdd = false;
                Alpine.store('app').invalidate();
                if (hasLimitAlert) {
                    setTimeout(() => window.location.reload(), 1800);
                } else {
                    window.location.reload();
                }
            }
        },

        openEdit(tx) {
            this.editForm = {
                id: tx.id,
                type: tx.type,
                amount: tx.amount,
                category_name: tx.category,
                happened_at: tx.happened_at ? tx.happened_at.slice(0, 10) : '',
                note: tx.note || '',
            };
            this.showEdit = true;
        },

        async submitEdit() {
            this.loading = true;
            const payload = {
                amount: parseAmount(this.editForm.amount),
                category_name: this.editForm.category_name,
                note: this.editForm.note || '',
            };
            if (this.editForm.happened_at) {
                payload.happened_at = this.editForm.happened_at + 'T00:00:00';
            }
            const result = await apiCall('PUT', `/api/transactions/${this.editForm.id}`, payload);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Transaction updated', 'success');
                this.showEdit = false;
                window.location.reload();
            }
        },

        confirmDelete(id) {
            this.confirmId = id;
            this.showConfirm = true;
        },

        async doDelete() {
            this.loading = true;
            const result = await apiCall('DELETE', `/api/transactions/${this.confirmId}`);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Transaction deleted', 'success');
                this.showConfirm = false;
                window.location.reload();
            }
        },
    };
}

/* ── Categories page component ──────────────────────────────────────── */

function limitsPage(initialCategories, initialLimits) {
    return {
        categories: initialCategories || [],
        limits: initialLimits || [],
        amounts: {},
        loadingCategoryId: null,
        showConfirm: false,
        removeCategory: null,

        init() {
            this.categories.forEach(category => {
                const limit = this.limitFor(category.id);
                this.amounts[category.id] = limit ? Number(limit.limit).toFixed(2) : '';
            });
        },

        limitFor(categoryId) {
            return this.limits.find(limit => limit.category_id === categoryId) || null;
        },

        sortedCategories() {
            return [...this.categories].sort((a, b) => {
                const aConfigured = this.limitFor(a.id) ? 0 : 1;
                const bConfigured = this.limitFor(b.id) ? 0 : 1;
                return aConfigured - bConfigured || a.name.localeCompare(b.name);
            });
        },

        formatMoney,

        async saveLimit(category) {
            const amount = parseAmount(this.amounts[category.id]);
            if (!Number.isFinite(amount) || amount <= 0) {
                Alpine.store('toast').add('Enter a positive monthly limit', 'error');
                return;
            }

            this.loadingCategoryId = category.id;
            const result = await apiCall('PUT', `/api/limits/${category.id}`, { amount });
            this.loadingCategoryId = null;
            if (!result) return;

            const index = this.limits.findIndex(limit => limit.category_id === category.id);
            if (index === -1) this.limits.push(result);
            else this.limits.splice(index, 1, result);
            this.amounts[category.id] = Number(result.limit).toFixed(2);
            Alpine.store('app').limits = null;
            Alpine.store('toast').add(`Limit saved for ${category.name}`, 'success');
        },

        askRemove(category) {
            this.removeCategory = category;
            this.showConfirm = true;
        },

        async removeLimit() {
            if (!this.removeCategory) return;
            const category = this.removeCategory;
            this.loadingCategoryId = category.id;
            const result = await apiCall('DELETE', `/api/limits/${category.id}`);
            this.loadingCategoryId = null;
            if (!result) return;

            this.limits = this.limits.filter(limit => limit.category_id !== category.id);
            this.amounts[category.id] = '';
            this.showConfirm = false;
            this.removeCategory = null;
            Alpine.store('app').limits = null;
            Alpine.store('toast').add(`Limit removed for ${category.name}`, 'success');
        },
    };
}

function categoriesPage(initialCategories) {
    return {
        categories: initialCategories || [],
        editId: null,
        editName: '',
        showConfirm: false,
        confirmId: null,
        showCreate: false,
        createForm: { name: '', type: 'expense' },
        loading: false,

        startRename(cat) {
            this.editId = cat.id;
            this.editName = cat.name;
            this.$nextTick(() => {
                const input = this.$refs['renameInput' + cat.id];
                if (input) input.focus();
            });
        },

        cancelRename() {
            this.editId = null;
            this.editName = '';
        },

        async submitRename(catId) {
            if (!this.editName.trim()) return;
            this.loading = true;
            const result = await apiCall('POST', `/api/categories/${catId}/rename`, { name: this.editName.trim() });
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Category renamed', 'success');
                this.editId = null;
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },

        async submitCreate() {
            if (!this.createForm.name.trim()) {
                Alpine.store('toast').add('Please enter a category name', 'error');
                return;
            }
            this.loading = true;
            const result = await apiCall('POST', '/api/categories', {
                name: this.createForm.name.trim(),
                type: this.createForm.type,
            });
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Category created', 'success');
                this.showCreate = false;
                this.createForm = { name: '', type: 'expense' };
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },

        confirmDelete(id) {
            this.confirmId = id;
            this.showConfirm = true;
        },

        async doDelete() {
            this.loading = true;
            const result = await apiCall('DELETE', `/api/categories/${this.confirmId}`);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Category deleted', 'success');
                this.showConfirm = false;
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },
    };
}

/* ── Spaces page component ──────────────────────────────────────────── */

function spacesPage(initialSpaces, initialUsers) {
    return {
        spaces: initialSpaces || [],
        users: initialUsers || [],
        editId: null,
        editName: '',
        showConfirm: false,
        confirmId: null,
        showCreate: false,
        createName: '',
        showTransfer: false,
        transferForm: {
            space_name: '',
            direction: 'to_space',
            amount: '',
            created_by_telegram_id: '',
            happened_at: todayDate(),
            note: '',
        },
        loading: false,

        async submitCreate() {
            if (!this.createName.trim()) {
                Alpine.store('toast').add('Please enter a space name', 'error');
                return;
            }
            this.loading = true;
            const result = await apiCall('POST', '/api/spaces', { name: this.createName.trim() });
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Space created', 'success');
                this.showCreate = false;
                this.createName = '';
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },

        startRename(space) {
            this.editId = space.id;
            this.editName = space.name;
            this.$nextTick(() => {
                const input = this.$refs['renameInput' + space.id];
                if (input) input.focus();
            });
        },

        cancelRename() {
            this.editId = null;
            this.editName = '';
        },

        async submitRename(spaceId) {
            if (!this.editName.trim()) return;
            this.loading = true;
            const result = await apiCall('POST', `/api/spaces/${spaceId}/rename`, { name: this.editName.trim() });
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Space renamed', 'success');
                this.editId = null;
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },

        confirmDelete(id) {
            this.confirmId = id;
            this.showConfirm = true;
        },

        async doDelete() {
            this.loading = true;
            const result = await apiCall('DELETE', `/api/spaces/${this.confirmId}`);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Space deleted', 'success');
                this.showConfirm = false;
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },

        openTransfer(spaceName) {
            this.transferForm.space_name = spaceName;
            this.transferForm.direction = 'to_space';
            this.transferForm.amount = '';
            this.transferForm.happened_at = todayDate();
            this.transferForm.note = '';
            if (this.users.length && !this.transferForm.created_by_telegram_id) {
                this.transferForm.created_by_telegram_id = this.users[0].telegram_id;
            }
            this.showTransfer = true;
        },

        async submitTransfer() {
            if (!this.transferForm.amount || !this.transferForm.space_name) {
                Alpine.store('toast').add('Please fill amount and space', 'error');
                return;
            }
            this.loading = true;
            const payload = {
                space_name: this.transferForm.space_name,
                direction: this.transferForm.direction,
                amount: parseAmount(this.transferForm.amount),
                created_by_telegram_id: parseInt(this.transferForm.created_by_telegram_id),
                note: this.transferForm.note || '',
            };
            if (this.transferForm.happened_at) {
                payload.happened_at = this.transferForm.happened_at + 'T00:00:00';
            }
            const result = await apiCall('POST', '/api/spaces/transfer', payload);
            this.loading = false;
            if (result) {
                const dir = this.transferForm.direction === 'to_space' ? 'Deposited to' : 'Withdrawn from';
                Alpine.store('toast').add(`${dir} ${this.transferForm.space_name}`, 'success');
                this.showTransfer = false;
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },
    };
}

/* ── Subscriptions page component ──────────────────────────────────── */

function subscriptionsPage(initialData, initialCategories, initialUsers) {
    const currentMonth = todayDate().slice(0, 7);
    initialData = initialData || {};
    initialCategories = initialCategories || [];
    initialUsers = initialUsers || [];
    const blankForm = () => ({
        name: '',
        amount: '',
        category_id: initialCategories.length ? initialCategories[0].id : '',
        created_by_telegram_id: initialUsers.length ? initialUsers[0].telegram_id : '',
        due_day: 1,
        note: '',
    });

    return {
        items: initialData.items || [],
        summary: initialData.summary || {},
        previousMissed: initialData.previous_missed || null,
        period: initialData.period || currentMonth,
        isCurrent: initialData.is_current !== false,
        maxPeriod: currentMonth,
        categories: initialCategories || [],
        users: initialUsers || [],
        showForm: false,
        editId: null,
        form: blankForm(),
        showConfirm: false,
        deleteItem: null,
        loading: false,
        loadingId: null,

        statusLabel(status) {
            return {
                paid: this.isCurrent ? 'Paid this month already' : 'Paid',
                not_paid: 'Not paid yet',
                missed: 'Missed',
                upcoming: 'Upcoming',
                not_started: 'Not started',
            }[status] || status;
        },

        statusClass(status) {
            return `subscription-${String(status).replace('_', '-')}`;
        },

        applyData(data) {
            this.items = data.items || [];
            this.summary = data.summary || {};
            this.previousMissed = data.previous_missed || null;
            this.period = data.period || this.period;
            this.isCurrent = Boolean(data.is_current);
        },

        async loadPeriod() {
            const match = String(this.period).match(/^(\d{4})-(\d{2})$/);
            if (!match) return;
            this.loading = true;
            const data = await apiCall(
                'GET',
                `/api/subscriptions?year=${Number(match[1])}&month=${Number(match[2])}`,
            );
            this.loading = false;
            if (data) this.applyData(data);
        },

        showCurrentMonth() {
            this.period = currentMonth;
            return this.loadPeriod();
        },

        showMissedPreviousMonth() {
            if (!this.previousMissed) return;
            this.period = this.previousMissed.period;
            return this.loadPeriod();
        },

        openCreate() {
            this.editId = null;
            this.form = blankForm();
            this.showForm = true;
        },

        openEdit(item) {
            this.editId = item.id;
            this.form = {
                name: item.name,
                amount: Number(item.amount).toFixed(2),
                category_id: item.category_id,
                created_by_telegram_id: item.created_by_telegram_id,
                due_day: item.due_day,
                note: item.note || '',
            };
            this.showForm = true;
        },

        async submitForm() {
            const amount = parseAmount(this.form.amount);
            const dueDay = Number(this.form.due_day);
            if (!this.form.name.trim() || !Number.isFinite(amount) || amount <= 0
                || !this.form.category_id || !this.form.created_by_telegram_id
                || !Number.isInteger(dueDay) || dueDay < 1 || dueDay > 31) {
                Alpine.store('toast').add('Please fill all required subscription fields', 'error');
                return;
            }

            const payload = {
                name: this.form.name.trim(),
                amount,
                category_id: Number(this.form.category_id),
                created_by_telegram_id: Number(this.form.created_by_telegram_id),
                due_day: dueDay,
                note: this.form.note.trim(),
            };
            this.loading = true;
            const result = await apiCall(
                this.editId ? 'PUT' : 'POST',
                this.editId ? `/api/subscriptions/${this.editId}` : '/api/subscriptions',
                payload,
            );
            this.loading = false;
            if (!result) return;

            Alpine.store('toast').add(
                this.editId ? 'Subscription updated' : 'Subscription created',
                'success',
            );
            this.showForm = false;
            await this.loadPeriod();
        },

        async pay(item) {
            const [year, month] = this.period.split('-').map(Number);
            this.loadingId = item.id;
            const result = await apiCall(
                'POST',
                `/api/subscriptions/${item.id}/pay`,
                { year, month },
            );
            this.loadingId = null;
            if (!result) return;

            if (!announceLimitStatus(result)) {
                Alpine.store('toast').add(`${item.name} paid and added to expenses`, 'success');
            }
            await this.loadPeriod();
        },

        confirmDelete(item) {
            this.deleteItem = item;
            this.showConfirm = true;
        },

        async doDelete() {
            if (!this.deleteItem) return;
            this.loading = true;
            const result = await apiCall('DELETE', `/api/subscriptions/${this.deleteItem.id}`);
            this.loading = false;
            if (!result) return;

            Alpine.store('toast').add('Subscription deleted', 'success');
            this.showConfirm = false;
            this.deleteItem = null;
            await this.loadPeriod();
        },
    };
}

/* -- Investments page component ------------------------------------------ */

function investmentsPage(initialAccounts, initialAssets, initialUsers) {
    return {
        accounts: initialAccounts || [],
        assets: initialAssets || [],
        users: initialUsers || [],
        loading: false,

        showAssetCreate: false,
        showTrade: false,
        showCashEvent: false,
        showPrice: false,
        showDeleteOperation: false,
        deleteOperation: null,
        editTradeId: null,
        editCashEventId: null,

        assetForm: {
            name: '',
            asset_type: 'etf',
            isin: '',
            wkn: '',
            ticker: '',
            currency_code: 'EUR',
            note: '',
        },

        tradeForm: {
            account_id: '',
            asset_id: '',
            side: 'buy',
            quantity: '',
            unit_price: '',
            fees: '',
            taxes: '',
            happened_at: todayDate(),
            note: '',
            created_by_telegram_id: '',
        },

        cashEventForm: {
            account_id: '',
            asset_id: '',
            event_type: 'dividend',
            amount: '',
            happened_at: todayDate(),
            note: '',
            created_by_telegram_id: '',
        },

        priceForm: {
            asset_id: '',
            price: '',
            priced_at: todayDate(),
        },

        init() {
            const defaultAccount = this.accounts[0];
            const defaultUser = this.users[0];
            if (defaultAccount) {
                this.tradeForm.account_id = defaultAccount.id;
                this.cashEventForm.account_id = defaultAccount.id;
            }
            if (defaultUser) {
                this.tradeForm.created_by_telegram_id = defaultUser.telegram_id;
                this.cashEventForm.created_by_telegram_id = defaultUser.telegram_id;
            }
        },

        resetAssetForm() {
            this.assetForm = {
                name: '',
                asset_type: 'etf',
                isin: '',
                wkn: '',
                ticker: '',
                currency_code: 'EUR',
                note: '',
            };
        },

        openTrade(side) {
            this.editTradeId = null;
            this.tradeForm.side = side;
            this.tradeForm.asset_id = '';
            this.tradeForm.quantity = '';
            this.tradeForm.unit_price = '';
            this.tradeForm.fees = '';
            this.tradeForm.taxes = '';
            this.tradeForm.happened_at = todayDate();
            this.tradeForm.note = '';
            if (this.accounts.length && !this.tradeForm.account_id) {
                this.tradeForm.account_id = this.accounts[0].id;
            }
            if (this.users.length && !this.tradeForm.created_by_telegram_id) {
                this.tradeForm.created_by_telegram_id = this.users[0].telegram_id;
            }
            this.showTrade = true;
        },

        openCashEvent(type) {
            this.editCashEventId = null;
            this.cashEventForm.event_type = type || 'dividend';
            this.cashEventForm.asset_id = '';
            this.cashEventForm.amount = '';
            this.cashEventForm.happened_at = todayDate();
            this.cashEventForm.note = '';
            if (this.accounts.length && !this.cashEventForm.account_id) {
                this.cashEventForm.account_id = this.accounts[0].id;
            }
            if (this.users.length && !this.cashEventForm.created_by_telegram_id) {
                this.cashEventForm.created_by_telegram_id = this.users[0].telegram_id;
            }
            this.showCashEvent = true;
        },

        openPriceModal() {
            this.priceForm.asset_id = '';
            this.priceForm.price = '';
            this.priceForm.priced_at = todayDate();
            this.showPrice = true;
        },

        async submitAsset() {
            if (!this.assetForm.name.trim() || !this.assetForm.isin.trim()) {
                Alpine.store('toast').add('Please fill asset name and ISIN', 'error');
                return;
            }
            this.loading = true;
            const result = await apiCall('POST', '/api/investments/assets', {
                name: this.assetForm.name.trim(),
                asset_type: this.assetForm.asset_type,
                isin: this.assetForm.isin.trim(),
                wkn: this.assetForm.wkn.trim(),
                ticker: this.assetForm.ticker.trim(),
                currency_code: (this.assetForm.currency_code || 'EUR').trim(),
                note: this.assetForm.note || '',
            });
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Asset created', 'success');
                this.showAssetCreate = false;
                this.resetAssetForm();
                window.location.reload();
            }
        },

        openEditTrade(op) {
            this.editTradeId = op.id;
            this.tradeForm = {
                account_id: op.account_id,
                asset_id: op.asset_id,
                side: op.type,
                quantity: op.quantity,
                unit_price: op.unit_price,
                fees: op.fees,
                taxes: op.taxes,
                happened_at: op.happened_at ? op.happened_at.slice(0, 10) : todayDate(),
                note: op.note || '',
                created_by_telegram_id: op.created_by_telegram_id,
            };
            this.showTrade = true;
        },

        openEditCashEvent(op) {
            this.editCashEventId = op.id;
            this.cashEventForm = {
                account_id: op.account_id,
                asset_id: op.asset_id || '',
                event_type: op.type,
                amount: op.gross_amount,
                happened_at: op.happened_at ? op.happened_at.slice(0, 10) : todayDate(),
                note: op.note || '',
                created_by_telegram_id: op.created_by_telegram_id,
            };
            this.showCashEvent = true;
        },

        confirmDeleteOperation(op) {
            this.deleteOperation = op;
            this.showDeleteOperation = true;
        },

        async submitTrade() {
            if (!this.tradeForm.asset_id || !this.tradeForm.quantity || !this.tradeForm.unit_price) {
                Alpine.store('toast').add('Please fill asset, quantity and unit price', 'error');
                return;
            }
            this.loading = true;
            const payload = {
                account_id: parseInt(this.tradeForm.account_id),
                asset_id: parseInt(this.tradeForm.asset_id),
                side: this.tradeForm.side,
                quantity: parseAmount(this.tradeForm.quantity),
                unit_price: parseAmount(this.tradeForm.unit_price),
                fees: this.tradeForm.fees ? parseAmount(this.tradeForm.fees) : 0,
                taxes: this.tradeForm.taxes ? parseAmount(this.tradeForm.taxes) : 0,
                created_by_telegram_id: parseInt(this.tradeForm.created_by_telegram_id),
                note: this.tradeForm.note || '',
            };
            if (this.tradeForm.happened_at) {
                payload.happened_at = this.tradeForm.happened_at + 'T00:00:00';
            }
            const method = this.editTradeId ? 'PUT' : 'POST';
            const url = this.editTradeId ? `/api/investments/trades/${this.editTradeId}` : '/api/investments/trades';
            const result = await apiCall(method, url, payload);
            this.loading = false;
            if (result) {
                const action = this.editTradeId ? 'Trade updated' : (this.tradeForm.side === 'buy' ? 'Buy recorded' : 'Sell recorded');
                Alpine.store('toast').add(action, 'success');
                this.showTrade = false;
                this.editTradeId = null;
                window.location.reload();
            }
        },

        async submitCashEvent() {
            if (!this.cashEventForm.amount) {
                Alpine.store('toast').add('Please fill the amount', 'error');
                return;
            }
            this.loading = true;
            const payload = {
                account_id: parseInt(this.cashEventForm.account_id),
                event_type: this.cashEventForm.event_type,
                amount: parseAmount(this.cashEventForm.amount),
                created_by_telegram_id: parseInt(this.cashEventForm.created_by_telegram_id),
                note: this.cashEventForm.note || '',
            };
            if (this.cashEventForm.asset_id) {
                payload.asset_id = parseInt(this.cashEventForm.asset_id);
            }
            if (this.cashEventForm.happened_at) {
                payload.happened_at = this.cashEventForm.happened_at + 'T00:00:00';
            }
            const method = this.editCashEventId ? 'PUT' : 'POST';
            const url = this.editCashEventId ? `/api/investments/cash-events/${this.editCashEventId}` : '/api/investments/cash-events';
            const result = await apiCall(method, url, payload);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add(this.editCashEventId ? 'Cash event updated' : 'Cash event recorded', 'success');
                this.showCashEvent = false;
                this.editCashEventId = null;
                window.location.reload();
            }
        },

        async submitPrice() {
            if (!this.priceForm.asset_id || !this.priceForm.price) {
                Alpine.store('toast').add('Please fill asset and price', 'error');
                return;
            }
            this.loading = true;
            const payload = {
                asset_id: parseInt(this.priceForm.asset_id),
                price: parseAmount(this.priceForm.price),
            };
            if (this.priceForm.priced_at) {
                payload.priced_at = this.priceForm.priced_at + 'T00:00:00';
            }
            const result = await apiCall('POST', '/api/investments/prices', payload);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add('Price updated', 'success');
                this.showPrice = false;
                window.location.reload();
            }
        },

        async doDeleteOperation() {
            if (!this.deleteOperation) return;
            this.loading = true;
            const isTrade = this.deleteOperation.kind === 'trade';
            const url = isTrade
                ? `/api/investments/trades/${this.deleteOperation.id}`
                : `/api/investments/cash-events/${this.deleteOperation.id}`;
            const result = await apiCall('DELETE', url);
            this.loading = false;
            if (result) {
                Alpine.store('toast').add(isTrade ? 'Trade deleted' : 'Cash event deleted', 'success');
                this.showDeleteOperation = false;
                this.deleteOperation = null;
                window.location.reload();
            }
        },
    };
}
