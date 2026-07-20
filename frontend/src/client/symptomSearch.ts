/* Symptom Search API - POST a described problem + vehicle, get a grounded answer */

import { request } from './api';
import type { SymptomSearchRequest, SymptomSearchResponse } from './types';

export const symptomSearchApi = {
    search: (body: SymptomSearchRequest) =>
        request<SymptomSearchResponse>('/symptom-search', {
            method: 'POST',
            body: JSON.stringify(body),
        }),
};
