/* ── Utilities ───────────────────────────────────────────────────────── */

function todayDate() {
    return new Date().toISOString().slice(0, 10);
}

function parseAmount(value) {
    if (typeof value === 'number') return value;
    return parseFloat(String(value).replace(',', '.'));
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
    const data = await resp.json();
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
        invalidate() {
            this.categories = null;
            this.users = null;
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
            if (this.users.length && !this.form.created_by_telegram_id) {
                this.form.created_by_telegram_id = this.users[0].telegram_id;
            }
            this.showModal = true;
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
                this.showModal = false;
                Alpine.store('app').invalidate();
                // Reload page to reflect new data
                setTimeout(() => window.location.reload(), 500);
            }
        },
    };
}

/* ── Transactions page component ────────────────────────────────────── */

function transactionsPage(initialData, initialCategories, initialUsers) {
    return {
        transactions: initialData.items || [],
        total: initialData.total || 0,
        page: initialData.page || 1,
        perPage: initialData.per_page || 50,
        categories: initialCategories || [],
        users: initialUsers || [],

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
        editForm: { id: null, amount: '', category_name: '', happened_at: '', note: '' },

        // Confirm delete
        showConfirm: false,
        confirmId: null,

        loading: false,

        init() {
            if (this.users.length && !this.addForm.created_by_telegram_id) {
                this.addForm.created_by_telegram_id = this.users[0].telegram_id;
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
                this.showAdd = false;
                Alpine.store('app').invalidate();
                window.location.reload();
            }
        },

        openEdit(tx) {
            this.editForm = {
                id: tx.id,
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
