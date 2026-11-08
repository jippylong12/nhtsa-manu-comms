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
    id: string;
    vehicleId: number;
    year: string;
    model: string;
    make?: string | null;
    keywords: string[];
    lastFetched: string | null;
    /** Communications in the corpus for this vehicle, derived on read. */
    commCount: number;
    createdAt: string;
    updatedAt: string;
}

export interface VehicleCreate {
    year: string;
    make: string;
    model: string;
    keywords?: string[];
}

export interface VehicleUpdate {
    keywords?: string[];
}

export interface VehicleListResponse {
    items: Vehicle[];
    total: number;
    page: number;
    perPage: number;
}

// The Mongo-era Communication, VehicleStats, FetchRequest, FetchProgress and
// FetchResult models lived here. Reading communications is CorpusCommunication
// below; running a fetch is RefreshRun. See JIP-417.

// --- Refresh (runs the pipeline for one vehicle) ---

/** What a refresh would do. Asking costs nothing. */
export interface RefreshPreview {
    vehicleId: number;
    listedByNhtsa: number;
    newBulletins: number;
    /** Already in the corpus for this vehicle but never analysed. */
    unfinishedDocuments: number;
    documentsToProcess: number;
    upToDate: boolean;
    /** Never scanned: the first refresh scans NHTSA's files before it can count bulletins. */
    scanPending: boolean;
    estimatedCostLowUsd: number;
    estimatedCostHighUsd: number;
    /** Ready to show, e.g. "about $0.03". */
    estimatedCostText: string;
    estimatedSeconds: number;
    /** Ready to show, e.g. "around 4 minutes". */
    estimatedDurationText: string;
    costPerDocumentUsd: number;
}

export type RefreshStatus = 'queued' | 'running' | 'succeeded' | 'failed';

/** The stages, in the order they run. Named for the user, not the pipeline. */
export type RefreshStage = 'sync' | 'read' | 'analyse' | 'index' | 'finalise';

export const REFRESH_STAGES: { key: RefreshStage; label: string }[] = [
    { key: 'sync', label: 'Fetching from NHTSA' },
    { key: 'read', label: 'Reading documents' },
    { key: 'analyse', label: 'Analysing' },
    { key: 'index', label: 'Indexing' },
    { key: 'finalise', label: 'Finishing up' },
];

export interface RefreshRun {
    id: number;
    vehicleId: number;
    status: RefreshStatus;
    stage: RefreshStage | null;
    stageNote: string | null;
    done: number;
    total: number;
    expectedDocuments: number;
    estimatedCostUsd: number;
    /** Metered spend for this run, set when it finishes. */
    actualCostUsd: number | null;
    counts: Record<string, unknown>;
    /** Why it failed, or what it could not finish on a partial success. */
    error: string | null;
    startedAt: string;
    heartbeatAt: string;
    finishedAt: string | null;
}

export const isRefreshActive = (run: RefreshRun | null | undefined): boolean =>
    run?.status === 'queued' || run?.status === 'running';

// --- Corpus (Postgres-backed processed pipeline) ---

export type ProcessingStatus = 'pending' | 'processed' | 'failed';

export interface CorpusVehicle {
    vehicleId: number;
    year: number;
    make: string;
    model: string;
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
    applicabilityYears: number[];
    applicabilityModels: string[];
    applicabilityConditions?: string | null;
    audience?: 'owner' | 'dealer' | 'both' | null;
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
    // Optional here: SymptomSearchView builds summaries without severity.
    severity?: 'serious' | 'medium' | 'small';
    severitySource?: 'type' | 'classifier';
    supersededCount: number;
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
    severity: 'serious' | 'medium' | 'small';
    severitySource: 'type' | 'classifier';
    supersededCount: number;
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

// --- Symptom Search (Phase B) ---

export interface SymptomSearchRequest {
    vehicleId: number;
    description: string;
}

export interface SymptomMatch {
    nhtsaId: string;
    communicationNumber?: string | null;
    communicationType?: CommType | null;
    communicationDate?: string | null;
    llmSummary?: string | null;
    symptoms: string[];
    systems: string[];
    components: string[];
    remedy?: string | null;
    relevanceScore: number;
    cosineSim?: number | null;
    // True when the composed answer cited this communication.
    cited: boolean;
    // Older revisions of this bulletin folded into this result.
    supersededCount: number;
}

export interface QueryCost {
    embedTokens: number;
    embedCostUsd: number;
    llmModel: string;
    llmTokensIn: number;
    llmTokensOut: number;
    llmCostUsd: number;
    totalUsd: number;
}

export interface SymptomSearchResponse {
    answer: string;
    refused: boolean;
    confidence: number;
    model: string;
    // False when retrieval succeeded but answer composition could not run.
    answerGenerated: boolean;
    matches: SymptomMatch[];
    citedIds: string[];
    cost: QueryCost;
}


// --- Me ---

export interface Entitlement {
    kind: 'unlock' | 'subscription';
    vehicleId: number | null;
    status: 'active' | 'canceled' | 'past_due';
    currentPeriodEnd: string | null;
}

export interface Me {
    id: number;
    email: string;
    displayName: string | null;
    isAdmin: boolean;
    entitlements: Entitlement[];
    vehicles: MyVehicle[];
}

// A vehicle on the signed-in user's account (GET /api/me, POST /api/me/vehicles)
export type MyVehicleAvailability = 'ready' | 'processing';
export type MyVehicleAccess = 'full' | 'sample';
export type Severity = 'serious' | 'medium' | 'small';

export interface SeverityCounts {
    serious: number;
    medium: number;
    small: number;
}

export interface MyVehicle {
    vehicleId: number;
    year: number;
    make: string;
    model: string;
    availability: MyVehicleAvailability;
    counts: SeverityCounts;
    access: MyVehicleAccess;
    addedAt: string;
}

export interface MyVehicleCreate {
    year: number;
    make: string;
    model: string;
}

// One folded bulletin. A locked row carries only the identity fields; every
// other key is absent from the wire.
export interface MyBulletin {
    nhtsaId: string;
    communicationNumber?: string | null;
    communicationType?: string | null;
    communicationDate?: string | null;
    severity: Severity;
    supersededCount?: number;
    locked: boolean;
    summary?: string | null;
    llmSummary?: string | null;
    symptoms?: string[] | null;
    systems?: string[] | null;
    components?: string[] | null;
    remedy?: string | null;
    applicability?: string | null;
    applicabilityYears?: number[] | null;
    applicabilityModels?: string[] | null;
    applicabilityConditions?: string | null;
    audience?: string | null;
    documents?: { url: string }[] | null;
}

export interface MyBulletinsParams {
    severity?: Severity;
    search?: string;
    page?: number;
    perPage?: number;
}

export interface MyBulletinsResponse {
    items: MyBulletin[];
    total: number;
    page: number;
    perPage: number;
    access: MyVehicleAccess;
    sampleCounts: SeverityCounts;
}

// Public lookup (GET /api/public/lookup)
export type LookupAvailability = 'ready' | 'processing' | 'unknown';

export interface LookupCounts {
    serious: number;
    medium: number;
    small: number;
}

export interface LookupExample {
    vehicleId: number | null;
    year: number;
    make: string;
    model: string;
    counts: LookupCounts;
    latestDate: string | null;
}

export interface LookupResponse {
    vehicleId: number | null;
    year: number;
    make: string;
    model: string;
    availability: LookupAvailability;
    counts: LookupCounts;
    latestDate: string | null;
    example: LookupExample | null;
}
