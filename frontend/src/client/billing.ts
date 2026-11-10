/* Billing API - Stripe Checkout and Customer Portal sessions, created by the server */

import { request } from './api';
import type { BillingUrlResponse, CheckoutKind, SubscriptionPlan } from './types';

export const billingApi = {
    /** POST /api/billing/checkout; ApiError 402/404/409/503 carry the server's message. */
    checkout: (kind: CheckoutKind, vehicleId?: number, plan: SubscriptionPlan = 'monthly') =>
        request<BillingUrlResponse>('/billing/checkout', {
            method: 'POST',
            body: JSON.stringify(kind === 'unlock' ? { kind, vehicleId } : { kind, plan }),
        }),

    /** POST /api/billing/portal; ApiError 404 when the caller never paid. */
    portal: (flow: 'manage' | 'cancel' = 'manage') =>
        request<BillingUrlResponse>('/billing/portal', {
            method: 'POST',
            body: JSON.stringify({ flow }),
        }),
};
