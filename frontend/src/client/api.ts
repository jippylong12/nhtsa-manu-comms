/* API Client - HTTP utilities for backend communication */

const API_BASE = '/api';

class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
    }
}

async function handleResponse<T>(response: Response): Promise<T> {
    if (!response.ok) {
        let message = `HTTP ${response.status}`;
        try {
            const error = await response.json();
            message = error.detail || error.message || message;
        } catch {
            // Ignore JSON parse error
        }
        throw new ApiError(response.status, message);
    }
    // Handle empty response (e.g., 204 No Content from DELETE)
    const text = await response.text();
    if (!text) {
        return undefined as T;
    }
    return JSON.parse(text);
}

async function request<T>(
    endpoint: string,
    options: RequestInit = {}
): Promise<T> {
    const url = `${API_BASE}${endpoint}`;
    const response = await fetch(url, {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers,
        },
        ...options,
    });
    return handleResponse<T>(response);
}

// Export utilities
export { ApiError, request, API_BASE };
