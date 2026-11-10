/* Labels for a vehicle's availability and access, shared by the card and the page. */

import { displayVehicle } from '../vehicles/displayName';
import type { Me, MyVehicle } from '@/client';

export function availabilityLabel(v: MyVehicle): string {
    if (v.availability === 'ready') return 'Ready';
    if (v.availability === 'processing') return 'Reading…';
    return 'Unknown';
}

/** True when the account bought this car outright ($3, forever). */
export function isUnlocked(v: { vehicleId: number }, me: Me | undefined): boolean {
    return (
        me?.entitlements.some(
            (e) => e.kind === 'unlock' && e.status === 'active' && e.vehicleId === v.vehicleId
        ) ?? false
    );
}

/** "Unlocked" for a car the account bought, "On your plan" for one a subscription covers. */
export function accessLabel(v: MyVehicle, me: Me | undefined): 'Sample' | 'Unlocked' | 'On your plan' {
    if (v.access === 'sample') return 'Sample';
    return isUnlocked(v, me) ? 'Unlocked' : 'On your plan';
}

/** The car's name as shown: "2024 Chevrolet Silverado EV" (stored values stay in NHTSA's capitals). */
export function vehicleName(v: { year: number; make: string; model: string }): string {
    return displayVehicle(v);
}
