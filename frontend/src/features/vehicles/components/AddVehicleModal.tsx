/* Add Vehicle Modal Component - year / make / model nameplate picker */

import { useState, useEffect } from 'react';
import { X, Plus, Car, Check, AlertCircle } from 'lucide-react';
import type { VehicleCreate } from '@/client';
import {
    useDiscoveryYears,
    useDiscoveryMakes,
    useDiscoveryModels,
} from '../../communications/hooks/useDiscovery';
import styles from './AddVehicleModal.module.css';

interface Props {
    isOpen: boolean;
    onClose: () => void;
    onSubmit: (data: VehicleCreate) => void;
    isLoading?: boolean;
}

export function AddVehicleModal({ isOpen, onClose, onSubmit, isLoading }: Props) {
    const [selectedYear, setSelectedYear] = useState<number | null>(null);
    const [selectedMake, setSelectedMake] = useState<string>('');
    const [selectedModel, setSelectedModel] = useState<string>('');
    const [keywords, setKeywords] = useState('');

    const yearsQuery = useDiscoveryYears();
    const makesQuery = useDiscoveryMakes(selectedYear);
    const modelsQuery = useDiscoveryModels(selectedYear, selectedMake);
    const { data: years, isLoading: loadingYears } = yearsQuery;
    const { data: makes, isLoading: loadingMakes } = makesQuery;
    const { data: models, isLoading: loadingModels } = modelsQuery;

    const ready = !!selectedYear && !!selectedMake && !!selectedModel;
    const failedQuery = [yearsQuery, makesQuery, modelsQuery].find((q) => q.isError);
    const noModels =
        !!selectedMake && !loadingModels && !modelsQuery.isError && models?.length === 0;

    useEffect(() => {
        // Clear the form whenever the modal closes. A successful add closes it by
        // flipping isOpen rather than going through handleClose, and the component
        // stays mounted while hidden, so without this the next open would show the
        // vehicle that was just added with the Add button still enabled, one stray
        // click from submitting it again.
        if (isOpen) return;
        /* eslint-disable react-hooks/set-state-in-effect */
        setSelectedYear(null);
        setSelectedMake('');
        setSelectedModel('');
        setKeywords('');
        /* eslint-enable react-hooks/set-state-in-effect */
    }, [isOpen]);

    if (!isOpen) return null;

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!ready) return;

        const keywordList = keywords
            .split(',')
            .map((k) => k.trim())
            .filter(Boolean);

        onSubmit({
            year: String(selectedYear),
            make: selectedMake,
            model: selectedModel,
            keywords: keywordList,
        });
    };

    // Closing is enough to clear the form; the effect above owns the reset so
    // that every exit path, including a successful add, goes through it.
    const handleClose = () => onClose();

    return (
        <div className={styles.modalOverlay} onClick={handleClose}>
            <div className={`${styles.modal} animate-slide-up`} onClick={(e) => e.stopPropagation()}>
                <div className={styles.modalHeader}>
                    <div className={styles.modalIcon}>
                        <Car size={24} />
                    </div>
                    <h2>Add Vehicle</h2>
                    <button className="btn btn-ghost btn-icon" onClick={handleClose}>
                        <X size={20} />
                    </button>
                </div>

                <form onSubmit={handleSubmit}>
                    <div className={styles.modalBody}>
                        {/* Step 1: Year */}
                        <div className="input-group">
                            <label className="input-label">Model Year</label>
                            <select
                                className={`input ${styles.select}`}
                                value={selectedYear || ''}
                                onChange={(e) => {
                                    setSelectedYear(Number(e.target.value));
                                    setSelectedMake('');
                                    setSelectedModel('');
                                                            }}
                                disabled={loadingYears}
                            >
                                <option value="">
                                    {loadingYears ? 'Loading years...' : 'Select Year'}
                                </option>
                                {years?.map((y) => (
                                    <option key={y} value={y}>{y}</option>
                                ))}
                            </select>
                        </div>

                        {/* Step 2: Make */}
                        <div className={`input-group ${!selectedYear ? styles.disabled : ''}`}>
                            <label className="input-label">Make</label>
                            <select
                                className={`input ${styles.select}`}
                                value={selectedMake}
                                onChange={(e) => {
                                    setSelectedMake(e.target.value);
                                    setSelectedModel('');
                                                            }}
                                disabled={!selectedYear || loadingMakes}
                            >
                                <option value="">
                                    {loadingMakes ? 'Loading makes...' : 'Select Make'}
                                </option>
                                {makes?.map((m) => (
                                    <option key={m} value={m}>{m}</option>
                                ))}
                            </select>
                        </div>

                        {/* Step 3: Model */}
                        <div className={`input-group ${!selectedMake ? styles.disabled : ''}`}>
                            <label className="input-label">Model</label>
                            <select
                                className={`input ${styles.select}`}
                                value={selectedModel}
                                onChange={(e) => {
                                    setSelectedModel(e.target.value);
                                                            }}
                                disabled={!selectedMake || loadingModels}
                            >
                                <option value="">
                                    {loadingModels ? 'Loading models...' : 'Select Model'}
                                </option>
                                {models?.map((m) => (
                                    <option key={m} value={m}>{m}</option>
                                ))}
                            </select>
                        </div>

                        {/* A discovery request failed (our API, not NHTSA) */}
                        {failedQuery && (
                            <div className={styles.vehicleNotFound} role="alert">
                                <AlertCircle size={18} />
                                <div>
                                    <strong>Couldn't load the picker</strong>
                                    <span className={styles.variantDetails}>
                                        Check your connection and try again.
                                    </span>
                                    <button
                                        type="button"
                                        className={`btn btn-secondary ${styles.retryButton}`}
                                        onClick={() => void failedQuery.refetch()}
                                    >
                                        Retry
                                    </button>
                                </div>
                            </div>
                        )}

                        {/* No bulletins for this make in this year */}
                        {noModels && (
                            <div className={styles.vehicleNotFound} role="alert">
                                <AlertCircle size={18} />
                                <div>
                                    <strong>
                                        No models with bulletins for {selectedMake} in {selectedYear}
                                    </strong>
                                    <span className={styles.variantDetails}>
                                        Pick another make or year.
                                    </span>
                                </div>
                            </div>
                        )}

                        {/* Success confirmation */}
                        {ready && (
                            <div className={styles.vehicleConfirmed}>
                                <Check size={18} />
                                <div>
                                    <strong>
                                        {selectedYear} {selectedMake} {selectedModel}
                                    </strong>
                                    <span className={styles.variantDetails}>
                                        Tracks every bulletin NHTSA files under this nameplate
                                    </span>
                                </div>
                            </div>
                        )}

                        {/* Keywords */}
                        <div className={`input-group ${!selectedModel ? styles.disabled : ''}`}>
                            <label className="input-label">Filter Keywords (optional)</label>
                            <input
                                type="text"
                                className="input"
                                placeholder="software, update, recall (comma-separated)"
                                value={keywords}
                                onChange={(e) => setKeywords(e.target.value)}
                                disabled={!selectedModel}
                            />
                            <span className={styles.inputHint}>
                                Only show communications containing these keywords
                            </span>
                        </div>
                    </div>

                    <div className={styles.modalFooter}>
                        <button type="button" className="btn btn-secondary" onClick={handleClose}>
                            Cancel
                        </button>
                        <button
                            type="submit"
                            className="btn btn-primary"
                            disabled={isLoading || !ready}
                        >
                            <Plus size={18} />
                            {isLoading ? 'Adding...' : 'Add Vehicle'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
