/* The words of the Terms and the Privacy Policy. Plain language on purpose.
 * These are drafts written without a lawyer: have them reviewed before relying on them. */

import {
    ALL_ACCESS_CARS,
    ALL_ACCESS_PRICE_LABEL,
    ALL_ACCESS_REMOVALS,
    MONTHLY_CARS,
    MONTHLY_PRICE_LABEL,
    MONTHLY_REMOVALS,
    UNLOCK_PRICE_LABEL,
} from '../../features/billing/prices';

export const COMPANY = 'JippylongTwelve LLC';
export const PRODUCT = 'NHTSA Comms';
/** Where people write to us. Empty until one is chosen; the pages leave the address out while it is. */
export const CONTACT_EMAIL = '';
export const LAST_UPDATED = 'November 10, 2026';

export interface LegalSection {
    heading: string;
    paragraphs?: string[];
    bullets?: string[];
}

export interface LegalDoc {
    title: string;
    intro: string;
    sections: LegalSection[];
}

const CONTACT = CONTACT_EMAIL ? `Write to us at ${CONTACT_EMAIL}.` : '';

export const TERMS: LegalDoc = {
    title: 'Terms of Service',
    intro: `These terms are the agreement between you and ${COMPANY} ("we", "us") for using ${PRODUCT}. By creating an account or paying for a plan you agree to them.`,
    sections: [
        {
            heading: 'What the service is',
            paragraphs: [
                `${PRODUCT} collects the manufacturer communications that carmakers file with NHTSA, the US federal vehicle safety agency, and shows them by year, make and model. We summarize each document with software, including AI models, and link to the original.`,
                `${PRODUCT} is an independent service. It is not affiliated with, endorsed by or operated by NHTSA or any carmaker.`,
            ],
        },
        {
            heading: 'It is information, not advice',
            bullets: [
                'Summaries are written automatically and can be wrong or incomplete. Always read the original document, which we link, before acting on a bulletin.',
                'A bulletin listed for your year, make and model may not apply to your particular car. Trim, engine, build date and VIN range all matter.',
                'Nothing here is mechanical, safety or legal advice, and it is not a recall lookup. For recalls, check NHTSA or your dealer with your VIN.',
                'We do not have every communication ever filed, and new ones can take time to appear.',
            ],
        },
        {
            heading: 'Your account',
            bullets: [
                'You need an account to read bulletins. Keep your sign-in details to yourself; you are responsible for what happens under your account.',
                'One person per account. Do not share an account or resell access.',
                'You must be at least 18, or the age of majority where you live.',
            ],
        },
        {
            heading: 'Plans and what they include',
            bullets: [
                'Free: a sample of the newest bulletins for two different cars, in total, per account.',
                `Unlock (${UNLOCK_PRICE_LABEL}, one payment): every bulletin for one car, including bulletins published later, for as long as the service runs.`,
                `Monthly (${MONTHLY_PRICE_LABEL}): every bulletin for ${MONTHLY_CARS} cars at a time, the alert email, and ${MONTHLY_REMOVALS} car removal per calendar month.`,
                `All access (${ALL_ACCESS_PRICE_LABEL}): the same for ${ALL_ACCESS_CARS} cars at a time, with ${ALL_ACCESS_REMOVALS} car removals per calendar month.`,
                'Cars you have unlocked do not count toward a plan\'s car limit and can be removed and added back freely.',
                'Symptom search is included with an unlocked car or a plan and has fair-use limits on how many searches you can run per minute and per day.',
            ],
        },
        {
            heading: 'Payment, renewal and cancelling',
            bullets: [
                'Payments are handled by Stripe. We never see or store your full card number.',
                'Monthly and All access renew every month and charge the same card until you cancel.',
                'You can cancel at any time from your Account page. Cancelling stops the next renewal; you keep your plan until the end of the period you already paid for.',
                'We do not refund time already used. If you were charged by mistake or something did not work, tell us and we will put it right.',
                'If a renewal payment fails, plan features pause until the payment goes through.',
                'We may change prices. A new price never applies to a period you have already paid for, and we will tell subscribers before a renewal at a new price.',
            ],
        },
        {
            heading: 'Fair use',
            paragraphs: ['Please do not:'],
            bullets: [
                'copy the service in bulk, scrape it or use automated tools to pull data out of it;',
                'republish or resell our summaries;',
                'work around plan limits, for example by cycling cars or opening several accounts;',
                'interfere with the service or try to reach parts of it that are not meant for you.',
            ],
        },
        {
            heading: 'Our content and the source documents',
            paragraphs: [
                'The original documents are public records published by NHTSA. The summaries, organization and software are ours. Your plan lets you read them for your own use.',
            ],
        },
        {
            heading: 'Availability and changes',
            paragraphs: [
                'We work to keep the service running and accurate, but we provide it as it is, without warranties of any kind. We may change, pause or end features. If we shut the service down, subscriptions stop renewing.',
                'We may suspend or close an account that breaks these terms.',
            ],
        },
        {
            heading: 'Limit of liability',
            paragraphs: [
                `To the extent the law allows, ${COMPANY} is not liable for indirect or consequential losses, or for decisions made on the basis of information in the service, and our total liability to you is limited to what you paid us in the twelve months before the claim.`,
            ],
        },
        {
            heading: 'Changes to these terms',
            paragraphs: [
                'If we change these terms in a way that matters, we will post the new version here with a new date and tell account holders by email. Continuing to use the service after that means you accept them.',
            ],
        },
        {
            heading: 'Contact',
            paragraphs: [`${PRODUCT} is operated by ${COMPANY}. ${CONTACT}`.trim()],
        },
    ],
};

