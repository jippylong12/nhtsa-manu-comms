import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ecommerce, firstTime, productOf, scrubUrl, shouldLoad, transactionId } from '../src/analytics/events.ts';

test('a checkout maps to the product that was bought', () => {
    assert.equal(productOf('unlock'), 'unlock');
    assert.equal(productOf('unlock', 'all_access'), 'unlock');
    assert.equal(productOf('subscription'), 'monthly');
    assert.equal(productOf('subscription', null), 'monthly');
    assert.equal(productOf('subscription', 'all_access'), 'all_access');
});

test('ecommerce values match the prices sold', () => {
    assert.deepEqual(ecommerce('unlock').value, 3);
    assert.deepEqual(ecommerce('monthly').value, 5);
    assert.deepEqual(ecommerce('all_access').value, 20);
    const e = ecommerce('all_access');
    assert.equal(e.currency, 'USD');
    assert.deepEqual(e.items, [{ item_id: 'all_access', item_name: 'All access', price: 20, quantity: 1 }]);
});

test('the purchase id is a short tail, never the whole Stripe session id', () => {
    const id = transactionId('cs_live_a1B2c3D4e5F6g7H8i9J0k1L2m3N4');
    assert.equal(id, 'J0k1L2m3N4'.padStart(12, 'i9').slice(-12));
    assert.equal(id?.length, 12);
    assert.equal(transactionId(null), undefined);
});

test('Tag Manager stays off locally, on unsubscribe links and in the admin console', () => {
    assert.equal(shouldLoad('localhost', '/'), false);
    assert.equal(shouldLoad('127.0.0.1', '/app'), false);
    assert.equal(shouldLoad('app.localhost', '/'), false);
    assert.equal(shouldLoad('notjustmycar.com', '/unsubscribe/abc123'), false);
    assert.equal(shouldLoad('notjustmycar.com', '/admin'), false);
    assert.equal(shouldLoad('notjustmycar.com', '/'), true);
    assert.equal(shouldLoad('notjustmycar.com', '/app/account'), true);
});

test('what a person typed and identifiers never leave with the page address', () => {
    assert.equal(
        scrubUrl('https://notjustmycar.com/app/vehicles/7?search=brake%20grinding%20noise&severity=serious'),
        'https://notjustmycar.com/app/vehicles/7?severity=serious'
    );
    assert.equal(
        scrubUrl('https://notjustmycar.com/app/billing/success?session_id=cs_live_a1B2c3'),
        'https://notjustmycar.com/app/billing/success'
    );
    assert.equal(scrubUrl('https://notjustmycar.com/app/vehicles/7?b=11027376#x'), 'https://notjustmycar.com/app/vehicles/7');
    assert.equal(scrubUrl('https://notjustmycar.com/?utm_term=secret&unknown=1'), 'https://notjustmycar.com/');
});

test('the car a visitor looked up stays in the page address', () => {
    assert.equal(
        scrubUrl('https://notjustmycar.com/?year=2023&make=TOYOTA&model=RAV4'),
        'https://notjustmycar.com/?year=2023&make=TOYOTA&model=RAV4'
    );
    assert.equal(scrubUrl('not a url'), '');
});

test('typing in the search box does not change the scrubbed address', () => {
    const a = scrubUrl('https://notjustmycar.com/app/vehicles/7?search=b');
    const b = scrubUrl('https://notjustmycar.com/app/vehicles/7?search=brake');
    assert.equal(a, b);
});

test('a purchase is counted once even when storage is blocked', () => {
    const blocked = {
        getItem(): string | null {
            throw new Error('blocked');
        },
        setItem(): void {
            throw new Error('blocked');
        },
    };
    const seen = new Set<string>();
    assert.equal(firstTime('purchaseTracked:abc', seen, blocked), true);
    assert.equal(firstTime('purchaseTracked:abc', seen, blocked), false);
    assert.equal(firstTime('purchaseTracked:abc', seen, null), false);
    assert.equal(firstTime('purchaseTracked:other', seen, blocked), true);
});

test('a refresh does not count a purchase that storage remembers', () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    assert.equal(firstTime('purchaseTracked:abc', new Set(), storage), true);
    assert.equal(firstTime('purchaseTracked:abc', new Set(), storage), false); // new page load, same storage
});
