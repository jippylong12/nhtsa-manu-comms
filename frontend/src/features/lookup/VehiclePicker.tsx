/* Year -> make -> model picker, controlled by the landing page (URL-backed). Each field is
   type-to-search, numbered, and hands focus to the next one as soon as it is filled. */

import { useEffect, useRef, type RefObject } from 'react';

import {
    useDiscoveryMakes,
    useDiscoveryModels,
    useDiscoveryYears,
} from '@/features/communications/hooks/useDiscovery';
import { displayName } from '@/features/vehicles/displayName';
import { Combobox } from './Combobox';
import styles from './VehiclePicker.module.css';

export interface PickerValue {
    year: number | null;
    make: string;
    model: string;
}

interface VehiclePickerProps {
    value: PickerValue;
    onChange: (next: PickerValue) => void;
    /** The year field, so the page can send people here ("Check your car"). */
    yearRef?: RefObject<HTMLInputElement | null>;
}

export function VehiclePicker({ value, onChange, yearRef }: VehiclePickerProps) {
    const { year, make, model } = value;
    const yearsQuery = useDiscoveryYears();
    const makesQuery = useDiscoveryMakes(year);
    const modelsQuery = useDiscoveryModels(year, make);
    const makeRef = useRef<HTMLInputElement>(null);
    const modelRef = useRef<HTMLInputElement>(null);
    // Only a choice the visitor just made moves focus; a car restored from the URL does not.
    const picked = useRef<'year' | 'make' | null>(null);

    useEffect(() => {
        if (picked.current === 'year' && year && !make) makeRef.current?.focus();
        if (picked.current === 'make' && make && !model) modelRef.current?.focus();
        picked.current = null;
    }, [year, make, model]);

    return (
        <div className={styles.wrap}>
            <div className={styles.picker}>
                <Combobox
                    step={1}
                    label="Year"
                    placeholder="Year"
                    numeric
                    inputRef={yearRef}
                    options={(yearsQuery.data ?? []).map(String)}
                    value={year ? String(year) : ''}
                    loading={yearsQuery.isLoading}
                    onChange={(next) => {
                        picked.current = next ? 'year' : null;
                        onChange({ year: next ? Number(next) : null, make: '', model: '' });
                    }}
                />
                <Combobox
                    step={2}
                    label="Make"
                    placeholder={year ? 'Type a make' : 'Pick a year first'}
                    inputRef={makeRef}
                    options={makesQuery.data ?? []}
                    format={displayName}
                    value={make}
                    disabled={!year}
                    loading={!!year && makesQuery.isLoading}
                    onChange={(next) => {
                        picked.current = next ? 'make' : null;
                        onChange({ year, make: next, model: '' });
                    }}
                />
                <div className={styles.model}>
                    <Combobox
                        step={3}
                        label="Model"
                        placeholder={make ? 'Type a model' : 'Pick a make first'}
                        inputRef={modelRef}
                        options={modelsQuery.data ?? []}
                        format={displayName}
                        value={model}
                        disabled={!make}
                        loading={!!make && modelsQuery.isLoading}
                        onChange={(next) => onChange({ year, make, model: next })}
                    />
                </div>
            </div>
        </div>
    );
}
