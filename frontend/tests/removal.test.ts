import assert from 'node:assert/strict';
import { test } from 'node:test';

import { removalConsequence } from '../src/features/my-vehicles/removal.ts';

const monthly = (used: number) => ({ plan: 'monthly' as const, limit: 1, used, resetsOn: '2026-12-01' });
const allAccess = (used: number) => ({ plan: 'all_access' as const, limit: 5, used, resetsOn: '2026-12-01' });

test('an unlocked car is always safe to remove, even for a subscriber out of removals', () => {
    const c = removalConsequence({ unlocked: true, sample: false, allowance: monthly(1) });
    assert.equal(c.tone, 'safe');
    assert.match(c.body, /no cost/);
});

test('a Monthly subscriber is warned that this is the one removal of the month', () => {
    const c = removalConsequence({ unlocked: false, sample: false, allowance: monthly(0) });
    assert.equal(c.tone, 'warn');
    assert.match(c.title, /one removal this month/);
    assert.match(c.body, /December 1/);
});

test('a subscriber with no removals left is blocked and told the date', () => {
    const c = removalConsequence({ unlocked: false, sample: false, allowance: monthly(1) });
    assert.equal(c.tone, 'blocked');
    assert.match(c.title, /December 1/);
});

test('All access counts down the removals left', () => {
    const c = removalConsequence({ unlocked: false, sample: false, allowance: allAccess(2) });
    assert.equal(c.tone, 'warn');
    assert.match(c.title, /1 of your 3 removals left/);
    assert.equal(removalConsequence({ unlocked: false, sample: false, allowance: allAccess(5) }).tone, 'blocked');
});

test('a free sample warns that removing gives no new sample', () => {
    const c = removalConsequence({ unlocked: false, sample: true, allowance: null });
    assert.equal(c.tone, 'warn');
    assert.match(c.title, /does not give you a new free sample/);
});
