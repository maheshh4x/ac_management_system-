'use client';

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, LogIn } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { roleAssetDetail } from "@/lib/permissions";

/** Exact reduced field set returned by GET /api/v1/public/assets/<ref>. */
export interface PublicAsset {
  asset_code: string | null;
  make: string | null;
  model: string | null;
  type: string | null;
  capacity: number | null;
  block: string | null;
  floor: string | null;
  room: string | null;
  operational_status: string | null;
  last_maintenance_date: string | null;
}

function statusChipClass(status: string): string {
  if (status === "WORKING") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (status === "FAULT") return "border-red-200 bg-red-50 text-red-700";
  return "border-slate-200 bg-slate-50 text-slate-600";
}

/** DATE columns arrive as "YYYY-MM-DD"; parse at local midnight so the
 * calendar day never shifts west of UTC. */
function formatDate(isoDate: string): string {
  const date = new Date(`${isoDate.slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime()) ? isoDate : date.toLocaleDateString();
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-slate-500">{label}</dt>
      <dd className="font-semibold text-slate-800 text-right break-words">{value?.trim() || "—"}</dd>
    </div>
  );
}

/**
 * QR landing body (Phase 6): reduced public view for signed-out visitors
 * (mobile-first — this opens from a phone camera), plus the redirect for
 * signed-in users: they never consume the reduced view, they land on the
 * asset detail page of their own portal.
 */
export function PublicAssetView({
  slug,
  asset,
  failed,
}: {
  slug: string;
  asset: PublicAsset | null;
  failed: boolean;
}) {
  const router = useRouter();
  const { user, status, isLoading } = useAuth();
  const signedIn = Boolean(user) && status === "approved";

  useEffect(() => {
    if (!isLoading && signedIn && user) {
      router.replace(roleAssetDetail(user.role, slug));
    }
  }, [isLoading, signedIn, user, slug, router]);

  const statusLabel = asset?.operational_status?.trim() || "UNKNOWN";

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-4 sm:p-6">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 sm:p-8 shadow-xl">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          NITTTR Chennai — Asset
        </p>
        {asset ? (
          <>
            <div className="mt-2 flex items-start justify-between gap-3">
              <h1 className="text-2xl font-bold text-slate-900 break-all">
                {asset.asset_code ?? slug}
              </h1>
              <span
                className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold ${statusChipClass(statusLabel)}`}
              >
                {statusLabel}
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-600">
              {[asset.make, asset.model].filter(Boolean).join(" ") || "—"}
            </p>
            <dl className="mt-4 space-y-2 text-sm">
              <Row label="Type" value={asset.type} />
              <Row
                label="Capacity"
                value={asset.capacity != null ? `${asset.capacity} T` : null}
              />
              <Row
                label="Location"
                value={[asset.block, asset.floor, asset.room].filter(Boolean).join(" / ")}
              />
              <Row
                label="Last service"
                value={asset.last_maintenance_date ? formatDate(asset.last_maintenance_date) : null}
              />
            </dl>
            <p className="mt-6 rounded-xl bg-slate-50 p-3 text-xs text-slate-500">
              Public view — serial numbers, technicians, maintenance history and actions stay
              behind sign-in.
            </p>
          </>
        ) : (
          <>
            <h1 className="mt-2 text-2xl font-bold text-slate-900">
              {failed ? "Service unavailable" : "Asset not found"}
            </h1>
            <p className="mt-2 text-sm text-slate-600">
              {failed
                ? "The asset service could not be reached. Please try again shortly."
                : "This QR code does not match a registered asset."}
            </p>
          </>
        )}
        <Link
          href="/login"
          className="mt-6 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-blue-600 text-sm font-semibold text-white transition-colors hover:bg-blue-700"
        >
          <LogIn size={16} /> Sign in for full details
        </Link>
        {signedIn && (
          <p className="mt-3 flex items-center justify-center gap-1.5 text-xs text-slate-500">
            <Loader2 size={12} className="animate-spin" /> Opening your full asset view…
          </p>
        )}
      </div>
    </main>
  );
}
