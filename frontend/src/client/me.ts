/* Me API - who is signed in, their vehicles, and their bulletins */

import { request } from './api';
import type { Me, MyBulletinsParams, MyBulletinsResponse, MyVehicle } from './types';

export const meApi = {
    /** GET /api/me; 401 (ApiError) when nobody is signed in. */
    get: () => request<Me>('/me'),

    /** POST /api/me/vehicles: link a vehicle to the account, ingesting it when new. */
    addVehicle: (year: number, make: string, model: string) =>
        request<MyVehicle>('/me/vehicles', {
            method: 'POST',
            body: JSON.stringify({ year, make, model }),
        }),

    /** DELETE /api/me/vehicles/{id}: 404 (ApiError) when the account has no such link. */
    removeVehicle: (id: number) => request<void>(`/me/vehicles/${id}`, { method: 'DELETE' }),

    /** GET /api/me/vehicles/{id}/bulletins: newest first, locked beyond the free sample. */
    bulletins: (id: number, params: MyBulletinsParams = {}) => {
        const q = new URLSearchParams();
        if (params.severity) q.set('severity', params.severity);
        if (params.search) q.set('search', params.search);
        if (params.page) q.set('page', String(params.page));
        if (params.perPage) q.set('perPage', String(params.perPage));
        const qs = q.toString();
        return request<MyBulletinsResponse>(`/me/vehicles/${id}/bulletins${qs ? `?${qs}` : ''}`);
    },
};
