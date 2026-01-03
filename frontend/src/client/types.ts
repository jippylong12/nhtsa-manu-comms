/* API Types - Mirrors backend Pydantic schemas */

// Vehicles
export interface Vehicle {
    _id: string;
    vehicleId: number;
    year: string;
    model: string;
    keywords: string[];
    lastFetched: string | null;
    commCount: number;
    createdAt: string;
    updatedAt: string;
}

export interface VehicleCreate {
    vehicleId: number;
    year: string;
    model: string;
    keywords?: string[];
}

export interface VehicleUpdate {
    year?: string;
    model?: string;
    keywords?: string[];
}

export interface VehicleListResponse {
    items: Vehicle[];
    total: number;
    page: number;
    perPage: number;
}

// Communications
export interface AssociatedProduct {
    productYear: string;
    productModel: string;
    productMake?: string;
}

export interface AssociatedDocument {
    url: string;
    summary: string;
    loadDate?: string;
}

export interface Communication {
    _id: string;
    nhtsaId: number;
    vehicleId: number;
    communicationNumber?: string;
    communicationDate?: string;
    summary: string;
    detailsSummary?: string;
    associatedProducts: AssociatedProduct[];
    associatedDocuments: AssociatedDocument[];
    matchedKeywords: string[];
    fetchedAt: string;
}

export interface CommunicationListResponse {
    items: Communication[];
    total: number;
    page: number;
    perPage: number;
}

export interface FetchRequest {
    vehicleId: number;
    forceRefresh?: boolean;
}

export interface FetchProgress {
    status: 'pending' | 'fetching' | 'complete' | 'error';
    progress: number;
    message: string;
    totalIds: number;
    fetchedIds: number;
    newCount: number;
}

export interface FetchResult {
    vehicleId: number;
    totalFetched: number;
    newCount: number;
    matchedCount: number;
    durationSeconds: number;
}

// Filters
export interface CommunicationFilters {
    [key: string]: unknown;
    vehicleId?: number;
    year?: string;
    model?: string;
    keywords?: string;
    page?: number;
    perPage?: number;
}

