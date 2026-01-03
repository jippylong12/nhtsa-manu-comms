/* API Types - Mirrors backend Pydantic schemas */

// Communication Types - Extended categorization
export type CommType =
    | 'TSB'   // Technical Service Bulletin (prefix-based)
    | 'PIT'   // Preliminary Info Technical (prefix-based)
    | 'PIC'   // Preliminary Info Customer (prefix-based)
    | 'PIP'   // Preliminary Info Parts (prefix-based)
    | 'SB'    // Service Bulletin (summary-based)
    | 'TB'    // Technical Bulletin (summary-based)
    | 'IB'    // Informational Bulletin (summary-based)
    | 'SU'    // Service Update (summary-based)
    | 'WA'    // Warranty Administration (summary-based)
    | 'CSP'   // Customer Satisfaction Program (summary-based)
    | 'RC'    // Recall/Campaign (summary-based)
    | 'SC'    // Special Coverage (summary-based)
    | 'NA'    // NA Bulletin (XX-NA-XXX format) - catchall before OTHER
    | 'OTHER'; // Uncategorized

export const COMM_TYPE_LABELS: Record<CommType, string> = {
    TSB: 'Technical Service Bulletin',
    PIT: 'Preliminary Info Technical',
    PIC: 'Preliminary Info Customer',
    PIP: 'Preliminary Info Parts',
    SB: 'Service Bulletin',
    TB: 'Technical Bulletin',
    IB: 'Informational Bulletin',
    SU: 'Service Update',
    WA: 'Warranty Administration',
    CSP: 'Customer Satisfaction',
    RC: 'Recall/Campaign',
    SC: 'Special Coverage',
    NA: 'NA Bulletin',
    OTHER: 'Other',
};

export const COMM_TYPE_COLORS: Record<CommType, string> = {
    TSB: 'hsl(38, 92%, 50%)',       // Orange - High priority
    PIT: 'hsl(210, 100%, 56%)',     // Blue - Technical
    PIC: 'hsl(162, 73%, 46%)',      // Teal - Customer
    PIP: 'hsl(280, 65%, 60%)',      // Purple - Parts
    SB: 'hsl(45, 93%, 47%)',        // Gold - Service
    TB: 'hsl(200, 80%, 50%)',       // Cyan - Technical
    IB: 'hsl(170, 60%, 45%)',       // Sea green - Informational
    SU: 'hsl(25, 85%, 55%)',        // Coral - Service Update
    WA: 'hsl(190, 70%, 45%)',       // Dark cyan - Warranty
    CSP: 'hsl(340, 75%, 55%)',      // Pink - Customer Satisfaction
    RC: 'hsl(0, 85%, 55%)',         // Red - Recalls (important!)
    SC: 'hsl(270, 60%, 55%)',       // Violet - Special Coverage
    NA: 'hsl(220, 50%, 55%)',       // Steel blue - NA bulletins
    OTHER: 'hsl(215, 15%, 50%)',    // Gray
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
    commTypes?: CommType[];  // Changed to array for multi-select
    page?: number;
    perPage?: number;
}

