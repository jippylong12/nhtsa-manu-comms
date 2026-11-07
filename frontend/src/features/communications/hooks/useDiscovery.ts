/* Vehicle Discovery Hooks */

import { useQuery } from '@tanstack/react-query';
import { communicationApi } from '@/client';

export function useDiscoveryYears() {
    return useQuery({
        queryKey: ['discovery', 'years'],
        queryFn: () => communicationApi.getYears(),
        staleTime: 1000 * 60 * 60 * 24, // 24 hours
    });
}

export function useDiscoveryMakes(year: number | null) {
    return useQuery({
        queryKey: ['discovery', 'makes', year],
        queryFn: () => (year ? communicationApi.getMakes(year) : Promise.resolve([])),
        enabled: !!year,
        staleTime: 1000 * 60 * 60, // 1 hour
    });
}

export function useDiscoveryModels(year: number | null, make: string) {
    return useQuery({
        queryKey: ['discovery', 'models', year, make],
        queryFn: () =>
            year && make ? communicationApi.getModels(year, make) : Promise.resolve([]),
        enabled: !!year && !!make,
        staleTime: 1000 * 60 * 60, // 1 hour
    });
}
