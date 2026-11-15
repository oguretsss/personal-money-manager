const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function tx(id, source = 'transaction') {
    return { id, source, type: 'expense', amount: id, category: 'Cafe', happened_at: '2026-11-03T00:00:00', note: '' };
}

function loadPage(data = {}, filters = {}) {
    const observers = [];
    const scrolls = [];
    const stores = {
        toast: { add() {} },
        app: { invalidate() {}, async fetchCategories() { return []; }, async fetchLimits() { return []; } },
    };
    const context = {
        document: { addEventListener() {} },
        URLSearchParams,
        CustomEvent,
        Alpine: { store: name => stores[name] },
        window: {
            scrollY: 500, innerHeight: 800,
            scrollBy: (_x, y) => scrolls.push(y), scrollTo() {},
            location: { reload() { throw new Error('Unexpected page reload'); } },
        },
        IntersectionObserver: class {
            constructor(callback, options) { this.callback = callback; this.options = options; this.targets = []; observers.push(this); }
            observe(target) { this.targets.push(target); }
            disconnect() { this.targets = []; }
        },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8'), context);
    const page = context.transactionsPage(
        { items: [tx(5), tx(4)], total: 5, page: 1, per_page: 2, ...data },
        [{ name: 'Cafe', type: 'expense' }], [{ telegram_id: 123 }], [], [], filters,
    );
    page.$refs = { loadMore: {}, transactionRows: { querySelectorAll: () => [] } };
    page.$nextTick = async callback => { if (callback) callback(); };
    return { page, context, observers, scrolls };
}

test('appends pages with every active filter, preserves investment IDs, and stops at the end', async () => {
    const { page, context } = loadPage({}, {
        category: 'Cafe & "Biergarten" / O\'Brien', type: 'expense', user_id: 123,
        start: '2026-11-01', end: '2026-11-03',
    });
    const calls = [];
    context.apiCall = async (method, url) => {
        calls.push({ method, params: Object.fromEntries(new URL(url, 'http://localhost').searchParams) });
        return { items: [tx(4), tx(4, 'investment_buy'), tx(3)], total: 4, page: 2 };
    };
    await page.loadMore();
    assert.deepEqual(calls, [{ method: 'GET', params: {
        page: '2', per_page: '2', include_investments: 'true', type: 'expense',
        category: 'Cafe & "Biergarten" / O\'Brien', user_id: '123', start: '2026-11-01', end: '2026-11-03',
    } }]);
    assert.deepEqual(Array.from(page.transactions, row => page.transactionKey(row)), [
        'transaction:5', 'transaction:4', 'investment_buy:4', 'transaction:3',
    ]);
    assert.equal(page.hasMore, false);
    await page.loadMore();
    assert.equal(calls.length, 1);
});

test('only one append runs at a time and failed requests retry the same page', async () => {
    const { page, context } = loadPage();
    let finish;
    const pages = [];
    context.apiCall = (_method, url) => {
        pages.push(new URL(url, 'http://localhost').searchParams.get('page'));
        return new Promise(resolve => { finish = resolve; });
    };
    const pending = page.loadMore();
    await page.loadMore();
    assert.equal(pages.length, 1);
    finish(null);
    await pending;
    assert.equal(page.loadingMore, false);
    assert.ok(page.listError);
    assert.equal(page.page, 1);
    context.apiCall = async (_method, url) => {
        pages.push(new URL(url, 'http://localhost').searchParams.get('page'));
        return { items: [tx(3), tx(2)], total: 5, page: 2 };
    };
    await page.retryList();
    assert.equal(page.listError, '');
    assert.deepEqual(pages, ['2', '2']);
    assert.equal(page.transactions.length, 4);
});

test('observer preloads nearby rows, rearms after append, and pauses on network errors', async () => {
    const { page, context, observers } = loadPage();
    page.init();
    assert.equal(observers[0].options.rootMargin, '400px 0px');
    assert.equal(observers[0].targets[0], page.$refs.loadMore);
    context.apiCall = async () => { throw new Error('offline'); };
    await page.loadMore();
    assert.ok(page.listError);
    assert.equal(observers[0].targets.length, 0);
    context.apiCall = async () => ({ items: [tx(3), tx(2)], total: 5, page: 2 });
    await page.retryList();
    assert.equal(observers[0].targets[0], page.$refs.loadMore);
    page.destroy();
    assert.equal(observers[0].targets.length, 0);
});

test('an empty result ends loading and an initial failure can retry page one', async () => {
    const { page, context } = loadPage({ items: [], total: 0, load_error: true });
    const pages = [];
    context.apiCall = async (_method, url) => {
        pages.push(new URL(url, 'http://localhost').searchParams.get('page'));
        return { items: [tx(1)], total: 1, page: 1 };
    };
    await page.retryList();
    assert.deepEqual(pages, ['1']);
    assert.equal(page.listError, '');
    assert.equal(page.transactions.length, 1);
    assert.equal(page.hasMore, false);
    page.total = 20;
    context.apiCall = async () => ({ items: [], total: 20, page: 2 });
    await page.loadMore();
    assert.equal(page.hasMore, false);
});

test('deletion refreshes all loaded pages and the next append has no missing rows', async () => {
    const { page, context } = loadPage({ items: [tx(6), tx(5), tx(4), tx(3)], total: 6, page: 2 });
    const ledger = [tx(6), tx(5), tx(4), tx(3), tx(2), tx(1)];
    const pages = [];
    context.apiCall = async (method, url) => {
        if (method === 'DELETE') {
            ledger.splice(ledger.findIndex(item => item.id === 4), 1);
            return { ok: true };
        }
        const number = Number(new URL(url, 'http://localhost').searchParams.get('page'));
        pages.push(number);
        return {
            items: ledger.slice((number - 1) * 2, number * 2), total: ledger.length, page: number,
            totals: { income: 0, expense: 17, net: -17 },
        };
    };
    page.confirmDelete(4);
    await page.doDelete();
    assert.deepEqual(pages, [1, 2]);
    assert.deepEqual(Array.from(page.transactions, row => row.id), [6, 5, 3, 2]);
    assert.equal(page.total, 5);
    assert.equal(page.totals.expense, 17);
    assert.equal(page.showConfirm, false);
    await page.loadMore();
    assert.deepEqual(Array.from(page.transactions, row => row.id), [6, 5, 3, 2, 1]);
    assert.equal(page.hasMore, false);
});

test('refresh discards an append that was in flight before a mutation', async () => {
    const { page, context } = loadPage();
    let finish;
    context.apiCall = () => new Promise(resolve => { finish = resolve; });
    const pending = page.loadMore();
    context.apiCall = async () => ({ items: [tx(9), tx(8)], total: 2, page: 1 });
    await page.refreshTransactions();
    finish({ items: [tx(3), tx(2)], total: 5, page: 2 });
    await pending;
    assert.deepEqual(Array.from(page.transactions, row => row.id), [9, 8]);
    assert.equal(page.page, 1);
    assert.equal(page.total, 2);
    assert.equal(page.loadingMore, false);
});

test('failed refresh keeps the loaded rows and retries the prefix before allowing append', async () => {
    const { page, context } = loadPage({ items: [tx(6), tx(5), tx(4), tx(3)], total: 6, page: 2 });
    const pages = [];
    context.apiCall = async (_method, url) => {
        const number = Number(new URL(url, 'http://localhost').searchParams.get('page'));
        pages.push(number);
        if (number === 2) throw new Error('offline');
        return { items: [tx(9), tx(8)], total: 5, page: 1 };
    };
    await page.refreshTransactions();
    assert.deepEqual(Array.from(page.transactions, row => row.id), [6, 5, 4, 3]);
    assert.ok(page.listError);
    assert.equal(page.needsRefresh, true);
    await page.loadMore();
    assert.deepEqual(pages, [1, 2]);
    context.apiCall = async (_method, url) => {
        const number = Number(new URL(url, 'http://localhost').searchParams.get('page'));
        pages.push(number);
        return { items: number === 1 ? [tx(9), tx(8)] : [tx(7), tx(6)], total: 5, page: number };
    };
    await page.retryList();
    assert.deepEqual(pages, [1, 2, 1, 2]);
    assert.equal(page.needsRefresh, false);
    assert.equal(page.listError, '');
});

test('refresh keeps a visible surviving row at the same vertical position', async () => {
    const { page, context, scrolls } = loadPage();
    let top = 120;
    page.$refs.transactionRows.querySelectorAll = () => [{
        dataset: { transactionKey: 'transaction:4' },
        getBoundingClientRect: () => ({ top, bottom: top + 60 }),
    }];
    page.$nextTick = async () => { top = 180; };
    context.apiCall = async () => ({ items: [tx(7), tx(4)], total: 2, page: 1 });
    await page.refreshTransactions();
    assert.deepEqual(scrolls, [60]);
});

const duplicateWarning = {
    count: 1,
    happened_at: '2026-11-10T00:00:00',
    matches: [{ id: 1, amount: 10, happened_at: '2026-11-10T12:00:00', category: 'Groceries', note: 'REWE' }],
};

test('expense forms wait for confirmation, preserve the date and refresh only after saving', async () => {
    for (const quickExpense of [false, true]) {
        const { page, context } = loadPage();
        const component = quickExpense ? context.fabExpense() : page;
        const form = quickExpense ? component.form : component.addForm;
        const submit = () => quickExpense ? component.submit() : component.submitAdd();
        const isOpen = () => quickExpense ? component.showModal : component.showAdd;
        const calls = [];
        let refreshes = 0;
        context.window.dispatchEvent = () => { refreshes++; return false; };
        page.transactionsChanged = async () => { refreshes++; };
        if (quickExpense) component.showModal = true;
        else page.openAdd();
        Object.assign(form, { amount: '10,00', category_name: 'Cafe', created_by_telegram_id: 123, happened_at: '' });
        context.apiCall = async (_method, _url, payload) => {
            calls.push(payload);
            return payload.check_duplicates ? { ok: false, possible_duplicates: duplicateWarning } : { ok: true, id: 2 };
        };

        await submit();
        assert.equal(component.duplicateWarning, duplicateWarning);
        assert.equal(refreshes, 0);
        assert.equal(isOpen(), true);
        assert.match(context.duplicateWarningDetails(duplicateWarning), /€10\.00, 10\.11\.2026 · Groceries · REWE/);
        await submit();
        assert.deepEqual(calls.map(payload => payload.check_duplicates), [true, false]);
        assert.equal(calls[1].happened_at, duplicateWarning.happened_at);
        assert.equal(refreshes, 1);
        assert.equal(isOpen(), false);
    }
});

function csvRow(id, amount, selected = true) {
    return { id, amount, selected, status: 'pending', category_name: 'Cafe', happened_at: '2026-11-10T00:00' };
}

test('CSV review shows only potential duplicates and imports exactly the checked rows', async () => {
    const { page, context } = loadPage();
    page.openCsvImport();
    page.csvRows = [csvRow(1, '10'), csvRow(2, '20'), csvRow(3, '10'), csvRow(4, '30', false)];
    const calls = [];
    let refreshes = 0;
    page.transactionsChanged = async () => { refreshes++; };
    context.apiCall = async (_method, _url, payload) => {
        calls.push([payload.amount, payload.check_duplicates]);
        return payload.amount === 10 && payload.check_duplicates
            ? { ok: false, possible_duplicates: duplicateWarning }
            : { ok: true };
    };

    await page.submitCsvImport();
    assert.equal(page.showCsvImport, true);
    assert.equal(page.csvStep, 'review');
    assert.equal(page.csvResult.imported, 1);
    assert.equal(page.csvResult.total, 3);
    assert.deepEqual(Array.from(page.csvDuplicates, row => row.id), [1, 3]);
    assert.equal(page.csvSelectedDuplicates.length, 0);
    assert.equal(page.csvRows[0].duplicateWarning, duplicateWarning);
    assert.equal(refreshes, 1);

    page.csvRows[0].allowDuplicate = true;
    assert.equal(page.csvDuplicateImportLabel, 'Import 1 transaction');
    await page.importSelectedCsvDuplicates();
    assert.deepEqual(calls, [[10, true], [20, true], [10, true], [10, false]]);
    assert.equal(page.csvStep, 'complete');
    assert.equal(page.csvResult.imported, 2);
    assert.equal(page.csvResult.skipped, 1);
    assert.equal(page.csvResult.total, 3);
    assert.equal(refreshes, 2);
    page.closeCsvImport();
    page.openCsvImport();
    assert.equal(page.csvStep, 'prepare');
    assert.equal(page.csvRows.length, 0);
});

test('CSV duplicate review can skip every match without issuing another write', async () => {
    const { page, context } = loadPage();
    page.openCsvImport();
    page.csvRows = [csvRow(1, '10'), csvRow(2, '10')];
    let calls = 0;
    context.apiCall = async () => { calls++; return { possible_duplicates: duplicateWarning }; };

    await page.submitCsvImport();
    assert.equal(page.csvStep, 'review');
    assert.equal(page.csvResult.imported, 0);
    await page.importSelectedCsvDuplicates();
    page.csvRows[1].allowDuplicate = true;
    page.finishCsvDuplicateReview();
    assert.equal(calls, 2);
    assert.equal(page.csvStep, 'complete');
    assert.equal(page.csvResult.skipped, 2);
});

test('CSV retries only failed duplicate confirmations and preserves ordinary failures for retry', async () => {
    const { page, context } = loadPage();
    page.openCsvImport();
    page.csvRows = [csvRow(1, '10'), csvRow(2, '11'), csvRow(3, '20'), csvRow(4, '30')];
    const calls = [];
    page.transactionsChanged = async () => {};
    context.apiCall = async (_method, _url, payload) => {
        calls.push([payload.amount, payload.check_duplicates]);
        if (payload.check_duplicates && payload.amount < 20) return { possible_duplicates: duplicateWarning };
        if (payload.amount === 20 && calls.length === 3) return null;
        if (payload.amount === 11 && calls.length === 6) return null;
        return { ok: true };
    };

    await page.submitCsvImport();
    assert.equal(page.csvStep, 'review');
    assert.equal(page.csvResult.failed, 1);
    page.csvDuplicates.forEach(row => { row.allowDuplicate = true; });
    assert.equal(page.csvDuplicateImportLabel, 'Import 2 transactions');
    await page.importSelectedCsvDuplicates();
    assert.equal(page.csvStep, 'review');
    assert.equal(page.csvResult.imported, 2);
    assert.deepEqual(Array.from(page.csvSelectedDuplicates, row => row.id), [2]);
    assert.equal(page.csvSelectedDuplicates[0].reviewFailed, true);

    await page.importSelectedCsvDuplicates();
    assert.equal(page.csvStep, 'complete');
    assert.equal(page.csvResult.imported, 3);
    page.retryCsvFailures();
    assert.equal(page.csvStep, 'prepare');
    assert.deepEqual(Array.from(page.csvSelectedRows, row => row.id), [3]);
    await page.submitCsvImport();
    assert.equal(page.csvStep, 'complete');
    assert.equal(page.csvResult.imported, 4);
    assert.equal(page.csvResult.failed, 0);
    assert.deepEqual(calls, [[10, true], [11, true], [20, true], [30, true], [10, false], [11, false], [11, false], [20, true]]);
});

test('add, edit, sort and undo sort update the list without reloading the page', async () => {
    for (const action of ['submitAdd', 'submitEdit', 'submitIncomeSort', 'doUndoIncomeSort']) {
        const { page, context } = loadPage();
        let refreshed = 0;
        page.transactionsChanged = async () => { refreshed++; };
        context.apiCall = async () => ({ ok: true, allocated_amount: 5 });
        page.addForm.amount = 5;
        page.addForm.category_name = 'Cafe';
        page.editForm.id = 4;
        page.incomeSortTransaction = { id: 5, amount: 10 };
        page.incomeSortAllocations = [{ space_id: 1, amount: 5 }];
        page.undoSortId = 5;
        await page[action]();
        assert.equal(refreshed, 1, action);
    }
});

test('partial CSV success refreshes the list while leaving failed rows available for retry', async () => {
    const { page, context } = loadPage();
    let refreshed = 0;
    page.transactionsChanged = async () => { refreshed++; };
    page.csvImportUserId = 123;
    page.showCsvImport = true;
    page.csvRows = [csvRow(1, '5'), csvRow(2, '5')];
    let calls = 0;
    context.apiCall = async () => (++calls === 1 ? { ok: true } : null);
    await page.submitCsvImport();
    assert.equal(refreshed, 1);
    assert.equal(page.showCsvImport, true);
    assert.equal(page.csvStep, 'prepare');
    assert.equal(page.csvResult.imported, 1);
    assert.deepEqual(Array.from(page.csvSelectedRows, row => row.id), [2]);
});

test('quick expense dispatches a refresh event and does not reload when Transactions handles it', async () => {
    const { context } = loadPage();
    const events = [];
    context.window.dispatchEvent = event => { events.push(event); return false; };
    context.apiCall = async () => ({ ok: true });
    const fab = context.fabExpense();
    fab.form.amount = 5;
    fab.form.category_name = 'Cafe';
    await fab.submit();
    assert.equal(events[0].type, 'transactions-changed');
    assert.equal(events[0].cancelable, true);
    assert.equal(fab.showModal, false);
});

test('a metadata network failure does not erase category choices or reject a successful list refresh', async () => {
    const { page, context } = loadPage();
    const categories = page.categories;
    context.apiCall = async () => ({ items: [tx(5)], total: 1, page: 1 });
    context.Alpine.store('app').fetchCategories = async () => { throw new Error('offline'); };
    await page.transactionsChanged();
    assert.equal(page.categories, categories);
    assert.equal(page.transactions.length, 1);
    assert.equal(page.listError, '');
});
