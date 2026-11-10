/* What the funnel events carry. Pure, so the shapes are tested directly (npm test).
 *
 * Rules for everything in here: no email, no name, no account id, and never the text of a
 * symptom search. A car's year, make and model are fine: they describe a product, not a person. */

export const GTM_ID = 'GTM-TW42MFGP';

export type Product = 'unlock' | 'monthly' | 'all_access';

const PRODUCTS: Record<Product, { name: string; price: number }> = {
    unlock: { name: 'Unlock a car', price: 3 },
    monthly: { name: 'Monthly', price: 5 },
    all_access: { name: 'All access', price: 20 },
};

/** Which product a checkout is for. A subscription without a plan is the $5 one. */
export function productOf(kind: 'unlock' | 'subscription', plan?: string | null): Product {
    if (kind === 'unlock') return 'unlock';
    return plan === 'all_access' ? 'all_access' : 'monthly';
}

/** GA4's ecommerce shape for begin_checkout and purchase. */
export function ecommerce(product: Product): {
    currency: string;
    value: number;
    items: { item_id: string; item_name: string; price: number; quantity: number }[];
} {
    const p = PRODUCTS[product];
    return {
        currency: 'USD',
        value: p.price,
        items: [{ item_id: product, item_name: p.name, price: p.price, quantity: 1 }],
    };
}

/** A short, non-secret id for a purchase: the tail of Stripe's session id. */
export function transactionId(sessionId: string | null | undefined): string | undefined {
    return sessionId ? sessionId.slice(-12) : undefined;
}

/** The Google tag stays off on a developer's machine and on pages whose address is private. */
export function shouldLoad(hostname: string, pathname: string): boolean {
    const local =
        hostname === 'localhost' ||
        hostname === '127.0.0.1' ||
        hostname === '[::1]' ||
        hostname.endsWith('.localhost');
    if (local) return false;
    // The unsubscribe address carries a personal token; the admin console is not a visitor.
    return !pathname.startsWith('/unsubscribe') && !pathname.startsWith('/admin');
}

/* Query parameters that may leave the browser with a page address: the car a visitor looked
 * up and which view of it is open. Everything else is dropped, because it is either typed
 * by a person (`search`), an identifier (`session_id`, `b`) or unknown. */
const SAFE_PARAMS = new Set(['year', 'make', 'model', 'severity', 'view']);

/** The page address as analytics may see it: allow-listed query parameters only, no fragment. */
export function scrubUrl(href: string): string {
    let url: URL;
    try {
        url = new URL(href);
    } catch {
        return '';
    }
    const kept = new URLSearchParams();
    for (const [key, value] of url.searchParams) {
        if (SAFE_PARAMS.has(key)) kept.append(key, value);
    }
    const query = kept.toString();
    return `${url.origin}${url.pathname}${query ? `?${query}` : ''}`;
}

/** True the first time `key` is seen. `seen` holds for this page load; `storage`, when it
 *  works, holds across reloads. A storage that throws must never cause a second "first time". */
export function firstTime(
    key: string,
    seen: Set<string>,
    storage?: { getItem(k: string): string | null; setItem(k: string, v: string): void } | null
): boolean {
    if (seen.has(key)) return false;
    seen.add(key);
    try {
        if (storage?.getItem(key)) return false;
        storage?.setItem(key, '1');
    } catch {
        // Storage blocked: the in-memory set above still stops repeats on this page.
    }
    return true;
}
