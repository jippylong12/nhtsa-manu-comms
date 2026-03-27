/* Vehicle Card Component */

import { format } from 'date-fns';
import { Calendar, FileText, Trash2, RefreshCw, ChevronRight } from 'lucide-react';
import type { Vehicle } from '@/client';
import styles from './VehicleCard.module.css';

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
        <div className={`${styles.vehicleCard} animate-fade-in`}>
            <div className={styles.vehicleHeader}>
                <div>
                    <span className={styles.vehicleYear}>{vehicle.year}</span>
                    <h3 className={styles.vehicleModel}>{vehicle.model}</h3>
                </div>
                <div className={styles.vehicleActions}>
                    <button
                        className="btn btn-ghost btn-icon"
                        onClick={() => onFetch(vehicle.vehicleId)}
                        disabled={isFetching}
                        title="Fetch latest communications"
                    >
                        <RefreshCw size={18} className={isFetching ? styles.animateSpin : ''} />
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

            <div className={styles.vehicleStats}>
                <div className={styles.stat}>
                    <FileText size={16} />
                    <span>{vehicle.commCount} communications</span>
                </div>
                <div className={styles.stat}>
                    <Calendar size={16} />
                    <span>Last fetched: {lastFetched}</span>
                </div>
            </div>

            {vehicle.keywords.length > 0 && (
                <div className={styles.vehicleKeywords}>
                    {vehicle.keywords.map((kw) => (
                        <span key={kw} className="badge badge-primary">
                            {kw}
                        </span>
                    ))}
                </div>
            )}

            <button className={styles.viewCommsBtn} onClick={() => onSelect(vehicle.vehicleId)}>
                <span>View Communications</span>
                <ChevronRight size={18} />
            </button>
        </div>
    );
}
