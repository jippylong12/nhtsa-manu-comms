/* Labels for a vehicle's availability and access, shared by the card and the page. */

import type { Me, MyVehicle } from '@/client';

export function availabilityLabel(v: MyVehicle): string {
    if (v.availability === 'ready') return 'Ready';
    if (v.availability === 'processing') return 'Reading…';
    return 'Unknown';
}

/** "Subscribed" when an active subscription covers every car, else "Unlocked" for a paid car. */
export function accessLabel(v: MyVehicle, me: Me | undefined): 'Sample' | 'Unlocked' | 'Subscribed' {
    if (v.access === 'sample') return 'Sample';
    const subscribed = me?.entitlements.some((e) => e.kind === 'subscription' && e.status === 'active');
    return subscribed ? 'Subscribed' : 'Unlocked';
}

export function vehicleName(v: { year: number; make: string; model: string }): string {
    return `${v.year} ${v.make} ${v.model}`;
}
