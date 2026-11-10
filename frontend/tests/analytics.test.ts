import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ecommerce, productOf, shouldLoad, transactionId } from '../src/analytics/events.ts';

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
