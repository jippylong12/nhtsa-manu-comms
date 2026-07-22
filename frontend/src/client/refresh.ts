/* Refresh API Functions - runs the pipeline for one vehicle */

import { request } from './api';
import type { RefreshPreview, RefreshRun } from './types';

export const refreshApi = {
    /** Free. Always call this before start() and show the user what it says. */
    preview: (vehicleId: number) => request<RefreshPreview>(`/refresh/${vehicleId}/preview`),

    /** Billed. Returns immediately; poll status() for progress. */
    start: (vehicleId: number) =>
        request<RefreshRun>(`/refresh/${vehicleId}`, { method: 'POST' }),

    /**
     * The current or most recent run. Resolves to undefined when the vehicle
     * has never been refreshed, which the API reports as a 204 rather than an
     * error: it is an ordinary state for a newly added vehicle.
     */
    status: (vehicleId: number) => request<RefreshRun | undefined>(`/refresh/${vehicleId}`),
};
