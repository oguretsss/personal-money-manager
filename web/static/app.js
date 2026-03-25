/* ── Utilities ───────────────────────────────────────────────────────── */

function todayDate() {
    return new Date().toISOString().slice(0, 10);
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
                amount: parseFloat(this.form.amount),
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
                amount: parseFloat(this.addForm.amount),
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
                amount: parseFloat(this.editForm.amount),
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

function spacesPage(initialSpaces) {
    return {
        spaces: initialSpaces || [],
        editId: null,
        editName: '',
        showConfirm: false,
        confirmId: null,
        loading: false,

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
    };
}
