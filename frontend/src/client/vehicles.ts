/* Vehicle API Functions */

import { request } from './api';
import type {
    Vehicle,
    VehicleCreate,
    VehicleUpdate,
    VehicleListResponse,
} from './types';

export const vehicleApi = {
    list: (page = 1, perPage = 20) =>
        request<VehicleListResponse>(`/vehicles?page=${page}&per_page=${perPage}`),

    get: (vehicleId: number) =>
        request<Vehicle>(`/vehicles/${vehicleId}`),

    create: (data: VehicleCreate) =>
        request<Vehicle>('/vehicles', {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    update: (vehicleId: number, data: VehicleUpdate) =>
        request<Vehicle>(`/vehicles/${vehicleId}`, {
            method: 'PATCH',
            body: JSON.stringify(data),
        }),

    delete: (vehicleId: number) =>
        request<void>(`/vehicles/${vehicleId}`, {
            method: 'DELETE',
        }),
};
