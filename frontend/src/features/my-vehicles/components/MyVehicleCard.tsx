/* A vehicle on /app: the whole card is one link to its page; Remove sits in a menu. */

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { MoreHorizontal, Trash2 } from 'lucide-react';

import { PRIORITY_COLORS } from '@/client';
import type { CommPriority, Me, MyVehicle, SeverityCounts } from '@/client';
import { accessLabel, availabilityLabel, vehicleName } from '../access';
import styles from './MyVehicleCard.module.css';

const TILES: { key: keyof SeverityCounts; label: string; priority: CommPriority }[] = [
    { key: 'serious', label: 'Serious', priority: 'high' },
    { key: 'medium', label: 'Medium', priority: 'medium' },
    { key: 'small', label: 'Small', priority: 'low' },
];

interface Props {
    vehicle: MyVehicle;
    me: Me | undefined;
    onRemove: (id: number) => void;
    removing?: boolean;
}

export function MyVehicleCard({ vehicle, me, onRemove, removing }: Props) {
    const [menuOpen, setMenuOpen] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const menuRef = useRef<HTMLDivElement>(null);
    const name = vehicleName(vehicle);
    const processing = vehicle.availability === 'processing';
    const access = accessLabel(vehicle, me);

    useEffect(() => {
        if (!menuOpen) return;
        const onDown = (e: MouseEvent) => {
            if (!menuRef.current?.contains(e.target as Node)) {
                setMenuOpen(false);
                setConfirming(false);
            }
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                setMenuOpen(false);
                setConfirming(false);
            }
        };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [menuOpen]);

    return (
        <article className={styles.card}>
            <Link to={`/app/vehicles/${vehicle.vehicleId}`} className={styles.link} aria-label={name}>
                <h2 className={styles.title}>{name}</h2>
                <div className={styles.tiles}>
                    {TILES.map((t) => (
                        <div
                            key={t.key}
                            className={`${styles.tile} ${processing ? styles.skeleton : ''}`}
                            style={{ ['--tile-color' as string]: PRIORITY_COLORS[t.priority] }}
                        >
                            <span className={styles.count}>{processing ? '–' : vehicle.counts[t.key]}</span>
                            <span className={styles.label}>{t.label}</span>
                        </div>
                    ))}
                </div>
                <div className={styles.badges}>
                    <span className={`${styles.badge} ${processing ? styles.badgeWarn : styles.badgeReady}`}>
                        {availabilityLabel(vehicle)}
                    </span>
                    <span className={`${styles.badge} ${access === 'Sample' ? '' : styles.badgeAccess}`}>
                        {access}
                    </span>
                </div>
            </Link>

            <div className={styles.menu} ref={menuRef}>
                <button
                    type="button"
                    className={`btn btn-ghost btn-icon ${styles.menuButton}`}
                    aria-label={`More actions for ${name}`}
                    aria-haspopup="true"
                    aria-expanded={menuOpen}
                    onClick={() => {
                        setMenuOpen((o) => !o);
                        setConfirming(false);
                    }}
                >
                    <MoreHorizontal size={18} />
                </button>
                {menuOpen && (
                    <div className={styles.popover} role="group" aria-label={`Actions for ${name}`}>
                        {confirming ? (
                            <>
                                <p className={styles.confirmText}>Remove {name} from your account?</p>
                                <div className={styles.confirmActions}>
                                    <button
                                        type="button"
                                        className={`btn btn-danger btn-sm ${styles.popBtn}`}
                                        disabled={removing}
                                        onClick={() => onRemove(vehicle.vehicleId)}
                                    >
                                        {removing ? 'Removing...' : 'Remove'}
                                    </button>
                                    <button
                                        type="button"
                                        className={`btn btn-secondary btn-sm ${styles.popBtn}`}
                                        onClick={() => setConfirming(false)}
                                    >
                                        Cancel
                                    </button>
                                </div>
                            </>
                        ) : (
                            <button
                                type="button"
                                className={styles.menuItem}
                                onClick={() => setConfirming(true)}
                            >
                                <Trash2 size={15} /> Remove
                            </button>
                        )}
                    </div>
                )}
            </div>
        </article>
    );
}
