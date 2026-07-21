/* Add Vehicle Modal Component - Full Discovery Flow */

import { useState, useEffect } from 'react';
import { X, Plus, Car, Check, Loader2, AlertCircle } from 'lucide-react';
import type { VehicleVariant } from '@/client';
import {
    useDiscoveryYears,
    useDiscoveryMakes,
    useDiscoveryModels,
    useDiscoveryVariants,
} from '../../communications/hooks/useDiscovery';
import styles from './AddVehicleModal.module.css';

interface Props {
    isOpen: boolean;
    onClose: () => void;
    onSubmit: (data: { vehicleId: number; year: string; model: string; keywords: string[] }) => void;
    isLoading?: boolean;
}

export function AddVehicleModal({ isOpen, onClose, onSubmit, isLoading }: Props) {
    const [selectedYear, setSelectedYear] = useState<number | null>(null);
    const [selectedMake, setSelectedMake] = useState<string>('');
    const [selectedModel, setSelectedModel] = useState<string>('');
    const [selectedVariant, setSelectedVariant] = useState<VehicleVariant | null>(null);
    const [keywords, setKeywords] = useState('');

    const { data: years, isLoading: loadingYears } = useDiscoveryYears();
    const { data: makes, isLoading: loadingMakes } = useDiscoveryMakes(selectedYear);
    const { data: models, isLoading: loadingModels } = useDiscoveryModels(selectedYear, selectedMake);
    const {
        data: variants,
        isLoading: loadingVariants,
        isError,
        isPaused,
    } = useDiscoveryVariants(selectedYear, selectedMake, selectedModel);

    // A failed lookup does not always land in `isError`. When React Query decides
    // the browser is offline it parks the query at fetchStatus "paused" with
    // status still "pending", so isError and isLoading are both false and the
    // modal would render nothing at all. Treat both as "could not resolve".
    const variantsFailed = isError || isPaused;

    useEffect(() => {
        // Sync the selection to freshly loaded variant data. Manufacturer
        // communications are identical across every variant of a year/make/model
        // (verified against the NHTSA API), so the first one is always taken and
        // the user is never asked to pick a trim. This is a legitimate "sync to
        // async query result" effect; the set-state-in-effect rule is a false
        // positive here.
        if (!variants) return;
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setSelectedVariant(variants.length > 0 ? variants[0] : null);
    }, [variants]);

    if (!isOpen) return null;

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!selectedVariant) return;

        const keywordList = keywords
            .split(',')
            .map((k) => k.trim())
            .filter(Boolean);

        onSubmit({
            vehicleId: selectedVariant.vehicleId,
            year: String(selectedVariant.modelYear),
            model: selectedVariant.model,
            keywords: keywordList,
        });
    };

    const resetSelections = () => {
        setSelectedYear(null);
        setSelectedMake('');
        setSelectedModel('');
        setSelectedVariant(null);
        setKeywords('');
    };

    const handleClose = () => {
        resetSelections();
        onClose();
    };

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
                                    setSelectedVariant(null);
                                }}
                                disabled={loadingYears}
                            >
                                <option value="">Select Year</option>
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
                                    setSelectedVariant(null);
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
                                    setSelectedVariant(null);
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

                        {/* Resolving the NHTSA vehicleId for the chosen year/make/model */}
                        {selectedModel && loadingVariants && (
                            <div className={styles.loadingVariants} role="status">
                                <Loader2 size={20} className={styles.spinner} />
                                <span>Finding this vehicle...</span>
                            </div>
                        )}

                        {/* The lookup itself failed. Without this the modal would
                            sit blank, since there is no picker left to fall back on. */}
                        {selectedModel && !loadingVariants && variantsFailed && (
                            <div className={styles.vehicleNotFound} role="alert">
                                <AlertCircle size={18} />
                                <div>
                                    <strong>Could not look up this vehicle</strong>
                                    <span className={styles.variantDetails}>
                                        Reselect the model to try again.
                                    </span>
                                </div>
                            </div>
                        )}

                        {/* No NHTSA record for this combination */}
                        {selectedModel && !loadingVariants && !variantsFailed && variants?.length === 0 && (
                            <div className={styles.vehicleNotFound} role="alert">
                                <AlertCircle size={18} />
                                <div>
                                    <strong>No NHTSA record for this vehicle</strong>
                                    <span className={styles.variantDetails}>
                                        Try a different model or year.
                                    </span>
                                </div>
                            </div>
                        )}

                        {/* Success confirmation */}
                        {selectedVariant && (
                            <div className={styles.vehicleConfirmed}>
                                <Check size={18} />
                                <div>
                                    <strong>
                                        {selectedVariant.modelYear} {selectedVariant.make} {selectedVariant.model}
                                    </strong>
                                    <span className={styles.variantDetails}>
                                        ID: {selectedVariant.vehicleId}
                                    </span>
                                </div>
                            </div>
                        )}

                        {/* Keywords */}
                        <div className={`input-group ${!selectedVariant ? styles.disabled : ''}`}>
                            <label className="input-label">Filter Keywords (optional)</label>
                            <input
                                type="text"
                                className="input"
                                placeholder="software, update, recall (comma-separated)"
                                value={keywords}
                                onChange={(e) => setKeywords(e.target.value)}
                                disabled={!selectedVariant}
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
                            disabled={isLoading || !selectedVariant}
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