export const PRIVACY: LegalDoc = {
    title: 'Privacy Policy',
    intro: `This explains what ${COMPANY} collects when you use ${PRODUCT}, why, and who else handles it. The short version: we collect what the service needs to work, we do not sell it, and we do not run advertising trackers.`,
    sections: [
        {
            heading: 'What we collect',
            bullets: [
                'Account details: your email address and, if you sign in with Google, the name on your Google account.',
                'Your cars: the year, make and model of each car you add. We do not ask for a VIN, a plate or an address.',
                'How you use it: which bulletins you have opened, your alert settings, and the text of the symptom searches you run.',
                'Payments: what you bought, when, and whether a subscription is active. Card details go straight to Stripe and never reach our servers.',
                'Technical records: standard server logs such as IP address, browser type and the time of each request.',
            ],
        },
        {
            heading: 'Why we use it',
            bullets: [
                'to show you the bulletins for your cars and remember which you have read;',
                'to email you when a new bulletin lands, if you are on a plan and alerts are on;',
                'to take payment and know what your account includes;',
                'to keep the service secure, stop abuse and fix problems.',
            ],
        },
        {
            heading: 'Who else handles it',
            paragraphs: ['We use a small number of companies to run the service. Each one receives only what its job needs:'],
            bullets: [
                'Google Firebase: sign-in.',
                'Stripe: payments and billing.',
                'Resend: sending email.',
                'Railway: hosting and the database.',
                'Anthropic and Google: the AI models. When you run a symptom search, the text you type is sent to them to produce the answer. Do not put personal details in a search.',
            ],
        },
        {
            heading: 'What we do not do',
            bullets: [
                'We do not sell or rent your information.',
                'We do not show ads or use advertising or cross-site tracking cookies.',
                'We do not send marketing email. The only email we send is about your account, and alerts you can turn off.',
            ],
        },
        {
            heading: 'Storage in your browser',
            paragraphs: [
                'Signing in keeps a session in your browser so you stay signed in, and we remember a purchase in progress while you are at Stripe. That is all we store there.',
            ],
        },
        {
            heading: 'Email',
            paragraphs: [
                'Every alert email has an unsubscribe link, and you can turn alerts off, or leave a single car out, on your Account page.',
            ],
        },
        {
            heading: 'How long we keep it',
            paragraphs: [
                'We keep your account information while your account is open. Payment records are kept as long as tax and accounting rules require. Server logs are kept for a limited time.',
            ],
        },
        {
            heading: 'Your choices',
            paragraphs: [
                `You can ask us for a copy of the information we hold about you, ask us to correct it, or ask us to delete your account. ${CONTACT ? CONTACT + ' ' : ''}Depending on where you live, the law may give you further rights; we will honor them.`,
            ],
        },
        {
            heading: 'Children',
            paragraphs: ['The service is for adults. We do not knowingly collect information from anyone under 18.'],
        },
        {
            heading: 'Changes',
            paragraphs: ['If this policy changes in a way that matters, we will post the new version here with a new date and tell account holders by email.'],
        },
        {
            heading: 'Contact',
            paragraphs: [`${PRODUCT} is operated by ${COMPANY}. ${CONTACT}`.trim()],
        },
    ],
};
