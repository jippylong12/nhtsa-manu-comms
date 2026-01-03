/* Add Vehicle Modal Component */

import { useState } from 'react';
import { X, Plus, Car, ExternalLink, AlertTriangle } from 'lucide-react';
import { useDiscoveryYears, useDiscoveryMakes, useDiscoveryModels } from '../../communications/hooks/useDiscovery';

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
    const [manualId, setManualId] = useState('');
    const [keywords, setKeywords] = useState('');

    const { data: years, isLoading: loadingYears } = useDiscoveryYears();
    const { data: makes, isLoading: loadingMakes } = useDiscoveryMakes(selectedYear);
    const { data: models, isLoading: loadingModels } = useDiscoveryModels(selectedYear, selectedMake);

    if (!isOpen) return null;

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        // Allow submission if we have a manual ID OR if we have full selection (but we need ID)
        if (!manualId) return;

        const keywordList = keywords
            .split(',')
            .map((k) => k.trim())
            .filter(Boolean);

        onSubmit({
            vehicleId: parseInt(manualId, 10),
            year: selectedYear ? String(selectedYear) : '',
            model: selectedModel || selectedMake, // Fallback
            keywords: keywordList,
        });
    };

    const getNhtsaLink = () => {
        if (!selectedYear || !selectedMake) return 'https://www.nhtsa.gov/vehicle';
        const parts = [selectedYear, selectedMake, selectedModel].filter(Boolean);
        return `https://www.nhtsa.gov/vehicle/${parts.map(p => encodeURIComponent(p)).join('/')}`;
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
                        {/* Progressive Selection for Link Gen */}
                        <div className="selection-flow">
                            <div className="input-group">
                                <label className="input-label">Year</label>
                                <select
                                    className="input select"
                                    value={selectedYear || ''}
                                    onChange={(e) => {
                                        setSelectedYear(Number(e.target.value));
                                        setSelectedMake('');
                                        setSelectedModel('');
                                    }}
                                    disabled={loadingYears}
                                >
                                    <option value="">Year</option>
                                    {years?.map((y) => (
                                        <option key={y} value={y}>{y}</option>
                                    ))}
                                </select>
                            </div>

                            <div className={`input-group ${!selectedYear ? 'disabled' : ''}`}>
                                <label className="input-label">Make</label>
                                <select
                                    className="input select"
                                    value={selectedMake}
                                    onChange={(e) => {
                                        setSelectedMake(e.target.value);
                                        setSelectedModel('');
                                    }}
                                    disabled={!selectedYear || loadingMakes}
                                >
                                    <option value="">Make</option>
                                    {makes?.map((m) => (
                                        <option key={m} value={m}>{m}</option>
                                    ))}
                                </select>
                            </div>

                            <div className={`input-group ${!selectedMake ? 'disabled' : ''}`}>
                                <label className="input-label">Model</label>
                                <select
                                    className="input select"
                                    value={selectedModel}
                                    onChange={(e) => setSelectedModel(e.target.value)}
                                    disabled={!selectedMake || loadingModels}
                                >
                                    <option value="">Model</option>
                                    {models?.map((m) => (
                                        <option key={m} value={m}>{m}</option>
                                    ))}
                                </select>
                            </div>
                        </div>

                        {/* ID Discovery Helper */}
                        <div className="id-helper-box">
                            <div className="helper-icon">
                                <AlertTriangle size={18} />
                            </div>
                            <div className="helper-content">
                                <strong>Technical ID Required</strong>
                                <p>
                                    NHTSA separates Safety Ratings from Technical Communications.
                                    <strong> You must use the Technical ID.</strong>
                                </p>
                                <div className="helper-steps">
                                    <ol>
                                        <li>Click the link below to open the official NHTSA page.</li>
                                        <li>
                                            Look at the URL in your browser address bar. It will look like:
                                            <div className="code-snippet">nhtsa.gov/vehicle/<strong>20540</strong>/details</div>
                                            (You may need to select a Trim/Style on the page first).
                                        </li>
                                        <li>Copy that number (e.g. 20540) and paste it below.</li>
                                    </ol>
                                </div>
                                <a
                                    href={getNhtsaLink()}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="helper-link"
                                >
                                    Open NHTSA Page <ExternalLink size={14} />
                                </a>
                            </div>
                        </div>

                        {/* Manual ID Input */}
                        <div className="input-group">
                            <label className="input-label">Vehicle ID (from NHTSA)</label>
                            <input
                                type="number"
                                className="input"
                                placeholder="Paste ID here (e.g. 218944)"
                                value={manualId}
                                onChange={(e) => setManualId(e.target.value)}
                                required
                            />
                        </div>

                        {/* Additional Info */}
                        <div className="input-group">
                            <label className="input-label">Filter Keywords (optional)</label>
                            <input
                                type="text"
                                className="input"
                                placeholder="software, update, recall (comma-separated)"
                                value={keywords}
                                onChange={(e) => setKeywords(e.target.value)}
                            />
                        </div>
                    </div>

                    <div className="modal-footer">
                        <button type="button" className="btn btn-secondary" onClick={onClose}>
                            Cancel
                        </button>
                        <button
                            type="submit"
                            className="btn btn-primary"
                            disabled={isLoading || !manualId}
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
            max-width: 500px;
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

          .selection-flow {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: var(--space-xs);
          }

          .select {
            appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 0.5rem center;
            background-size: 1.25em;
            padding-right: 2rem;
            font-size: 0.9rem;
          }

          .id-helper-box {
            display: flex;
            gap: var(--space-md);
            background: var(--bg-hover);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-md);
            padding: var(--space-md);
            font-size: 0.9rem;
          }

          .helper-icon {
            color: var(--color-warning);
            margin-top: 2px;
            flex-shrink: 0;
          }

          .helper-content {
            display: flex;
            flex-direction: column;
            gap: var(--space-xs);
          }

          .helper-content p {
            margin: 0;
            color: var(--text-muted);
            font-size: 0.85rem;
          }

          .helper-steps ol {
            margin: var(--space-xs) 0;
            padding-left: var(--space-lg);
            color: var(--text-muted);
          }

          .helper-steps li {
            margin-bottom: var(--space-xs);
          }

          .code-snippet {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
            margin: 4px 0;
            display: inline-block;
          }
          
          .code-snippet strong {
              color: var(--color-primary);
          }

          .helper-link {
            display: inline-flex;
            align-items: center;
            gap: var(--space-xs);
            color: var(--color-primary);
            font-weight: 500;
            margin-top: var(--space-xs);
          }

          .helper-link:hover {
            text-decoration: underline;
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

          @media (max-width: 640px) {
            .selection-flow {
              grid-template-columns: 1fr;
            }
          }
        `}</style>
            </div>
        </div>
    );
}
