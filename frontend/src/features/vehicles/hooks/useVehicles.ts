/* Vehicle Query Hooks */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { vehicleApi, type VehicleCreate, type VehicleUpdate } from '@/client';
import { vehicleKeys, communicationKeys } from '@/features/queryKeys';

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
            queryClient.invalidateQueries({
                queryKey: communicationKeys.lists(),
                refetchType: 'active',
            });
        },
    });
}
