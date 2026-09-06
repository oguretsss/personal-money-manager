const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadCard(category = 'Cafe', period = '2026-07') {
    const context = {
        document: { addEventListener() {} },
        URLSearchParams,
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8'), context);
    return { context, card: context.categoryLimitCard(category, period) };
}

test('loads only on expansion, scopes expenses to the card month, and caches on collapse', async () => {
    const { context, card } = loadCard('Cafe & "Biergarten" / O\'Brien');
    const calls = [];
    context.apiCall = async (method, url) => {
        calls.push({ method, params: new URL(url, 'http://localhost').searchParams });
        return { items: [{ id: 3, amount: 30 }, { id: 2, amount: 20 }, { id: 1, amount: 27 }], total: 3, page: 1 };
    };
    assert.equal(card.expanded, false);
    assert.equal(calls.length, 0);
    await card.toggle();
    assert.equal(card.expanded, true);
    assert.equal(card.transactions.reduce((sum, tx) => sum + tx.amount, 0), 77);
    assert.equal(calls[0].method, 'GET');
    assert.deepEqual(Object.fromEntries(calls[0].params), {
        type: 'expense', category: 'Cafe & "Biergarten" / O\'Brien',
        start: '2026-07-01T00:00:00', end: '2026-08-01T00:00:00', page: '1', per_page: '50',
    });
    await card.toggle();
    assert.equal(card.expanded, false);
    await card.toggle();
    assert.equal(calls.length, 1);
});

test('uses exclusive month boundaries across December and leap February', async () => {
    for (const [period, end] of [['2026-12', '2027-01-01T00:00:00'], ['2028-02', '2028-03-01T00:00:00']]) {
        const { context, card } = loadCard('Cafe', period);
        context.apiCall = async (_method, url) => {
            assert.equal(new URL(url, 'http://localhost').searchParams.get('end'), end);
            return { items: [], total: 0, page: 1 };
        };
        await card.toggle();
        assert.equal(card.loaded, true);
        assert.equal(card.total, 0);
        assert.equal(card.error, false);
    }
});

test('appends additional pages and retries the same page after a failure', async () => {
    const { context, card } = loadCard();
    const pages = [];
    context.apiCall = async (_method, url) => {
        const page = Number(new URL(url, 'http://localhost').searchParams.get('page'));
        pages.push(page);
        if (pages.length === 2) throw new Error('Network offline');
        return { items: [{ id: page }], total: 2, page };
    };
    await card.toggle();
    await card.loadTransactions();
    assert.equal(card.error, true);
    assert.equal(card.loading, false);
    assert.equal(card.transactions.length, 1);
    assert.equal(card.page, 1);
    await card.loadTransactions();
    assert.deepEqual(pages, [1, 2, 2]);
    assert.equal(card.error, false);
    assert.equal(card.transactions.length, card.total);
    assert.equal(card.transactions[1].id, 2);
});

test('allows retry after an HTTP error without caching a failed initial load', async () => {
    const { context, card } = loadCard();
    context.apiCall = async () => null;
    await card.toggle();
    assert.equal(card.loaded, false);
    assert.equal(card.error, true);
    assert.equal(card.loading, false);
    context.apiCall = async () => ({ items: [], total: 0, page: 1 });
    await card.loadTransactions();
    assert.equal(card.loaded, true);
    assert.equal(card.error, false);
});

test('rapid toggles do not duplicate requests and cards expand independently', async () => {
    const { context, card } = loadCard();
    const otherCard = context.categoryLimitCard('Groceries', '2026-07');
    let finish;
    let calls = 0;
    context.apiCall = () => {
        calls++;
        return new Promise(resolve => { finish = resolve; });
    };
    const pending = card.toggle();
    await card.toggle();
    await card.toggle();
    assert.equal(calls, 1);
    assert.equal(otherCard.expanded, false);
    finish({ items: [{ id: 1 }], total: 1, page: 1 });
    await pending;
    assert.equal(card.transactions.length, 1);
    assert.equal(card.loading, false);
});
