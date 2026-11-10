/* Pure helpers for the optimistic mark-as-read cache update. No app imports: node --test loads this file as is. */

/** Where `meKeys.bulletins(vehicleId, params)` keeps its params: ['me','vehicles',id,'bulletins',params]. */
export const BULLETINS_PARAMS_INDEX = 4;

/**
 * True when a cached list is not narrowed by search or filters, so it holds every unread
 * bulletin its facets count. A narrowed list that lacks the row says nothing about it.
 * `nhtsaId` is not a narrowing: the facets of a one-id fetch ignore it.
 */
export function isUnnarrowed(key: readonly unknown[]): boolean {
    const p = (key[BULLETINS_PARAMS_INDEX] ?? {}) as Record<string, unknown>;
    return !(p.search || p.commType || p.systems || p.dateFrom || p.dateTo);
}

/**
 * Cancel filter for the optimistic update: only queries that already hold data. A first
 * fetch has nothing to overwrite, and nothing would restart it once cancelled.
 */
export function hasData(query: { state: { data: unknown } }): boolean {
    return query.state.data !== undefined;
}
