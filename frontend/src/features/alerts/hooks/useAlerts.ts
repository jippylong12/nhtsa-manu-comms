/* TanStack Query hooks for daily alerts: the account toggle and the unsubscribe link */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { meApi, publicApi } from '@/client';
import type { AlertsStatus, UnsubscribeResponse } from '@/client';
import { alertKeys, meKeys } from '../../queryKeys';

export function useSetAlerts() {
    const queryClient = useQueryClient();
    return useMutation<AlertsStatus, Error, boolean>({
        mutationFn: (enabled) => meApi.setAlerts(enabled),
        // Holding the promise keeps the switch pending until `me` has the new state.
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
