/* Public API - anonymous endpoints used by the landing page */

import { request } from './api';
import type { LookupResponse, UnsubscribeResponse } from './types';

export const publicApi = {
    lookup: (year: number, make: string, model: string) =>
        request<LookupResponse>(
            `/public/lookup?year=${year}&make=${encodeURIComponent(make)}&model=${encodeURIComponent(model)}`
        ),

    /** GET /api/public/unsubscribe/{token}: idempotent; 404 (ApiError) for a bad link. */
    unsubscribe: (token: string) =>
        request<UnsubscribeResponse>(`/public/unsubscribe/${encodeURIComponent(token)}`),
};
