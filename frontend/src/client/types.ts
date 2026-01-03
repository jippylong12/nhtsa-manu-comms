/* API Types - Mirrors backend Pydantic schemas */

// Communication Types
export type CommType = 'TSB' | 'PIT' | 'PIC' | 'PIP' | 'OTHER';

export const COMM_TYPE_LABELS: Record<CommType, string> = {
    TSB: 'Technical Service Bulletin',
    PIT: 'Preliminary Info Technical',
    PIC: 'Preliminary Info Customer',
    PIP: 'Preliminary Info Parts',
    OTHER: 'Other',
};

export const COMM_TYPE_COLORS: Record<CommType, string> = {
    TSB: 'hsl(38, 92%, 50%)',      // Warning orange
    PIT: 'hsl(210, 100%, 56%)',    // Primary blue
    PIC: 'hsl(162, 73%, 46%)',     // Accent teal
    PIP: 'hsl(280, 65%, 60%)',     // Purple
    OTHER: 'hsl(215, 15%, 50%)',   // Gray
};

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
    communicationType: CommType;
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

// Stats
export interface CategoryStats {
    type: CommType;
    label: string;
    count: number;
}

export interface VehicleStats {
    vehicleId: number;
    totalCount: number;
    last30DaysCount: number;
    categories: CategoryStats[];
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
    search?: string;
    commType?: CommType;
    page?: number;
    perPage?: number;
}
