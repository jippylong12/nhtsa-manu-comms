/* Public API - anonymous endpoints used by the landing page */

import { request } from './api';
import type { LookupResponse } from './types';

export const publicApi = {
    lookup: (year: number, make: string, model: string) =>
        request<LookupResponse>(
            `/public/lookup?year=${year}&make=${encodeURIComponent(make)}&model=${encodeURIComponent(model)}`
        ),
};
