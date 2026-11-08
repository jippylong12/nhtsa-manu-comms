/* Billing API - Stripe Checkout and Customer Portal sessions, created by the server */

import { request } from './api';
import type { BillingUrlResponse, CheckoutKind } from './types';

export const billingApi = {
    /** POST /api/billing/checkout; ApiError 402/404/409/503 carry the server's message. */
    checkout: (kind: CheckoutKind, vehicleId?: number) =>
        request<BillingUrlResponse>('/billing/checkout', {
            method: 'POST',
            body: JSON.stringify(kind === 'unlock' ? { kind, vehicleId } : { kind }),
        }),

    /** POST /api/billing/portal; ApiError 404 when the caller never paid. */
    portal: () => request<BillingUrlResponse>('/billing/portal', { method: 'POST' }),
};
