/* What an account's entitlements add up to, for the plan card, banners and success page. */

import type { Entitlement, Me } from '@/client';

export type PlanState = 'free' | 'unlocked' | 'subscribed' | 'past_due';

export function subscriptionOf(me: Me | undefined): Entitlement | undefined {
    const subs = me?.entitlements.filter((e) => e.kind === 'subscription') ?? [];
    return subs.find((e) => e.status === 'active') ?? subs.find((e) => e.status === 'past_due');
}

export function isPastDue(me: Me | undefined): boolean {
    return subscriptionOf(me)?.status === 'past_due';
}

export function unlockedCount(me: Me | undefined): number {
    return me?.entitlements.filter((e) => e.kind === 'unlock' && e.status === 'active').length ?? 0;
}

export function planState(me: Me | undefined): PlanState {
    const sub = subscriptionOf(me);
    if (sub?.status === 'active') return 'subscribed';
    if (sub?.status === 'past_due') return 'past_due';
    return unlockedCount(me) > 0 ? 'unlocked' : 'free';
}

/** The me payload has no customer flag; any entitlement row means the account has paid Stripe. */
export function hasBillingAccount(me: Me | undefined): boolean {
    return (me?.entitlements.length ?? 0) > 0;
}
