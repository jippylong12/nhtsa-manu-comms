/* A vehicle on /app: the whole card is one link to its page; Remove sits in a menu. */

import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { MoreHorizontal, Trash2, Lock } from 'lucide-react';

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
    /** When set, Remove hands the car to the caller's own dialog instead of confirming here. */
    onRequestRemove?: (vehicle: MyVehicle) => void;
    removing?: boolean;
    /** Extra menu items shown above Remove (for example Refresh on /admin). */
    actions?: ReactNode;
}

export function MyVehicleCard({ vehicle, me, onRemove, onRequestRemove, removing, actions }: Props) {
    const [menuOpen, setMenuOpen] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const menuRef = useRef<HTMLDivElement>(null);
    const name = vehicleName(vehicle);
    const processing = vehicle.availability === 'processing';
    const access = accessLabel(vehicle, me);
    const total = vehicle.counts.serious + vehicle.counts.medium + vehicle.counts.small;
    const unreadTotal = vehicle.unread.serious + vehicle.unread.medium + vehicle.unread.small;

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
            <Link
                to={`/app/vehicles/${vehicle.vehicleId}`}
                className={styles.link}
                aria-label={`${name}, ${total} bulletins, ${unreadTotal} unread`}
            >
                <div className={styles.header}>
                    <h2 className={styles.title}>{name}</h2>
                    <div className={styles.badges}>
                        <span className={`${styles.badge} ${processing ? styles.badgeWarn : styles.badgeReady}`}>
                            {availabilityLabel(vehicle)}
                        </span>
                        <span className={`${styles.badge} ${access === 'Sample' ? styles.badgeSample : styles.badgeAccess}`}>
                            {access === 'Sample' && <Lock size={12} aria-hidden="true" />}
                            {access === 'Sample' ? 'Free sample' : access}
                        </span>
                    </div>
                </div>
                <div className={styles.tiles}>
                    {TILES.map((t) => (
                        <div
                            key={t.key}
                            className={styles.tile}
                            style={{ ['--tile-color' as string]: PRIORITY_COLORS[t.priority] }}
                        >
                            {processing ? (
                                <span className={`skeleton ${styles.skeletonCount}`} aria-hidden="true" />
                            ) : (
                                <span className={styles.count}>{vehicle.counts[t.key]}</span>
                            )}
                            <span className={styles.label}>{t.label}</span>
                            <span className={styles.unread}>
                                {!processing && vehicle.unread[t.key] > 0 ? `${vehicle.unread[t.key]} unread` : ''}
                            </span>
                        </div>
                    ))}
                </div>
                {processing ? (
                    <p className={styles.footer}>We're reading this car's bulletins.</p>
                ) : vehicle.newThisWeek > 0 ? (
                    <p className={styles.footer}>
                        <span className={styles.dot} aria-hidden="true" />
                        {vehicle.newThisWeek} new this week
                    </p>
                ) : (
                    <p className={`${styles.footer} ${styles.footerMuted}`}>Nothing new this week</p>
                )}
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
                            <>
                            {actions}
                            <button
                                type="button"
                                className={styles.menuItem}
                                onClick={() => {
                                    if (onRequestRemove) {
                                        setMenuOpen(false);
                                        onRequestRemove(vehicle);
                                    } else {
                                        setConfirming(true);
                                    }
                                }}
                            >
                                <Trash2 size={15} /> Remove
                            </button>
                            </>
                        )}
                    </div>
                )}
            </div>
        </article>
    );
}
