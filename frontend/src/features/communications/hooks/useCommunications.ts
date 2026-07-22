/* Communication Query Hooks

   Only the Mongo-backed fetch survives here. Reading communications now goes
   through the Postgres corpus (useCorpus), so the list, detail, and stats hooks
   went with CommunicationsView. */

import { useQueryClient } from '@tanstack/react-query';
import { useState, useCallback } from 'react';
import { communicationApi, type FetchProgress } from '@/client';
import { communicationKeys, vehicleKeys } from '@/features/queryKeys';

export function useFetchCommunications() {
    const queryClient = useQueryClient();
    const [progress, setProgress] = useState<FetchProgress | null>(null);
    const [isFetching, setIsFetching] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const fetch = useCallback(
        (vehicleId: number, forceRefresh = false) => {
            setIsFetching(true);
            setError(null);
            setProgress({
                status: 'pending',
                progress: 0,
                message: 'Starting fetch...',
                totalIds: 0,
                fetchedIds: 0,
                newCount: 0,
            });

            const abort = communicationApi.fetchWithProgress(
                vehicleId,
                forceRefresh,
                (p) => setProgress(p),
                () => {
                    setIsFetching(false);
                    // Invalidate queries on completion
                    queryClient.invalidateQueries({ queryKey: communicationKeys.lists() });
                    queryClient.invalidateQueries({ queryKey: vehicleKeys.lists() });
                },
                (err) => {
                    setIsFetching(false);
                    setError(err);
                    setProgress((p) => (p ? { ...p, status: 'error', message: err } : null));
                }
            );

            return abort;
        },
        [queryClient]
    );

    const reset = useCallback(() => {
        setProgress(null);
        setError(null);
    }, []);

    return {
        fetch,
        reset,
        progress,
        isFetching,
        error,
        isComplete: progress?.status === 'complete',
    };
}
