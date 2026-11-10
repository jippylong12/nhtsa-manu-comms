/* Run: npm test  (Node 24 strips the types; no test framework needed.) */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { QueryClient } from '@tanstack/query-core';

import { hasData, isUnnarrowed } from '../src/features/my-vehicles/hooks/markReadCache.ts';

const key = (params: Record<string, unknown>) => ['me', 'vehicles', 6, 'bulletins', params] as const;

test('a list with a search or a filter counts as narrowed', () => {
    assert.equal(isUnnarrowed(key({ page: 1, perPage: 50 })), true);
    assert.equal(isUnnarrowed(key({ severity: 'serious', unreadOnly: true })), true);
    assert.equal(isUnnarrowed(key({ search: 'brake' })), false);
    assert.equal(isUnnarrowed(key({ commType: 'TSB' })), false);
    assert.equal(isUnnarrowed(key({ systems: ['Brakes'] })), false);
    assert.equal(isUnnarrowed(key({ dateFrom: '2026-01-01' })), false);
    assert.equal(isUnnarrowed(key({ dateTo: '2026-06-30' })), false);
});

test('a one-id fetch is not a narrowing: its facets cover the whole car', () => {
    assert.equal(isUnnarrowed(key({ nhtsaId: '11034014', perPage: 1 })), true);
});

test('cancelling with hasData leaves a first fetch running, so the new list still arrives', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const loaded = key({ page: 1 });
    const loading = key({ severity: 'serious', page: 1 });
    client.setQueryData(loaded, { items: ['old'] });

    let release: (v: { items: string[] }) => void = () => {};
    const fetching = client.fetchQuery({
        queryKey: loading,
        queryFn: () => new Promise<{ items: string[] }>((resolve) => (release = resolve)),
    });

    await client.cancelQueries({ queryKey: ['me', 'vehicles', 6, 'bulletins'], predicate: hasData });
    release({ items: ['new'] });

    assert.deepEqual(await fetching, { items: ['new'] });
    assert.equal(client.getQueryState(loading)?.status, 'success');
    assert.deepEqual(client.getQueryData(loading), { items: ['new'] });
    client.clear();
});
