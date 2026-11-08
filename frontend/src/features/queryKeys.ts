/* Query Key Factory - Prevents cache invalidation bugs */

export const vehicleKeys = {
    all: ['vehicles'] as const,
    lists: () => [...vehicleKeys.all, 'list'] as const,
    list: (page: number, perPage: number) => [...vehicleKeys.lists(), { page, perPage }] as const,
    details: () => [...vehicleKeys.all, 'detail'] as const,
    detail: (vehicleId: number) => [...vehicleKeys.details(), vehicleId] as const,
};

// communicationKeys went with the Mongo-backed list and detail endpoints
// (JIP-417). Communications are read through corpusKeys.

export const refreshKeys = {
    all: ['refresh'] as const,
    run: (vehicleId: number) => [...refreshKeys.all, 'run', vehicleId] as const,
};

export const corpusKeys = {
    all: ['corpus'] as const,
    lists: () => [...corpusKeys.all, 'list'] as const,
    list: (filters: Record<string, unknown>) => [...corpusKeys.lists(), filters] as const,
    details: () => [...corpusKeys.all, 'detail'] as const,
    detail: (nhtsaId: string) => [...corpusKeys.details(), nhtsaId] as const,
    tags: (vehicleId?: number) => [...corpusKeys.all, 'tags', vehicleId ?? null] as const,
};

// The signed-in user's account. AuthContext reads ['me'] for the profile.
export const meKeys = {
    all: ['me'] as const,
    bulletins: (vehicleId: number, params: Record<string, unknown>) =>
        [...meKeys.all, 'vehicles', vehicleId, 'bulletins', params] as const,
};
