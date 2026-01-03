/* Communication Query Hooks */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, useCallback } from 'react';
import { communicationApi, type CommunicationFilters, type FetchProgress } from '@/client';
import { communicationKeys, vehicleKeys } from '@/features/queryKeys';

export function useCommunicationsQuery(filters: CommunicationFilters = {}) {
    return useQuery({
        queryKey: communicationKeys.list(filters),
        queryFn: () => communicationApi.list(filters),
    });
}

export function useCommunicationQuery(nhtsaId: number) {
    return useQuery({
        queryKey: communicationKeys.detail(nhtsaId),
        queryFn: () => communicationApi.get(nhtsaId),
        enabled: nhtsaId > 0,
    });
}

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
