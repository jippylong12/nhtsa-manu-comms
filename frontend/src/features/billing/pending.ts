/* Remembers what was being bought across the Stripe redirect. Display only: access comes from /api/me. */

import type { CheckoutKind } from '@/client';

const KEY = 'pendingCheckout';

export interface PendingCheckout {
    kind: CheckoutKind;
    vehicleId?: number;
}

export function rememberCheckout(p: PendingCheckout): void {
    try {
        sessionStorage.setItem(KEY, JSON.stringify(p));
    } catch {
        // Storage can be blocked; the success page then falls back to what /api/me shows.
    }
}

export function recallCheckout(): PendingCheckout | null {
    try {
        const raw = sessionStorage.getItem(KEY);
        if (!raw) return null;
        const p = JSON.parse(raw) as PendingCheckout;
        return p.kind === 'unlock' || p.kind === 'subscription' ? p : null;
    } catch {
        return null;
    }
}
