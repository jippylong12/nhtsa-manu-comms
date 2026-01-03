/* Vehicle Card Component */

import { format } from 'date-fns';
import { Calendar, FileText, Trash2, RefreshCw, ChevronRight } from 'lucide-react';
import type { Vehicle } from '@/client';

interface Props {
    vehicle: Vehicle;
    onFetch: (vehicleId: number) => void;
    onDelete: (vehicleId: number) => void;
    onSelect: (vehicleId: number) => void;
    isFetching?: boolean;
}

export function VehicleCard({ vehicle, onFetch, onDelete, onSelect, isFetching }: Props) {
    const lastFetched = vehicle.lastFetched
        ? format(new Date(vehicle.lastFetched), 'MMM d, yyyy h:mm a')
        : 'Never';

    return (
        <div className="vehicle-card animate-fade-in">
            <div className="vehicle-header">
                <div className="vehicle-info">
                    <span className="vehicle-year">{vehicle.year}</span>
                    <h3 className="vehicle-model">{vehicle.model}</h3>
                </div>
                <div className="vehicle-actions">
                    <button
                        className="btn btn-ghost btn-icon"
                        onClick={() => onFetch(vehicle.vehicleId)}
                        disabled={isFetching}
                        title="Fetch latest communications"
                    >
                        <RefreshCw size={18} className={isFetching ? 'animate-spin' : ''} />
                    </button>
                    <button
                        className="btn btn-ghost btn-icon"
                        onClick={() => onDelete(vehicle.vehicleId)}
                        title="Remove vehicle"
                    >
                        <Trash2 size={18} />
                    </button>
                </div>
            </div>

            <div className="vehicle-stats">
                <div className="stat">
                    <FileText size={16} />
                    <span>{vehicle.commCount} communications</span>
                </div>
                <div className="stat">
                    <Calendar size={16} />
                    <span>Last fetched: {lastFetched}</span>
                </div>
            </div>

            {vehicle.keywords.length > 0 && (
                <div className="vehicle-keywords">
                    {vehicle.keywords.map((kw) => (
                        <span key={kw} className="badge badge-primary">
                            {kw}
                        </span>
                    ))}
                </div>
            )}

            <button className="view-comms-btn" onClick={() => onSelect(vehicle.vehicleId)}>
                <span>View Communications</span>
                <ChevronRight size={18} />
            </button>

            <style>{`
        .vehicle-card {
          background: var(--bg-surface);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-lg);
          padding: var(--space-lg);
          transition: all var(--transition-default);
        }

        .vehicle-card:hover {
          border-color: var(--border-default);
          box-shadow: var(--shadow-md);
        }

        .vehicle-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          margin-bottom: var(--space-md);
        }

        .vehicle-year {
          font-size: 0.75rem;
          font-weight: 600;
          color: var(--color-primary);
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }

        .vehicle-model {
          font-size: 1.25rem;
          font-weight: 600;
          margin: var(--space-xs) 0 0;
        }

        .vehicle-actions {
          display: flex;
          gap: var(--space-xs);
        }

        .vehicle-actions .btn:hover {
          color: var(--color-danger);
        }

        .vehicle-actions .btn:first-child:hover {
          color: var(--color-primary);
        }

        .vehicle-stats {
          display: flex;
          flex-direction: column;
          gap: var(--space-xs);
          margin-bottom: var(--space-md);
        }

        .stat {
          display: flex;
          align-items: center;
          gap: var(--space-sm);
          font-size: 0.875rem;
          color: var(--text-secondary);
        }

        .stat svg {
          color: var(--text-muted);
        }

        .vehicle-keywords {
          display: flex;
          flex-wrap: wrap;
          gap: var(--space-xs);
          margin-bottom: var(--space-md);
        }

        .view-comms-btn {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: var(--space-sm);
          width: 100%;
          padding: var(--space-sm) var(--space-md);
          background: var(--bg-elevated);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-md);
          color: var(--text-secondary);
          font-size: 0.875rem;
          font-weight: 500;
          cursor: pointer;
          transition: all var(--transition-fast);
        }

        .view-comms-btn:hover {
          background: var(--bg-hover);
          color: var(--text-primary);
          border-color: var(--color-primary);
        }

        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }

        .animate-spin {
          animation: spin 1s linear infinite;
        }
      `}</style>
        </div>
    );
}
