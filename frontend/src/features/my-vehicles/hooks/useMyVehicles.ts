/* TanStack Query hooks for the signed-in user's vehicles and bulletins */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { meApi } from '@/client';
import type { Me, MyBulletinsParams, MyBulletinsResponse, MyVehicle, Severity } from '@/client';
import { useAuth } from '@/auth/AuthContext';
import { meKeys } from '../../queryKeys';

// While any car is still being read, poll so the card flips to Ready by itself.
const PROCESSING_POLL_MS = 10_000;

export function useMeQuery() {
    const { user } = useAuth();
    return useQuery<Me>({
        queryKey: meKeys.all,
        queryFn: meApi.get,
        // Never fire before a signed-in user (and so a token provider) exists.
        enabled: Boolean(user),
        retry: false,
        refetchInterval: (query) =>
            query.state.data?.vehicles.some((v) => v.availability === 'processing')
                ? PROCESSING_POLL_MS
                : false,
    });
}

export function useAddVehicle() {
    const queryClient = useQueryClient();
    return useMutation<MyVehicle, Error, { year: number; make: string; model: string }>({
        mutationFn: ({ year, make, model }) => meApi.addVehicle(year, make, model),
        // Returning the promise holds the mutation open until `me` has refetched,
        // so callers that navigate on success land on a page that knows the car.
        onSuccess: () => queryClient.invalidateQueries({ queryKey: meKeys.all }),
    });
}

export function useRemoveVehicle() {
    const queryClient = useQueryClient();
    return useMutation<void, Error, number>({
        mutationFn: (id) => meApi.removeVehicle(id),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: meKeys.all }),
    });
}

export function useMyBulletinsQuery(
    vehicleId: number,
    params: MyBulletinsParams,
    enabled = true
) {
    return useQuery<MyBulletinsResponse>({
        queryKey: meKeys.bulletins(vehicleId, {
            ...params,
            systems: params.systems ? [...params.systems].sort() : undefined,
        }),
        queryFn: () => meApi.bulletins(vehicleId, params),
        enabled: enabled && Number.isInteger(vehicleId),
        placeholderData: keepPreviousData,
        retry: false,
    });
}

export function useMarkAllRead(vehicleId: number) {
    const queryClient = useQueryClient();
    return useMutation<{ marked: number }, Error, Severity | undefined>({
        mutationFn: (severity) => meApi.readAll(vehicleId, severity),
        // me.vehicles[].unread and every bulletins list (read flags, facets) are stale now.
        onSuccess: () => queryClient.invalidateQueries({ queryKey: meKeys.all }),
    });
}
