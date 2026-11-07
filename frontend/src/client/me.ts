/* Me API - who is signed in */

import { request } from './api';
import type { Me } from './types';

export const meApi = {
    /** GET /api/me; 401 (ApiError) when nobody is signed in. */
    get: () => request<Me>('/me'),
};
