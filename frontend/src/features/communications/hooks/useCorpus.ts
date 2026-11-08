/* React Query hooks for the Postgres corpus read API */

import { useQuery } from '@tanstack/react-query';

import { corpusApi, meApi } from '@/client';
import type { CorpusCommunicationDetail, CorpusFilters } from '@/client';
import { corpusKeys, meKeys } from '../../queryKeys';

export function useCorpusCommunicationsQuery(filters: CorpusFilters = {}) {
    return useQuery({
        queryKey: corpusKeys.list(filters),
        queryFn: () => corpusApi.list(filters),
    });
}

/**
 * One communication's detail. The admin corpus endpoint reads any id; on a product
 * page pass `vehicleId` so the read goes through the sample-aware /api/me endpoint
 * instead. A locked row comes back as null: there is nothing more to show.
 */
export function useCorpusDetailQuery(nhtsaId: string | null, vehicleId?: number) {
    return useQuery<CorpusCommunicationDetail | null>({
        queryKey:
            vehicleId === undefined
                ? corpusKeys.detail(nhtsaId ?? '')
                : meKeys.bulletin(vehicleId, nhtsaId ?? ''),
        queryFn: async () => {
            if (vehicleId === undefined) return corpusApi.get(nhtsaId as string);
            const found = await meApi.bulletin(vehicleId, nhtsaId as string);
            return found.locked ? null : found;
        },
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
