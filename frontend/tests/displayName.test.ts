/* Run: npm test */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { displayName, displayVehicle } from '../src/features/vehicles/displayName.ts';

test('ordinary names are title-cased', () => {
    assert.equal(displayName('CHEVROLET'), 'Chevrolet');
    assert.equal(displayName('GRAND CHEROKEE'), 'Grand Cherokee');
    assert.equal(displayName('LAND ROVER'), 'Land Rover');
    assert.equal(displayName('KIA'), 'Kia');
    assert.equal(displayName('RAM'), 'Ram');
});

test('hyphenated names keep each part capitalised', () => {
    assert.equal(displayName('MERCEDES-BENZ'), 'Mercedes-Benz');
    assert.equal(displayName('HARLEY-DAVIDSON'), 'Harley-Davidson');
});

test('codes, badges and initialisms stay in capitals', () => {
    assert.equal(displayName('GMC'), 'GMC');
    assert.equal(displayName('BMW'), 'BMW');
    assert.equal(displayName('SILVERADO EV'), 'Silverado EV');
    assert.equal(displayName('ESCALADE ESV'), 'Escalade ESV');
    assert.equal(displayName('YUKON XL'), 'Yukon XL');
    assert.equal(displayName('RS Q8'), 'RS Q8');
    assert.equal(displayName('F 150'), 'F 150');
    assert.equal(displayName('CR-V'), 'CR-V');
    assert.equal(displayName('911 GT3'), '911 GT3');
    assert.equal(displayName('TAYCAN 4S'), 'Taycan 4S');
    assert.equal(displayName('GTI'), 'GTI');
});

test('a word with a number on the end keeps the word readable', () => {
    assert.equal(displayName('MAZDA3'), 'Mazda3');
    assert.equal(displayName('RAV4'), 'RAV4');
    assert.equal(displayName('CT5'), 'CT5');
});

test('names with unusual capitals are spelled the way the maker does', () => {
    assert.equal(displayName('MCLAREN'), 'McLaren');
});

test('empty input is an empty string, and a vehicle joins its parts', () => {
    assert.equal(displayName(''), '');
    assert.equal(displayName(null), '');
    assert.equal(
        displayVehicle({ year: 2024, make: 'CHEVROLET', model: 'SILVERADO EV' }),
        '2024 Chevrolet Silverado EV'
    );
});
