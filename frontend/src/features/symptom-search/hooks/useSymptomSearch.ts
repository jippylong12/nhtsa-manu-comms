/* React Query mutation for the symptom-search endpoint */

import { useMutation } from '@tanstack/react-query';

import { symptomSearchApi } from '@/client';
import type { SymptomSearchRequest, SymptomSearchResponse } from '@/client';

export function useSymptomSearch() {
    return useMutation<SymptomSearchResponse, Error, SymptomSearchRequest>({
        mutationFn: (body) => symptomSearchApi.search(body),
    });
}
