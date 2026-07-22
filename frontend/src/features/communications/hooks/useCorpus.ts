/* React Query hooks for the Postgres corpus read API */

import { useQuery } from '@tanstack/react-query';

import { corpusApi } from '@/client';
import type { CorpusFilters } from '@/client';
import { corpusKeys } from '../../queryKeys';

export function useCorpusCommunicationsQuery(filters: CorpusFilters = {}) {
    return useQuery({
        queryKey: corpusKeys.list(filters),
        queryFn: () => corpusApi.list(filters),
    });
}

export function useCorpusDetailQuery(nhtsaId: string | null) {
    return useQuery({
        queryKey: corpusKeys.detail(nhtsaId ?? ''),
        queryFn: () => corpusApi.get(nhtsaId as string),
        enabled: !!nhtsaId,
    });
}

export function useCorpusTagsQuery(vehicleId?: number, limit = 100) {
    return useQuery({
        // vehicleId is part of the key: the vocabulary is scoped to the vehicle,
        // so two vehicles must not share one cache entry.
        queryKey: corpusKeys.tags(vehicleId),
        queryFn: () => corpusApi.tags(limit, vehicleId),
        // Tag vocabulary changes only when the corpus is reprocessed.
        staleTime: 1000 * 60 * 30,
    });
}
