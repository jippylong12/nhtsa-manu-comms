/* Fixture bulletins for the row smoke and the list: one of each shape the row renders. */

import type { MyBulletin } from '@/client';

export const fullUnread: MyBulletin = {
    nhtsaId: '10250001',
    communicationNumber: 'SB-25-001',
    communicationType: 'SB',
    communicationDate: '2025-03-04',
    severity: 'medium',
    locked: false,
    read: false,
    summary: 'Technical service bulletin about transmission shift quality.',
    llmSummary:
        'Some vehicles may shift harshly or hesitate between second and third gear; a software update to the transmission control module corrects it at no cost.',
    symptoms: ['Harsh shifting', 'Hesitation on acceleration'],
    systems: ['Transmission', 'Powertrain', 'Software'],
};

export const fullReadNhtsaSummaryOnly: MyBulletin = {
    nhtsaId: '10250002',
    communicationNumber: 'TSB-24-017',
    communicationType: 'TSB',
    communicationDate: '2024-11-18',
    severity: 'serious',
    locked: false,
    read: true,
    summary: 'Inspect the front brake caliper bolts for proper torque and replace if corroded.',
    llmSummary: null,
    symptoms: null,
    systems: null,
};

export const lockedUnread: MyBulletin = {
    nhtsaId: '10250003',
    communicationType: 'IB',
    communicationDate: '2023-06-09',
    severity: 'small',
    locked: true,
    read: false,
};

export const dateNull: MyBulletin = {
    nhtsaId: '10250004',
    communicationNumber: 'MC-10',
    communicationType: 'MC',
    communicationDate: null,
    severity: 'small',
    locked: false,
    read: false,
    summary: 'Manufacturer notice with no recorded date.',
    llmSummary: null,
};

export const BULLETIN_FIXTURES: MyBulletin[] = [fullUnread, fullReadNhtsaSummaryOnly, lockedUnread, dateNull];
