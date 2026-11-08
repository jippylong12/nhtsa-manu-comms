/* Year -> make -> model picker, controlled by the landing page (URL-backed) */

import {
    useDiscoveryMakes,
    useDiscoveryModels,
    useDiscoveryYears,
} from '@/features/communications/hooks/useDiscovery';
import modalStyles from '@/features/vehicles/components/AddVehicleModal.module.css';
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
    const selectClass = `input ${modalStyles.select}`;

    return (
        <div className={styles.picker}>
            <div className="input-group">
                <label className="input-label" htmlFor="pick-year">Model year</label>
                <select
                    id="pick-year"
                    className={selectClass}
                    value={year ?? ''}
                    disabled={yearsQuery.isLoading}
                    onChange={(e) =>
                        onChange({ year: e.target.value ? Number(e.target.value) : null, make: '', model: '' })
                    }
                >
                    <option value="">{yearsQuery.isLoading ? 'Loading years...' : 'Select year'}</option>
                    {yearsQuery.data?.map((y) => (
                        <option key={y} value={y}>{y}</option>
                    ))}
                </select>
            </div>
            <div className="input-group">
                <label className="input-label" htmlFor="pick-make">Make</label>
                <select
                    id="pick-make"
                    className={selectClass}
                    value={make}
                    disabled={!year || makesQuery.isLoading}
                    onChange={(e) => onChange({ year, make: e.target.value, model: '' })}
                >
                    <option value="">{makesQuery.isLoading && year ? 'Loading makes...' : 'Select make'}</option>
                    {makesQuery.data?.map((m) => (
                        <option key={m} value={m}>{m}</option>
                    ))}
                </select>
            </div>
            <div className="input-group">
                <label className="input-label" htmlFor="pick-model">Model</label>
                <select
                    id="pick-model"
                    className={selectClass}
                    value={model}
                    disabled={!make || modelsQuery.isLoading}
                    onChange={(e) => onChange({ year, make, model: e.target.value })}
                >
                    <option value="">{modelsQuery.isLoading && make ? 'Loading models...' : 'Select model'}</option>
                    {modelsQuery.data?.map((m) => (
                        <option key={m} value={m}>{m}</option>
                    ))}
                </select>
            </div>
        </div>
    );
}
