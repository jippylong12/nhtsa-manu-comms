/* TanStack Query hooks for daily alerts: the account toggle and the unsubscribe link */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { meApi, publicApi } from '@/client';
import type { AlertsStatus, MyVehicle, UnsubscribeResponse } from '@/client';
import { alertKeys, meKeys } from '../../queryKeys';

export function useSetAlerts() {
    const queryClient = useQueryClient();
    return useMutation<AlertsStatus, Error, boolean>({
        mutationFn: (enabled) => meApi.setAlerts(enabled),
        // Holding the promise keeps the switch pending until `me` has the new state.
        onSuccess: () => queryClient.invalidateQueries({ queryKey: meKeys.all }),
    });
}

/** Includes or leaves out one car in the alert email. */
export function useSetVehicleAlerts() {
    const queryClient = useQueryClient();
    return useMutation<MyVehicle, Error, { vehicleId: number; enabled: boolean }>({
        mutationFn: ({ vehicleId, enabled }) => meApi.setVehicleAlerts(vehicleId, enabled),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: meKeys.all }),
    });
}

/** Calls the unsubscribe link once per page load: a query is de-duplicated across
 *  StrictMode's double effect, and never retried or refetched. */
export function useUnsubscribe(token: string) {
    return useQuery<UnsubscribeResponse>({
        queryKey: alertKeys.unsubscribe(token),
        queryFn: () => publicApi.unsubscribe(token),
        retry: false,
        staleTime: Infinity,
        gcTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
}
