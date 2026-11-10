/* Me API - who is signed in, their vehicles, and their bulletins */

import { request } from './api';
import type {
    AlertsStatus,
    Me,
    MyBulletinDetail,
    MyBulletinsParams,
    MyBulletinsResponse,
    MyVehicle,
    Severity,
} from './types';

export const meApi = {
    /** GET /api/me; 401 (ApiError) when nobody is signed in. */
    get: () => request<Me>('/me'),

    /** POST /api/me/alerts: 402 (ApiError) when turning on without a subscription. */
    setAlerts: (enabled: boolean) =>
        request<AlertsStatus>('/me/alerts', {
            method: 'POST',
            body: JSON.stringify({ enabled }),
        }),

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
        if (params.unreadOnly) q.set('unreadOnly', 'true');
        if (params.commType) q.set('commType', params.commType);
        if (params.systems?.length) q.set('systems', params.systems.join(','));
        if (params.dateFrom) q.set('dateFrom', params.dateFrom);
        if (params.dateTo) q.set('dateTo', params.dateTo);
        if (params.nhtsaId) q.set('nhtsaId', params.nhtsaId);
        const qs = q.toString();
        return request<MyBulletinsResponse>(`/me/vehicles/${id}/bulletins${qs ? `?${qs}` : ''}`);
    },

    /** POST /api/me/vehicles/{id}/read-all: mark every bulletin (or one severity) read. */
    readAll: (id: number, severity?: Severity) =>
        request<{ marked: number }>(
            `/me/vehicles/${id}/read-all${severity ? `?severity=${severity}` : ''}`,
            { method: 'POST' }
        ),

    /** POST /api/me/bulletins/{nhtsaId}/read: 204. */
    markRead: (nhtsaId: string) =>
        request<void>(`/me/bulletins/${encodeURIComponent(nhtsaId)}/read`, { method: 'POST' }),

    /** GET /api/me/vehicles/{id}/bulletins/{nhtsaId}: full detail, or `locked: true` identity only. */
    bulletin: (id: number, nhtsaId: string) =>
        request<MyBulletinDetail>(`/me/vehicles/${id}/bulletins/${encodeURIComponent(nhtsaId)}`),
};
