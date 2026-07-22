/* Refresh hooks - estimate, confirm, then go (JIP-419)
 *
 * A run outlives the click that started it, so its state comes from the server
 * on a poll rather than from component state. Leaving the vehicle page and
 * coming back re-reads the same run instead of losing it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { refreshApi, isRefreshActive } from '@/client';
import type { RefreshPreview, RefreshRun } from '@/client';
import { corpusKeys, refreshKeys, vehicleKeys } from '@/features/queryKeys';

// Fast enough that the stage line feels live, slow enough that a 15 minute run
// is a few hundred requests rather than a few thousand.
const POLL_MS = 2000;

export function useRefreshRun(vehicleId: number | null) {
    return useQuery<RefreshRun | null>({
        queryKey: refreshKeys.run(vehicleId ?? 0),
        // Coerced to null, never left undefined. A vehicle that has never been
        // refreshed is an ordinary 204, and the client turns an empty body into
        // undefined, which React Query rejects outright as query data. That
        // failure put the query into an error state, and since the poll below
        // keys on the data, polling then latched off for the whole run.
        queryFn: async () => (await refreshApi.status(vehicleId as number)) ?? null,
        enabled: vehicleId !== null && vehicleId > 0,
        // Only poll while something is actually happening.
        refetchInterval: (query) => (isRefreshActive(query.state.data) ? POLL_MS : false),
        refetchOnWindowFocus: true,
        staleTime: 0,
        // A poll that fails once (a dropped request mid-run) must not stop the
        // whole thing; the next interval tick tries again.
        retry: 1,
    });
}

/**
 * The whole flow: ask what it would cost, let the user confirm, then run it.
 *
 * `preview` is held in local state rather than a query because it is a
 * question asked at a moment in time, not a cache entry: showing yesterday's
 * estimate on a dialog the user is about to agree to would be wrong.
 */
export function useRefreshFlow(vehicleId: number | null) {
    const queryClient = useQueryClient();
    const [preview, setPreview] = useState<RefreshPreview | null>(null);
    const [upToDate, setUpToDate] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const { data: run } = useRefreshRun(vehicleId);

    // When a run finishes, the corpus and the card count are both stale. Doing
    // this here rather than inside the query function keeps the fetch free of
    // side effects, and firing only on the transition avoids re-invalidating
    // every poll while the run is still going.
    const wasActive = useRef(false);
    useEffect(() => {
        const active = isRefreshActive(run);
        if (wasActive.current && !active) {
            queryClient.invalidateQueries({ queryKey: corpusKeys.all });
            queryClient.invalidateQueries({ queryKey: vehicleKeys.lists() });
        }
        wasActive.current = active;
    }, [run, queryClient]);

    const previewMutation = useMutation({
        mutationFn: (id: number) => refreshApi.preview(id),
        onSuccess: (result) => {
            setError(null);
            // Nothing to do is a real answer, and asking someone to confirm a
            // run that would do nothing is a pointless step.
            if (result.upToDate) {
                setUpToDate(true);
                setPreview(null);
            } else {
                setUpToDate(false);
                setPreview(result);
            }
        },
        onError: (e: Error) => setError(e.message),
    });

    const startMutation = useMutation({
        mutationFn: (id: number) => refreshApi.start(id),
        onSuccess: (started) => {
            setPreview(null);
            setError(null);
            queryClient.setQueryData(refreshKeys.run(started.vehicleId), started);
            queryClient.invalidateQueries({ queryKey: refreshKeys.run(started.vehicleId) });
        },
        onError: (e: Error) => setError(e.message),
    });

    const ask = useCallback(
        (id: number) => {
            setUpToDate(false);
            previewMutation.mutate(id);
        },
        [previewMutation]
    );

    const confirm = useCallback(() => {
        if (preview) startMutation.mutate(preview.vehicleId);
    }, [preview, startMutation]);

    const cancel = useCallback(() => setPreview(null), []);
    const dismiss = useCallback(() => {
        setUpToDate(false);
        setError(null);
    }, []);

    return {
        run,
        isActive: isRefreshActive(run),
        preview,
        upToDate,
        error,
        isAsking: previewMutation.isPending,
        isStarting: startMutation.isPending,
        ask,
        confirm,
        cancel,
        dismiss,
    };
}
