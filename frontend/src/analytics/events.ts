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
