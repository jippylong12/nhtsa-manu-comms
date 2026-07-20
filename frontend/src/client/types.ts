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
    | 'BL'    // Generic bulletin (document-type based)
    | 'OL'    // Owner Letter (document-type based)
    | 'DL'    // Dealer Letter (document-type based)
    | 'MC'    // Manufacturer Communication (generic catch-all)
    | 'NA'    // NA Bulletin (XX-NA-XXX format) - catchall before OTHER
    | 'OTHER'; // Uncategorized

// Priority levels
export type CommPriority = 'high' | 'medium' | 'low';

// Priority groupings
export const COMM_PRIORITY_TYPES: Record<CommPriority, CommType[]> = {
    high: ['TSB', 'CSP', 'RC'],           // Safety critical & important
    medium: ['PIT', 'PIC', 'SB', 'TB', 'SU', 'WA', 'SC'],  // Service & technical
    low: ['PIP', 'IB', 'BL', 'OL', 'DL', 'MC', 'NA', 'OTHER'],    // Informational
};

// Standard priority colors (red, yellow, green)
export const PRIORITY_COLORS: Record<CommPriority, string> = {
    high: 'hsl(0, 75%, 55%)',      // Red
    medium: 'hsl(45, 90%, 50%)',   // Yellow/Gold
    low: 'hsl(145, 60%, 45%)',     // Green
};

// Get priority for a comm type
export const getCommPriority = (type: CommType): CommPriority => {
    if (COMM_PRIORITY_TYPES.high.includes(type)) return 'high';
    if (COMM_PRIORITY_TYPES.medium.includes(type)) return 'medium';
    return 'low';
};

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
    BL: 'Bulletin',
    OL: 'Owner Letter',
    DL: 'Dealer Letter',
    MC: 'Manufacturer Communication',
    NA: 'NA Bulletin',
    OTHER: 'Other',
};

// Colors reflect priority: reds for high, oranges/yellows for medium, greens/blues for low
// Each type within a priority has a distinct shade
export const COMM_TYPE_COLORS: Record<CommType, string> = {
    // HIGH PRIORITY - Red shades
    RC: 'hsl(0, 80%, 50%)',          // Bright red - Recalls (most critical)
    TSB: 'hsl(15, 85%, 50%)',        // Red-orange - Technical bulletins
    CSP: 'hsl(350, 75%, 55%)',       // Rose red - Customer satisfaction

    // MEDIUM PRIORITY - Orange/Yellow shades
    PIT: 'hsl(35, 90%, 50%)',        // Orange - Preliminary technical
    PIC: 'hsl(45, 95%, 48%)',        // Gold - Preliminary customer
    SB: 'hsl(40, 85%, 52%)',         // Amber - Service bulletin
    TB: 'hsl(30, 80%, 55%)',         // Light orange - Technical bulletin
    SU: 'hsl(50, 90%, 45%)',         // Yellow-gold - Service update
    WA: 'hsl(25, 75%, 55%)',         // Burnt orange - Warranty
    SC: 'hsl(55, 85%, 45%)',         // Yellow - Special coverage

    // LOW PRIORITY - Green/Blue shades
    PIP: 'hsl(145, 55%, 48%)',       // Green - Parts info
    IB: 'hsl(160, 50%, 45%)',        // Teal-green - Informational
    BL: 'hsl(170, 45%, 46%)',        // Teal - Generic bulletin
    OL: 'hsl(190, 45%, 48%)',        // Sky - Owner letter
    DL: 'hsl(210, 45%, 52%)',        // Blue - Dealer letter
    MC: 'hsl(200, 30%, 52%)',        // Slate-blue - Manufacturer comm
    NA: 'hsl(180, 45%, 42%)',        // Cyan - NA bulletins
    OTHER: 'hsl(200, 25%, 50%)',     // Gray-blue - Other
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

// Shape returned by GET /communications/discovery/variants. The backend's
// get_vehicle_variants builds these camelCase keys directly; the previous
// PascalCase declaration ({VehicleId, VehicleDescription}) never matched the
// wire format, which forced the unsound casts in AddVehicleModal and broke the
// production build.
export interface VehicleVariant {
    vehicleId: number;
    ncapId: number;
    modelYear: number;
    make: string;
    model: string;
    trim: string;
    series: string;
    vehicleDescription: string;
}

// --- Corpus (Postgres-backed processed pipeline) ---

export type ProcessingStatus = 'pending' | 'processed' | 'failed';

export interface CorpusVehicle {
    nhtsaVehicleId: number;
    year: number;
    make: string;
    model: string;
    trim?: string | null;
}

export interface CorpusDocument {
    id: number;
    url: string;
    docSummary?: string | null;
    extractionMethod?: string | null;
    pageCount?: number | null;
    llmSummary?: string | null;
    docKind?: string | null;
    symptoms: string[];
    systems: string[];
    components: string[];
    remedy?: string | null;
    applicability?: string | null;
    hasEmbedding: boolean;
}

export interface CorpusCommunicationSummary {
    nhtsaId: string;
    communicationNumber?: string | null;
    communicationType?: CommType | null;
    communicationDate?: string | null;
    summary: string;
    status: ProcessingStatus;
    documentCount: number;
    llmSummary?: string | null;
    symptoms: string[];
    systems: string[];
    vehicles: CorpusVehicle[];
}

export interface CorpusCommunicationDetail {
    nhtsaId: string;
    communicationNumber?: string | null;
    communicationType?: CommType | null;
    communicationDate?: string | null;
    summary: string;
    detailsSummary?: string | null;
    status: ProcessingStatus;
    statusReason?: string | null;
    processedAt?: string | null;
    documents: CorpusDocument[];
    vehicles: CorpusVehicle[];
}

export interface CorpusListResponse {
    items: CorpusCommunicationSummary[];
    total: number;
    page: number;
    perPage: number;
}

export interface TagCount {
    tag: string;
    count: number;
}

export interface TagVocabulary {
    systems: TagCount[];
    components: TagCount[];
}

export interface CorpusFilters {
    [key: string]: unknown;
    vehicleId?: number;
    commType?: CommType;
    status?: ProcessingStatus;
    dateFrom?: string;
    dateTo?: string;
    systems?: string[];
    components?: string[];
    search?: string;
    page?: number;
    perPage?: number;
}

