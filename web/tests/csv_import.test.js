const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadAppScript() {
    const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
    const context = {
        clearTimeout,
        console,
        Date,
        document: { addEventListener() {} },
        fetch: async () => { throw new Error('Unexpected fetch'); },
        setTimeout,
        window: {},
    };
    vm.createContext(context);
    vm.runInContext(source, context);
    return context;
}

test('imports expenses from the new vendor CSV without using vendor categories', async () => {
    const app = loadAppScript();
    const page = app.transactionsPage(
        { items: [] },
        [{ id: 1, name: 'Merchant', type: 'expense' }],
        [{ telegram_id: 123, name: 'Test User' }],
    );
    page.$refs = { csvInput: { value: 'selected.csv' } };

    const csv = [
        'Started Date,Description,Category,Amount,Balance,Tax withheld,Other taxes,Fees',
        '26-Jul-26,Karls,Merchant,-€6.95,€562.41,€0.00,€0.00,€0.00',
        '27-Jul-26,"Coffee, Inc.",Merchant,"-€1,234.56",€557.79,€0.00,€0.00,€0.00',
        '28-Jul-26,Salary,Others,€100.00,€657.79,€0.00,€0.00,€0.00',
    ].join('\r\n');

    await page.handleCsvFile({
        name: 'consolidated_statement.csv',
        text: async () => csv,
    });

    assert.equal(page.csvErrors.length, 0);
    assert.equal(page.csvSkippedIncomeCount, 1);
    assert.equal(page.csvSkippedInvalidCount, 0);
    assert.equal(page.csvRows.length, 2);
    assert.equal(page.csvRows[0].happened_at, '2026-07-26T00:00');
    assert.equal(page.csvRows[0].amount, '6.95');
    assert.equal(page.csvRows[0].note, 'Karls');
    assert.equal(page.csvRows[0].category_name, '');
    assert.equal(Object.hasOwn(page.csvRows[0], 'csvCategory'), false);
    assert.equal(page.csvRows[1].amount, '1234.56');
    assert.equal(page.csvRows[1].note, 'Coffee, Inc.');
    assert.equal(page.$refs.csvInput.value, '');
});

test('rejects invalid statement dates instead of relying on browser date parsing', () => {
    const app = loadAppScript();

    assert.equal(app.parseBankDate('31-Feb-26'), '');
    assert.equal(app.parseBankDate('not a date'), '');
});

test('keeps supporting plain amounts and ISO dates from existing CSV files', () => {
    const app = loadAppScript();

    assert.equal(app.parseBankAmount('-6.95'), -6.95);
    assert.equal(app.parseBankDate('2026-07-26 14:05:00'), '2026-07-26T14:05');
});

test('applies the configured category limit thresholds to a projected expense', () => {
    const app = loadAppScript();
    const limits = [{
        category: 'Cafe',
        limit: 100,
        spent: 50,
        period: '2026-08',
    }];

    const preview = app.projectedLimitFor(limits, {
        type: 'expense',
        amount: '20',
        category_name: 'Cafe',
        happened_at: '2026-08-10',
    });

    assert.equal(preview.percentage, 70);
    assert.equal(preview.status, 'caution');
    assert.equal(preview.title, '70% threshold');
    assert.equal(app.limitStatusForPercentage(49.9), 'safe');
    assert.equal(app.limitStatusForPercentage(50), 'warning');
    assert.equal(app.limitStatusForPercentage(100), 'exceeded');
});

test('does not show a stale limit preview for a transaction in another month', () => {
    const app = loadAppScript();
    const preview = app.projectedLimitFor([{
        category: 'Cafe',
        limit: 100,
        spent: 50,
        period: '2026-08',
    }], {
        type: 'expense',
        amount: '20',
        category_name: 'Cafe',
        happened_at: '2026-07-31',
    });

    assert.equal(preview, null);
});

test('income sorter builds allocations and keeps the unallocated income in cash', () => {
    const app = loadAppScript();
    const page = app.transactionsPage(
        { items: [] },
        [],
        [{ telegram_id: 123, name: 'Test User' }],
        [],
        [
            { id: 1, name: 'Savings' },
            { id: 2, name: 'Vacation' },
            { id: 3, name: 'Unused' },
        ],
    );
    page.incomeSortTransaction = {
        id: 10,
        amount: 1000,
        created_by_telegram_id: 123,
    };
    page.incomeSortAllocations = [
        { space_id: 1, space_name: 'Savings', amount: '300.10' },
        { space_id: 2, space_name: 'Vacation', amount: '200,20' },
        { space_id: 3, space_name: 'Unused', amount: '' },
    ];

    assert.deepEqual(
        JSON.parse(JSON.stringify(page.incomeSortPayload())),
        [
            { space_id: 1, amount: 300.1 },
            { space_id: 2, amount: 200.2 },
        ],
    );
    assert.equal(page.incomeSortAllocated(), 500.3);
    assert.equal(page.incomeSortRemaining(), 499.7);
    assert.equal(page.incomeSortValidationMessage(), '');
    assert.equal(page.incomeSortUserName(), 'Test User');

    page.incomeSortAllocations[0].amount = '1000.01';
    page.incomeSortAllocations[1].amount = '';
    assert.equal(page.incomeSortValidationMessage(), 'Allocated amount cannot exceed this income.');
});
