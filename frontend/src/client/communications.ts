/* Discovery API Functions
 *
 * The year, make, model and variant lookups that fill the add-vehicle form.
 * The list, detail, stats and SSE fetch functions that used to sit here backed
 * the Mongo path and are gone (JIP-417): reading communications is corpusApi,
 * fetching them is refreshApi.
 */

import { request } from './api';

export const communicationApi = {
    // Discovery API
    getYears: () => request<number[]>('/communications/discovery/years'),
    getMakes: (year: number) => request<string[]>(`/communications/discovery/makes?year=${year}`),
    getModels: (year: number, make: string) =>
        request<string[]>(`/communications/discovery/models?year=${year}&make=${encodeURIComponent(make)}`),
    getVariants: (year: number, make: string, model: string) => {
        const params = new URLSearchParams({
            year: String(year),
            make,
            model,
        });
        return request<import('./types').VehicleVariant[]>(`/communications/discovery/variants?${params.toString()}`);
    },
};
