/* Lookup hook - severity counts for one year/make/model, no sign-in needed */

import { useQuery } from '@tanstack/react-query';
import { publicApi } from '@/client';

export function useLookup(year: number | null, make: string, model: string) {
    return useQuery({
        queryKey: ['public', 'lookup', year, make, model],
        queryFn: () => publicApi.lookup(year as number, make, model),
        enabled: !!year && !!make && !!model,
        staleTime: 1000 * 60 * 5, // 5 minutes
    });
}
