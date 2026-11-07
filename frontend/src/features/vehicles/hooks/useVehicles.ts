/* Vehicle Query Hooks */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getHealth } from '@/client/api';
import { vehicleApi, type VehicleCreate, type VehicleUpdate } from '@/client';
import { vehicleKeys, corpusKeys } from '@/features/queryKeys';

/** True on a read-only deploy. One cached health query shared by every caller. */
export function useReadOnly(): boolean {
    const { data } = useQuery({
        queryKey: ['health'],
        queryFn: getHealth,
        staleTime: Infinity,
    });
    return data?.readOnly === true;
}

export function useVehiclesQuery(page = 1, perPage = 20) {
    return useQuery({
        queryKey: vehicleKeys.list(page, perPage),
        queryFn: () => vehicleApi.list(page, perPage),
    });
}

export function useVehicleQuery(vehicleId: number) {
    return useQuery({
        queryKey: vehicleKeys.detail(vehicleId),
        queryFn: () => vehicleApi.get(vehicleId),
        enabled: vehicleId > 0,
    });
}

export function useCreateVehicle() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (data: VehicleCreate) => vehicleApi.create(data),
        onSuccess: () => {
            queryClient.invalidateQueries({
                queryKey: vehicleKeys.lists(),
                refetchType: 'active',
            });
        },
    });
}

export function useUpdateVehicle() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ vehicleId, data }: { vehicleId: number; data: VehicleUpdate }) =>
            vehicleApi.update(vehicleId, data),
        onSuccess: (_, { vehicleId }) => {
            queryClient.invalidateQueries({
                queryKey: vehicleKeys.detail(vehicleId),
                refetchType: 'active',
            });
            queryClient.invalidateQueries({
                queryKey: vehicleKeys.lists(),
                refetchType: 'active',
            });
        },
    });
}

export function useDeleteVehicle() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (vehicleId: number) => vehicleApi.delete(vehicleId),
        onSuccess: (_, vehicleId) => {
            // Force refetch even with stale data
            queryClient.invalidateQueries({
                queryKey: vehicleKeys.lists(),
                refetchType: 'active',
            });
            queryClient.removeQueries({ queryKey: vehicleKeys.detail(vehicleId) });
            // The corpus is what the detail page reads, so a removed vehicle
            // has to drop out of that cache too.
            queryClient.invalidateQueries({
                queryKey: corpusKeys.all,
                refetchType: 'active',
            });
        },
    });
}
