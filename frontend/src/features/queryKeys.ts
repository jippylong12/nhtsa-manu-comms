/* Query Key Factory - Prevents cache invalidation bugs */

export const vehicleKeys = {
    all: ['vehicles'] as const,
    lists: () => [...vehicleKeys.all, 'list'] as const,
    list: (page: number, perPage: number) => [...vehicleKeys.lists(), { page, perPage }] as const,
    details: () => [...vehicleKeys.all, 'detail'] as const,
    detail: (vehicleId: number) => [...vehicleKeys.details(), vehicleId] as const,
};

export const communicationKeys = {
    all: ['communications'] as const,
    lists: () => [...communicationKeys.all, 'list'] as const,
    list: (filters: Record<string, unknown>) => [...communicationKeys.lists(), filters] as const,
    details: () => [...communicationKeys.all, 'detail'] as const,
    detail: (nhtsaId: number) => [...communicationKeys.details(), nhtsaId] as const,
};

export const corpusKeys = {
    all: ['corpus'] as const,
    lists: () => [...corpusKeys.all, 'list'] as const,
    list: (filters: Record<string, unknown>) => [...corpusKeys.lists(), filters] as const,
    details: () => [...corpusKeys.all, 'detail'] as const,
    detail: (nhtsaId: string) => [...corpusKeys.details(), nhtsaId] as const,
    tags: () => [...corpusKeys.all, 'tags'] as const,
};
