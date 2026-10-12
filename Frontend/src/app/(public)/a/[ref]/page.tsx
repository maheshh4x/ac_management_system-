import type { Metadata } from "next";
import { PublicAssetView, type PublicAsset } from "./PublicAssetView";

export const metadata: Metadata = { title: "Asset — NITTTR ACMS" };

/**
 * Anonymous QR landing page (`/a/<asset_uuid>`), backed by the reduced-field
 * endpoint GET /api/v1/public/assets/<ref> (Phase 6 read model): no auth, no
 * secrets, only identity/model/location/status fields. Signed-in visitors are
 * redirected to their role's asset detail page by PublicAssetView.
 * Next 16: dynamic route params arrive as a Promise.
 */
export default async function PublicAssetPage({
  params,
}: {
  params: Promise<{ ref: string }>;
}) {
  const { ref } = await params;
  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:5000";

  let asset: PublicAsset | null = null;
  let failed = false;
  try {
    const res = await fetch(`${apiBase}/api/v1/public/assets/${encodeURIComponent(ref)}`, {
      cache: "no-store",
    });
    if (res.ok) {
      const body = (await res.json()) as { success: boolean; data?: PublicAsset };
      asset = body.success ? (body.data ?? null) : null;
    }
    failed = !res.ok && res.status !== 404;
  } catch {
    failed = true;
  }

  return <PublicAssetView slug={ref} asset={asset} failed={failed} />;
}
