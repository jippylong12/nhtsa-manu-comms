/* Communication API Functions */

import { request, API_BASE } from './api';
import type {
    Communication,
    CommunicationListResponse,
    CommunicationFilters,
    FetchResult,
    FetchProgress,
} from './types';

export const communicationApi = {
    list: (filters: CommunicationFilters = {}) => {
        const params = new URLSearchParams();
        if (filters.vehicleId) params.append('vehicle_id', String(filters.vehicleId));
        if (filters.year) params.append('year', filters.year);
        if (filters.model) params.append('model', filters.model);
        if (filters.keywords) params.append('keywords', filters.keywords);
        if (filters.page) params.append('page', String(filters.page));
        if (filters.perPage) params.append('per_page', String(filters.perPage));

        const query = params.toString();
        return request<CommunicationListResponse>(`/communications${query ? `?${query}` : ''}`);
    },

    get: (nhtsaId: number) =>
        request<Communication>(`/communications/${nhtsaId}`),

    fetchSync: (vehicleId: number, forceRefresh = false) =>
        request<FetchResult>('/communications/fetch-sync', {
            method: 'POST',
            body: JSON.stringify({ vehicleId, forceRefresh }),
        }),

    // SSE streaming fetch with progress
    fetchWithProgress: (
        vehicleId: number,
        forceRefresh: boolean,
        onProgress: (progress: FetchProgress) => void,
        onComplete: () => void,
        onError: (error: string) => void
    ) => {
        const controller = new AbortController();

        fetch(`${API_BASE}/communications/fetch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ vehicleId, forceRefresh }),
            signal: controller.signal,
        })
            .then(async (response) => {
                if (!response.ok) {
                    const error = await response.json().catch(() => ({ detail: 'Fetch failed' }));
                    onError(error.detail || 'Fetch failed');
                    return;
                }

                const reader = response.body?.getReader();
                if (!reader) {
                    onError('No response body');
                    return;
                }

                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const data = JSON.parse(line.slice(6)) as FetchProgress;
                                onProgress(data);
                            } catch {
                                // Ignore parse errors
                            }
                        }
                    }
                }

                onComplete();
            })
            .catch((err) => {
                if (err.name !== 'AbortError') {
                    onError(err.message || 'Fetch failed');
                }
            });

        return () => controller.abort();
    },
};
