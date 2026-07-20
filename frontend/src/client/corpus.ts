/* Corpus API Functions - Postgres-backed processed pipeline */

import { request } from './api';
import type {
    CorpusListResponse,
    CorpusCommunicationDetail,
    CorpusFilters,
    TagVocabulary,
} from './types';

export const corpusApi = {
    list: (filters: CorpusFilters = {}) => {
        const params = new URLSearchParams();
        if (filters.vehicleId) params.append('vehicle_id', String(filters.vehicleId));
        if (filters.commType) params.append('comm_type', filters.commType);
        if (filters.status) params.append('status', filters.status);
        if (filters.dateFrom) params.append('date_from', filters.dateFrom);
        if (filters.dateTo) params.append('date_to', filters.dateTo);
        if (filters.systems && filters.systems.length > 0) {
            params.append('systems', filters.systems.join(','));
        }
        if (filters.components && filters.components.length > 0) {
            params.append('components', filters.components.join(','));
        }
        if (filters.search) params.append('search', filters.search);
        if (filters.page) params.append('page', String(filters.page));
        if (filters.perPage) params.append('per_page', String(filters.perPage));

        const query = params.toString();
        return request<CorpusListResponse>(`/corpus/communications${query ? `?${query}` : ''}`);
    },

    get: (nhtsaId: string) =>
        request<CorpusCommunicationDetail>(`/corpus/communications/${encodeURIComponent(nhtsaId)}`),

    tags: (limit = 100) =>
        request<TagVocabulary>(`/corpus/tags?limit=${limit}`),
};
