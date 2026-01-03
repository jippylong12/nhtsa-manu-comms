/* Add Vehicle Modal Component - Full Discovery Flow */

import { useState, useEffect } from 'react';
import { X, Plus, Car, Check, Loader2 } from 'lucide-react';
import {
    useDiscoveryYears,
    useDiscoveryMakes,
    useDiscoveryModels,
    useDiscoveryVariants,
} from '../../communications/hooks/useDiscovery';

interface Props {
    isOpen: boolean;
    onClose: () => void;
    onSubmit: (data: { vehicleId: number; year: string; model: string; keywords: string[] }) => void;
    isLoading?: boolean;
}

interface VehicleVariant {
    vehicleId: number;
    ncapId: number;
    modelYear: number;
    make: string;
    model: string;
    trim: string;
    series: string;
    vehicleDescription: string;
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
    const { data: variants, isLoading: loadingVariants } = useDiscoveryVariants(
        selectedYear,
        selectedMake,
        selectedModel
    );

    // Auto-select variant if only one exists
    useEffect(() => {
        if (variants && variants.length === 1) {
            setSelectedVariant(variants[0] as VehicleVariant);
        } else if (variants && variants.length > 1) {
            setSelectedVariant(null); // Reset if multiple
        }
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
        <div className="modal-overlay" onClick={handleClose}>
            <div className="modal animate-slide-up" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <div className="modal-icon">
                        <Car size={24} />
                    </div>
                    <h2>Add Vehicle</h2>
                    <button className="btn btn-ghost btn-icon" onClick={handleClose}>
                        <X size={20} />
                    </button>
                </div>

                <form onSubmit={handleSubmit}>
                    <div className="modal-body">
                        {/* Step 1: Year */}
                        <div className="input-group">
                            <label className="input-label">Model Year</label>
                            <select
                                className="input select"
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
                        <div className={`input-group ${!selectedYear ? 'disabled' : ''}`}>
                            <label className="input-label">Make</label>
                            <select
                                className="input select"
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
                        <div className={`input-group ${!selectedMake ? 'disabled' : ''}`}>
                            <label className="input-label">Model</label>
                            <select
                                className="input select"
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

                        {/* Step 4: Variant Selection (if multiple) */}
                        {selectedModel && variants && variants.length > 1 && (
                            <div className="variant-selection">
                                <label className="input-label">Select Trim/Style</label>
                                <div className="variant-grid">
                                    {(variants as VehicleVariant[]).map((v) => (
                                        <button
                                            key={v.vehicleId}
                                            type="button"
                                            className={`variant-card ${selectedVariant?.vehicleId === v.vehicleId ? 'selected' : ''}`}
                                            onClick={() => setSelectedVariant(v)}
                                        >
                                            <span className="variant-trim">{v.trim}</span>
                                            <span className="variant-series">{v.series}</span>
                                            {selectedVariant?.vehicleId === v.vehicleId && (
                                                <Check size={16} className="variant-check" />
                                            )}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* Loading state for variants */}
                        {selectedModel && loadingVariants && (
                            <div className="loading-variants">
                                <Loader2 size={20} className="spinner" />
                                <span>Finding vehicle variants...</span>
                            </div>
                        )}

                        {/* Success confirmation */}
                        {selectedVariant && (
                            <div className="vehicle-confirmed">
                                <Check size={18} />
                                <div>
                                    <strong>
                                        {selectedVariant.modelYear} {selectedVariant.make} {selectedVariant.model}
                                    </strong>
                                    <span className="variant-details">
                                        {selectedVariant.trim} {selectedVariant.series} · ID: {selectedVariant.vehicleId}
                                    </span>
                                </div>
                            </div>
                        )}

                        {/* Keywords */}
                        <div className={`input-group ${!selectedVariant ? 'disabled' : ''}`}>
                            <label className="input-label">Filter Keywords (optional)</label>
                            <input
                                type="text"
                                className="input"
                                placeholder="software, update, recall (comma-separated)"
                                value={keywords}
                                onChange={(e) => setKeywords(e.target.value)}
                                disabled={!selectedVariant}
                            />
                            <span className="input-hint">
                                Only show communications containing these keywords
                            </span>
                        </div>
                    </div>

                    <div className="modal-footer">
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

                <style>{`
          .modal-overlay {
            position: fixed;
            inset: 0;
            background: hsla(0, 0%, 0%, 0.7);
            backdrop-filter: blur(4px);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            padding: var(--space-lg);
          }

          .modal {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-xl);
            width: 100%;
            max-width: 480px;
            max-height: 90vh;
            overflow: hidden;
            display: flex;
            flex-direction: column;
          }

          .modal-header {
            display: flex;
            align-items: center;
            gap: var(--space-md);
            padding: var(--space-lg);
            border-bottom: 1px solid var(--border-subtle);
          }

          .modal-icon {
            width: 40px;
            height: 40px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-dark) 100%);
            border-radius: var(--radius-md);
            color: white;
          }

          .modal-header h2 {
            flex: 1;
            font-size: 1.25rem;
            margin: 0;
          }

          .modal-body {
            padding: var(--space-lg);
            display: flex;
            flex-direction: column;
            gap: var(--space-lg);
            overflow-y: auto;
          }

          .select {
            appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 0.75rem center;
            background-size: 1.25em;
            padding-right: 2.5rem;
          }

          .variant-selection {
            display: flex;
            flex-direction: column;
            gap: var(--space-sm);
          }

          .variant-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
            gap: var(--space-sm);
          }

          .variant-card {
            position: relative;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: var(--space-xs);
            padding: var(--space-md);
            background: var(--bg-hover);
            border: 2px solid var(--border-subtle);
            border-radius: var(--radius-md);
            cursor: pointer;
            transition: all 0.15s ease;
          }

          .variant-card:hover {
            border-color: var(--color-primary);
            background: var(--bg-active);
          }

          .variant-card.selected {
            border-color: var(--color-primary);
            background: hsla(var(--color-primary-hsl), 0.1);
          }

          .variant-trim {
            font-weight: 600;
            font-size: 0.9rem;
          }

          .variant-series {
            color: var(--text-muted);
            font-size: 0.8rem;
          }

          .variant-check {
            position: absolute;
            top: 6px;
            right: 6px;
            color: var(--color-primary);
          }

          .loading-variants {
            display: flex;
            align-items: center;
            gap: var(--space-sm);
            color: var(--text-muted);
            padding: var(--space-md);
            background: var(--bg-hover);
            border-radius: var(--radius-md);
          }

          .spinner {
            animation: spin 1s linear infinite;
          }

          @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }

          .vehicle-confirmed {
            display: flex;
            align-items: flex-start;
            gap: var(--space-sm);
            padding: var(--space-md);
            background: hsla(120, 50%, 40%, 0.15);
            border: 1px solid hsla(120, 50%, 40%, 0.3);
            border-radius: var(--radius-md);
            color: hsl(120, 50%, 45%);
          }

          .vehicle-confirmed div {
            display: flex;
            flex-direction: column;
            gap: 2px;
          }

          .vehicle-confirmed strong {
            color: var(--text-primary);
          }

          .variant-details {
            font-size: 0.85rem;
            color: var(--text-muted);
          }

          .input-hint {
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: var(--space-xs);
          }

          .modal-footer {
            display: flex;
            justify-content: flex-end;
            gap: var(--space-md);
            padding: var(--space-lg);
            border-top: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            margin-top: auto;
          }

          .disabled {
            opacity: 0.5;
            pointer-events: none;
          }
        `}</style>
            </div>
        </div>
    );
}
