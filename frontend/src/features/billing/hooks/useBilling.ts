/* TanStack Query mutations that send the browser to Stripe. */

import { useMutation } from '@tanstack/react-query';

import { billingApi } from '@/client';
import type { CheckoutKind, SubscriptionPlan } from '@/client';
import { rememberCheckout } from '../pending';

/** Creates a Checkout session on the server, then leaves for the URL it returned. */
export function useCheckout() {
    return useMutation<string, Error, { kind: CheckoutKind; vehicleId?: number; plan?: SubscriptionPlan }>({
        mutationFn: async ({ kind, vehicleId, plan }) =>
            (await billingApi.checkout(kind, vehicleId, plan)).url,
        onSuccess: (url, { kind, vehicleId }) => {
            rememberCheckout({ kind, vehicleId });
            window.location.assign(url);
        },
    });
}

/** Creates a Customer Portal session, then leaves for the URL it returned. */
export function usePortal() {
    return useMutation<string, Error, void>({
        mutationFn: async () => (await billingApi.portal()).url,
        onSuccess: (url) => window.location.assign(url),
    });
}
