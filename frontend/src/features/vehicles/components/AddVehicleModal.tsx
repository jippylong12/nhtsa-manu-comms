/* Add Vehicle Modal Component */

import { useState } from 'react';
import { X, Plus, Car } from 'lucide-react';

interface Props {
    isOpen: boolean;
    onClose: () => void;
    onSubmit: (data: { vehicleId: number; year: string; model: string; keywords: string[] }) => void;
    isLoading?: boolean;
}

export function AddVehicleModal({ isOpen, onClose, onSubmit, isLoading }: Props) {
    const [vehicleId, setVehicleId] = useState('');
    const [year, setYear] = useState('');
    const [model, setModel] = useState('');
    const [keywords, setKeywords] = useState('');

    if (!isOpen) return null;

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        const keywordList = keywords
            .split(',')
            .map((k) => k.trim())
            .filter(Boolean);
        onSubmit({
            vehicleId: parseInt(vehicleId, 10),
            year,
            model: model.toUpperCase(),
            keywords: keywordList,
        });
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal animate-slide-up" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <div className="modal-icon">
                        <Car size={24} />
                    </div>
                    <h2>Add Vehicle</h2>
                    <button className="btn btn-ghost btn-icon" onClick={onClose}>
                        <X size={20} />
                    </button>
                </div>

                <form onSubmit={handleSubmit}>
                    <div className="modal-body">
                        <div className="input-group">
                            <label className="input-label">NHTSA Vehicle ID *</label>
                            <input
                                type="number"
                                className="input"
                                placeholder="e.g., 218944"
                                value={vehicleId}
                                onChange={(e) => setVehicleId(e.target.value)}
                                required
                            />
                            <span className="input-hint">
                                Find vehicle IDs at{' '}
                                <a href="https://www.nhtsa.gov/vehicle" target="_blank" rel="noopener noreferrer">
                                    nhtsa.gov/vehicle
                                </a>
                            </span>
                        </div>

                        <div className="form-row">
                            <div className="input-group">
                                <label className="input-label">Model Year *</label>
                                <input
                                    type="text"
                                    className="input"
                                    placeholder="e.g., 2024"
                                    value={year}
                                    onChange={(e) => setYear(e.target.value)}
                                    required
                                />
                            </div>
                            <div className="input-group">
                                <label className="input-label">Model Name *</label>
                                <input
                                    type="text"
                                    className="input"
                                    placeholder="e.g., SILVERADO EV"
                                    value={model}
                                    onChange={(e) => setModel(e.target.value)}
                                    required
                                />
                            </div>
                        </div>

                        <div className="input-group">
                            <label className="input-label">Filter Keywords (optional)</label>
                            <input
                                type="text"
                                className="input"
                                placeholder="software, update, recall (comma-separated)"
                                value={keywords}
                                onChange={(e) => setKeywords(e.target.value)}
                            />
                            <span className="input-hint">
                                Only show communications containing these keywords
                            </span>
                        </div>
                    </div>

                    <div className="modal-footer">
                        <button type="button" className="btn btn-secondary" onClick={onClose}>
                            Cancel
                        </button>
                        <button type="submit" className="btn btn-primary" disabled={isLoading}>
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
            max-width: 500px;
            max-height: 90vh;
            overflow: hidden;
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
            max-height: 60vh;
            overflow-y: auto;
          }

          .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: var(--space-md);
          }

          .input-hint {
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: var(--space-xs);
          }

          .input-hint a {
            color: var(--color-primary);
          }

          .modal-footer {
            display: flex;
            justify-content: flex-end;
            gap: var(--space-md);
            padding: var(--space-lg);
            border-top: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
          }

          @media (max-width: 640px) {
            .form-row {
              grid-template-columns: 1fr;
            }
          }
        `}</style>
            </div>
        </div>
    );
}
