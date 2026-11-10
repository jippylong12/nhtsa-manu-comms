/* What removing a car means for this car on this account. Pure, so it is tested directly. */

import { format, parseISO } from 'date-fns';

export interface RemovalFacts {
    /** The account bought this car outright. */
    unlocked: boolean;
    /** The car is a free sample (no plan, not unlocked). */
    sample: boolean;
    /** The subscriber's removal allowance, or null when the account is not limited. */
    allowance: { plan: 'monthly' | 'all_access'; limit: number; used: number; resetsOn: string } | null;
}

export type Tone = 'safe' | 'warn' | 'blocked';

export interface Consequence {
    tone: Tone;
    title: string;
    body: string;
}

const PLAN_NAME = { monthly: 'Monthly', all_access: 'All access' } as const;

/** What removing this car means: free to undo, costs a monthly removal, or not allowed yet. */
export function removalConsequence({ unlocked, sample, allowance }: RemovalFacts): Consequence {
    if (unlocked) {
        return {
            tone: 'safe',
            title: 'You own this car',
            body: 'It is unlocked for good. You can add it back any time at no cost, and everything will be open again.',
        };
    }
    if (allowance) {
        const plan = PLAN_NAME[allowance.plan];
        const left = Math.max(0, allowance.limit - allowance.used);
        const cars = allowance.limit === 1 ? '1 car' : `${allowance.limit} cars`;
        const reset = format(parseISO(allowance.resetsOn), 'MMMM d');
        if (left === 0) {
            return {
                tone: 'blocked',
                title: `You can remove another car from ${reset}`,
                body: `${plan} lets you remove ${cars} a month, and you have already done that this month.`,
            };
        }
        return {
            tone: 'warn',
            title:
                allowance.limit === 1
                    ? 'This is your one removal this month'
                    : `This uses 1 of your ${left} removals left this month`,
            body:
                left === 1
                    ? `${plan} lets you remove ${cars} a month. After this one, the next is from ${reset}.`
                    : `${plan} lets you remove ${cars} a month. The count starts again on ${reset}.`,
        };
    }
    if (sample) {
        return {
            tone: 'warn',
            title: 'Removing it does not give you a new free sample',
            body: 'A free account can sample two cars. This one keeps counting as one of yours, and you can add this same car back any time.',
        };
    }
    return {
        tone: 'safe',
        title: 'You can add it back later',
        body: 'Removing a car takes it off your list. Nothing else on your account changes.',
    };
}
