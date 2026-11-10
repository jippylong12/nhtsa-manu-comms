/* How a make or model is shown. NHTSA files everything in capitals ("CHEVROLET SILVERADO EV");
   the stored value never changes, only what is displayed. Pure: node --test loads this file as is. */

// Trim levels and badges that are read letter by letter, so they stay in capitals.
const ACRONYMS = new Set([
    'AMG', 'AWD', 'CLA', 'CLS', 'EQA', 'EQB', 'EQE', 'EQS', 'ESV', 'FWD', 'GLA', 'GLB', 'GLC', 'GLE',
    'GTE', 'GTI', 'GTO', 'GTS', 'HEV', 'IROC', 'PHEV', 'RWD', 'SLK', 'SRT', 'STI', 'SUV', 'TRD', 'TTS',
    'USA',
]);

// Names whose capitals sit somewhere a general rule cannot find.
const SPECIAL: Record<string, string> = {
    MCLAREN: 'McLaren',
    BRIGHTDROP: 'BrightDrop',
};

const HAS_VOWEL = /[AEIOU]/;

function word(token: string): string {
    if (!token) return token;
    const upper = token.toUpperCase();
    if (SPECIAL[upper]) return SPECIAL[upper];
    if (ACRONYMS.has(upper)) return upper;
    // MAZDA3 -> Mazda3: a real word with a number stuck on the end.
    const named = /^([A-Z]{4,})(\d+)$/.exec(upper);
    if (named && HAS_VOWEL.test(named[1])) {
        return named[1][0] + named[1].slice(1).toLowerCase() + named[2];
    }
    // A6, 4S, CT5, RAV4, 1500: anything else with a digit is a code.
    if (/\d/.test(upper)) return upper;
    // EV, GT, XL, S: one or two letters are read out, not pronounced.
    if (upper.length <= 2) return upper;
    // GMC, BMW, FCCC: no vowel means nobody says it as a word.
    if (!HAS_VOWEL.test(upper)) return upper;
    return upper[0] + upper.slice(1).toLowerCase();
}

/** "CHEVROLET" -> "Chevrolet", "SILVERADO EV" -> "Silverado EV", "MERCEDES-BENZ" -> "Mercedes-Benz". */
export function displayName(raw: string | null | undefined): string {
    if (!raw) return '';
    return raw
        .trim()
        .split(/\s+/)
        .map((part) => part.split('-').map(word).join('-'))
        .join(' ');
}

/** "2024 Chevrolet Silverado EV" from the stored year, make and model. */
export function displayVehicle(v: { year: number | string; make?: string | null; model: string }): string {
    return [String(v.year), displayName(v.make), displayName(v.model)].filter(Boolean).join(' ');
}
