/* Mark one bulletin read: the row, the tab badge and the car's tile count move together, before the POST returns. */

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { meApi } from '@/client';
import type { Me, MyBulletinsResponse, Severity } from '@/client';
import { meKeys } from '../../queryKeys';

export interface MarkReadVars {
    nhtsaId: string;
    severity: Severity;
}

interface Snapshot {
    lists: [readonly unknown[], MyBulletinsResponse | undefined][];
    me: Me | undefined;
}

const dec = (n: number) => Math.max(0, n - 1);

// A list narrowed by search or filters may not contain the bulletin, so a miss says nothing about its facets.
function isUnnarrowed(key: readonly unknown[]): boolean {
    const p = (key[5] ?? {}) as Record<string, unknown>;
    return !(p.search || p.commType || p.systems || p.dateFrom || p.dateTo || p.nhtsaId);
}

export function useMarkRead(vehicleId: number, options: { onError?: () => void } = {}) {
    const queryClient = useQueryClient();
    const listsKey = meKeys.vehicleBulletins(vehicleId);

    return useMutation<void, Error, MarkReadVars, Snapshot>({
        mutationFn: ({ nhtsaId }) => meApi.markRead(nhtsaId),
        onMutate: async ({ nhtsaId, severity }) => {
            await queryClient.cancelQueries({ queryKey: listsKey });
            await queryClient.cancelQueries({ queryKey: meKeys.all, exact: true });

            const snapshot: Snapshot = {
                lists: queryClient.getQueriesData<MyBulletinsResponse>({ queryKey: listsKey }),
                me: queryClient.getQueryData<Me>(meKeys.all),
            };

            for (const [key, data] of snapshot.lists) {
                if (!data) continue;
                const row = data.items.find((b) => b.nhtsaId === nhtsaId);
                if (row?.read === true) continue;
                if (!row && !isUnnarrowed(key)) continue;
                queryClient.setQueryData<MyBulletinsResponse>(key, {
                    ...data,
                    items: data.items.map((b) => (b.nhtsaId === nhtsaId ? { ...b, read: true } : b)),
                    facets: {
                        ...data.facets,
                        unreadBySeverity: {
                            ...data.facets.unreadBySeverity,
                            [severity]: dec(data.facets.unreadBySeverity[severity]),
                        },
                    },
                });
            }

            if (snapshot.me) {
                queryClient.setQueryData<Me>(meKeys.all, {
                    ...snapshot.me,
                    vehicles: snapshot.me.vehicles.map((v) =>
                        v.vehicleId === vehicleId
                            ? { ...v, unread: { ...v.unread, [severity]: dec(v.unread[severity]) } }
                            : v
                    ),
                });
            }
            return snapshot;
        },
        onError: (_err, _vars, snapshot) => {
            if (snapshot) {
                for (const [key, data] of snapshot.lists) queryClient.setQueryData(key, data);
                queryClient.setQueryData(meKeys.all, snapshot.me);
            }
            options.onError?.();
        },
        // Stale, not refetched now: a refetch would drop the open row from an unread-only list.
        onSettled: () => {
            void queryClient.invalidateQueries({ queryKey: listsKey, refetchType: 'none' });
            void queryClient.invalidateQueries({ queryKey: meKeys.all, exact: true, refetchType: 'none' });
        },
    });
}
