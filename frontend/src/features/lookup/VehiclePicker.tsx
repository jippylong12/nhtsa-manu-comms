/* Year -> make -> model picker, controlled by the landing page (URL-backed). Each field is type-to-search. */

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
}

export function VehiclePicker({ value, onChange }: VehiclePickerProps) {
    const { year, make, model } = value;
    const yearsQuery = useDiscoveryYears();
    const makesQuery = useDiscoveryMakes(year);
    const modelsQuery = useDiscoveryModels(year, make);

    return (
        <div className={styles.picker}>
            <Combobox
                label="Year"
                placeholder="Year"
                numeric
                options={(yearsQuery.data ?? []).map(String)}
                value={year ? String(year) : ''}
                loading={yearsQuery.isLoading}
                onChange={(next) => onChange({ year: next ? Number(next) : null, make: '', model: '' })}
            />
            <Combobox
                label="Make"
                placeholder={year ? 'Type a make' : 'Pick a year first'}
                options={makesQuery.data ?? []}
                format={displayName}
                value={make}
                disabled={!year}
                loading={!!year && makesQuery.isLoading}
                onChange={(next) => onChange({ year, make: next, model: '' })}
            />
            <div className={styles.model}>
                <Combobox
                    label="Model"
                    placeholder={make ? 'Type a model' : 'Pick a make first'}
                    options={modelsQuery.data ?? []}
                    format={displayName}
                    value={model}
                    disabled={!make}
                    loading={!!make && modelsQuery.isLoading}
                    onChange={(next) => onChange({ year, make, model: next })}
                />
            </div>
        </div>
    );
}
