/* The Google tag and the funnel events.
 *
 * GTM-TW42MFGP is a Google tag (not a Tag Manager container): it is already connected to
 * the Google Analytics property, counts page views by itself, and takes events as
 * `gtag('event', name, params)`. Nothing is sent anywhere until `initAnalytics` has loaded
 * the tag, which it does not do on a developer's machine, on /unsubscribe or on /admin. */

import { GTM_ID, ecommerce, firstTime, productOf, scrubUrl, shouldLoad, transactionId } from './events';

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
    // Every event, the tag's own first page view included, reports this address and never
    // the raw one: a search a person typed or a payment session id must not leave the browser.
    lastPage = scrubUrl(window.location.href);
    gtag('set', { page_location: lastPage });
    const script = document.createElement('script');
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtm.js?id=${GTM_ID}`;
    script.dataset.gtm = GTM_ID;
    document.head.appendChild(script);
}

let lastPage = '';

/** Call on every route change. Sends one page view per page, with the scrubbed address;
 *  typing in a search box rewrites the address many times and counts as no change. */
export function trackPageView(): void {
    const page = scrubUrl(window.location.href);
    if (!page || page === lastPage) return;
    lastPage = page;
    gtag('set', { page_location: page });
    gtag('event', 'page_view', { page_location: page });
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

const purchasesSeen = new Set<string>();

/** Once per purchase: neither a re-render nor a refresh of the thank-you page counts it again. */
export function trackPurchase(
    kind: 'unlock' | 'subscription',
    plan: string | null | undefined,
    sessionId: string | null
): void {
    const id = transactionId(sessionId);
    let storage: Storage | null = null;
    try {
        storage = window.sessionStorage;
    } catch {
        // Reading the property itself can throw when site data is blocked.
    }
    if (!firstTime(`purchaseTracked:${id ?? 'unknown'}`, purchasesSeen, storage)) return;
    track('purchase', { transaction_id: id, ...ecommerce(productOf(kind, plan)) });
}

/** A symptom search ran. The words typed are never sent. */
export const trackSymptomSearch = () => track('symptom_search');

export const trackCancelStart = () => track('cancel_subscription_start');
export const trackRemoveVehicle = () => track('remove_vehicle');
