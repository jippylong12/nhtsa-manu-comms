/* SeverityPill - the tier label ("Serious" / "Medium" / "Small") tinted with the tier colour. */

import { PRIORITY_COLORS } from '@/client';
import type { CommPriority, Severity } from '@/client';

const TIER: Record<Severity, { priority: CommPriority; label: string }> = {
    serious: { priority: 'high', label: 'Serious' },
    medium: { priority: 'medium', label: 'Medium' },
    small: { priority: 'low', label: 'Small' },
};

export function SeverityPill({ severity }: { severity: Severity }) {
    const { priority, label } = TIER[severity];
    const color = PRIORITY_COLORS[priority];
    return (
        <span
            style={{
                display: 'inline-block',
                padding: '1px var(--space-sm)',
                borderRadius: 'var(--radius-full)',
                fontSize: 'var(--font-size-sm)',
                fontWeight: 'var(--font-weight-semibold)',
                color,
                background: `color-mix(in srgb, ${color} 15%, transparent)`,
            }}
        >
            {label}
        </span>
    );
}
