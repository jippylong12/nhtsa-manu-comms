/* The Google tag and the funnel events.
 *
 * GTM-TW42MFGP is a Google tag (not a Tag Manager container): it is already connected to
 * the Google Analytics property, counts page views by itself, and takes events as
 * `gtag('event', name, params)`. Nothing is sent anywhere until `initAnalytics` has loaded
 * the tag, which it does not do on a developer's machine, on /unsubscribe or on /admin. */

import { GTM_ID, ecommerce, productOf, shouldLoad, transactionId } from './events';

declare global {
    interface Window {
        dataLayer?: unknown[];
    }
}

// The Google tag only understands the `arguments` object itself, not an array copy of it.
const gtag = function () {
    window.dataLayer = window.dataLayer ?? [];
    // eslint-disable-next-line prefer-rest-params
    window.dataLayer.push(arguments);
} as (...args: unknown[]) => void;

/** Load the Google tag once. Safe to call on every page. */
export function initAnalytics(): void {
    if (!shouldLoad(window.location.hostname, window.location.pathname)) return;
    if (document.querySelector(`script[data-gtm="${GTM_ID}"]`)) return;
    gtag('js', new Date());
    const script = document.createElement('script');
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtm.js?id=${GTM_ID}`;
    script.dataset.gtm = GTM_ID;
    document.head.appendChild(script);
}

export function track(event: string, params: Record<string, unknown> = {}): void {
    gtag('event', event, params);
}

type Car = { year: number | string; make: string; model: string };
const car = (c: Car) => ({ vehicle_year: Number(c.year), vehicle_make: c.make, vehicle_model: c.model });

/* ---- The funnel, top to bottom ---- */

/** A visitor picked a year, make and model on the landing page and saw its counts. */
export const trackLookup = (c: Car, status: string, bulletins: number) =>
    track('vehicle_lookup', { ...car(c), lookup_status: status, bulletin_count: bulletins });

/** The create-account dialog opened. */
export const trackSignUpStart = (source: string) => track('sign_up_start', { source });

export const trackSignUp = (method: 'google' | 'email') => track('sign_up', { method });
export const trackLogin = (method: 'google' | 'email') => track('login', { method });

export const trackAddVehicle = (c: Car) => track('add_vehicle', car(c));

/** An account hit a limit and was shown an offer: free samples used, or the plan's cars. */
export const trackLimitReached = (plan: string) => track('limit_reached', { plan });

export function trackBeginCheckout(kind: 'unlock' | 'subscription', plan?: string | null): void {
    track('begin_checkout', ecommerce(productOf(kind, plan)));
}

/** Once per purchase: a refresh of the thank-you page must not count it again. */
export function trackPurchase(
    kind: 'unlock' | 'subscription',
    plan: string | null | undefined,
    sessionId: string | null
): void {
    const id = transactionId(sessionId);
    const key = `purchaseTracked:${id ?? 'unknown'}`;
    try {
        if (sessionStorage.getItem(key)) return;
        sessionStorage.setItem(key, '1');
    } catch {
        // Storage blocked: better to count than to lose the sale.
    }
    track('purchase', { transaction_id: id, ...ecommerce(productOf(kind, plan)) });
}

/** A symptom search ran. The words typed are never sent. */
export const trackSymptomSearch = () => track('symptom_search');

export const trackCancelStart = () => track('cancel_subscription_start');
export const trackRemoveVehicle = () => track('remove_vehicle');
