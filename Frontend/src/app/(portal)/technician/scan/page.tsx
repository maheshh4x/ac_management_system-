'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Camera, CheckCircle2, Loader2, QrCode, Search, WifiOff } from 'lucide-react';
import { Html5Qrcode, Html5QrcodeScannerState, Html5QrcodeSupportedFormats } from 'html5-qrcode';
import { api, ApiError } from '@/lib/api/client';
import { getCachedAsset, getTechnicianAsset, TechnicianAsset } from '@/lib/technician-asset-service';

const READER_ID = 'qr-scan-reader';
/** html5-qrcode fires the success callback every frame: ignore repeats of
 * the same code within this window (the camera is stopped anyway). */
const DUPLICATE_WINDOW_MS = 2500;

type CameraPhase = 'idle' | 'starting' | 'scanning';

interface ResolveResponse {
  ac_id: string;
  asset_code: string | null;
  asset_uuid: string | null;
}

function cameraErrorMessage(err: unknown): string {
  const name = err instanceof Error ? err.name : '';
  const message = err instanceof Error ? err.message.toLowerCase() : '';
  if (
    name === 'NotAllowedError' ||
    name === 'PermissionDeniedError' ||
    name === 'SecurityError' ||
    message.includes('permission') ||
    message.includes('not allowed')
  ) {
    return 'Camera access was denied. Allow camera permission for this site, or enter the asset ID manually below.';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError' || message.includes('no camera')) {
    return 'No suitable camera was found on this device. Enter the asset ID manually below.';
  }
  return 'Could not start the camera. Enter the asset ID manually below.';
}

function describeResolveError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'FOREIGN_HOST') return 'This QR code was issued by a different site.';
    if (err.status === 404) return 'No asset matches this QR code.';
    if (err.code === 'NETWORK_ERROR' || err.code === 'TIMEOUT') {
      return 'No connection — search by asset ID or serial number for assets from your last sync.';
    }
    return err.message;
  }
  return 'Unable to read the QR code right now.';
}

/** The camera track html5-qrcode attached to its <video>. */
function cameraTrack(): MediaStreamTrack | null {
  const video = document.querySelector<HTMLVideoElement>(`#${READER_ID} video`);
  const stream = video?.srcObject instanceof MediaStream ? video.srcObject : null;
  return stream?.getVideoTracks()[0] ?? null;
}

/**
 * Technician scan page (Phase 6). Camera scanning uses html5-qrcode (works
 * in every modern browser, unlike the old BarcodeDetector-only flow) and
 * resolves payloads through POST /api/v1/qr/resolve before navigating to
 * the asset's detail page. Manual ID/serial search is the fallback when
 * the camera is unavailable or offline.
 */
export default function TechnicianScanPage() {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [asset, setAsset] = useState<TechnicianAsset | null>(null);
  const [camera, setCamera] = useState<CameraPhase>('idle');
  const [torchOn, setTorchOn] = useState(false);
  const [torchSupported, setTorchSupported] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState('');
  const [offline, setOffline] = useState(() =>
    typeof navigator === 'undefined' ? false : !navigator.onLine,
  );
  const scannerRef = useRef<Html5Qrcode | null>(null);
  const lastScanRef = useRef<{ text: string; at: number } | null>(null);

  const stopCamera = useCallback(async () => {
    const scanner = scannerRef.current;
    scannerRef.current = null;
    if (!scanner) return;
    try {
      const state = scanner.getState();
      if (state === Html5QrcodeScannerState.SCANNING || state === Html5QrcodeScannerState.PAUSED) {
        await scanner.stop();
      }
      scanner.clear();
    } catch {
      // Already stopped (navigation, permission loss) — nothing to release.
    }
  }, []);

  useEffect(() => {
    const update = () => setOffline(!navigator.onLine);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => {
      window.removeEventListener('online', update);
      window.removeEventListener('offline', update);
      void stopCamera();
    };
  }, [stopCamera]);

  const lookup = async (value = query) => {
    if (!value.trim()) return setError('Enter an asset ID or serial number.');
    setError('');
    const cached = getCachedAsset(value);
    if (cached) {
      setAsset(cached);
      return;
    }
    try {
      const found = await getTechnicianAsset(value);
      if (!found) setError(offline ? 'No signal — asset not found in offline data.' : 'Unknown QR code. No matching asset was found.');
      else setAsset(found);
    } catch {
      setError('Unable to search assets right now.');
    }
  };

  const resolveQr = async (text: string) => {
    try {
      const data = await api<ResolveResponse>('/api/v1/qr/resolve', {
        method: 'POST',
        body: { payload: text },
      });
      const target = data.asset_uuid ?? data.ac_id;
      router.push(`/technician/assets/${encodeURIComponent(target)}`);
    } catch (err) {
      setResolving(false);
      setError(describeResolveError(err));
    }
  };

  const handleDetected = async (text: string) => {
    const now = Date.now();
    const last = lastScanRef.current;
    // Debounce: consecutive frames report the same code — resolve it once.
    if (last && last.text === text && now - last.at < DUPLICATE_WINDOW_MS) return;
    lastScanRef.current = { text, at: now };
    setQuery(text);
    setError('');
    setResolving(true);
    await stopCamera();
    await resolveQr(text);
  };

  const startCamera = async () => {
    setError('');
    setCamera('starting');
    try {
      const scanner = new Html5Qrcode(READER_ID, {
        formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE],
        verbose: false,
      });
      scannerRef.current = scanner;
      await scanner.start(
        { facingMode: 'environment' },
        { fps: 10, qrbox: { width: 220, height: 220 } },
        (text) => void handleDetected(text),
        () => {
          /* frame without a code — expected while scanning */
        },
      );
      setCamera('scanning');
      const track = cameraTrack();
      const capabilities =
        track && typeof track.getCapabilities === 'function'
          ? (track.getCapabilities() as MediaTrackCapabilities & { torch?: boolean })
          : null;
      setTorchSupported(Boolean(capabilities?.torch));
      setTorchOn(false);
    } catch (err) {
      scannerRef.current = null;
      setCamera('idle');
      setError(cameraErrorMessage(err));
    }
  };

  const stopCameraManually = async () => {
    await stopCamera();
    setCamera('idle');
    setTorchOn(false);
    setTorchSupported(false);
  };

  const toggleTorch = async () => {
    const track = cameraTrack();
    if (!track) return;
    const enable = !torchOn;
    try {
      // `torch` is not in the TS DOM lib (camera flashlight extension).
      await track.applyConstraints({
        advanced: [{ torch: enable }],
      } as unknown as MediaTrackConstraints);
      setTorchOn(enable);
    } catch {
      setTorchSupported(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-lg flex-col gap-5 pb-8">
      <div className="text-center">
        <div className="mx-auto mb-3 flex h-16 w-16 items-center justify-center rounded-2xl border border-blue-200 bg-blue-100 text-blue-600 shadow-sm">
          <QrCode size={28} />
        </div>
        <h1 className="text-xl font-bold text-slate-900">Scan AC QR Code</h1>
        <p className="mt-1 text-sm text-slate-500">Scan an asset label or search by ID/serial number.</p>
        {offline && (
          <p className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700">
            <WifiOff size={13} />Offline mode
          </p>
        )}
      </div>

      <div className="relative overflow-hidden rounded-2xl border-2 border-dashed border-slate-300 bg-slate-100">
        <div
          id={READER_ID}
          className="aspect-video w-full bg-slate-900 [&_video]:h-full [&_video]:w-full [&_video]:object-cover"
        />
        {camera === 'idle' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-100 text-slate-400">
            <Camera size={40} />
            <button
              onClick={() => void startCamera()}
              className="min-h-11 rounded-xl bg-white px-4 text-sm font-semibold text-slate-700 shadow-sm"
            >
              Use camera
            </button>
            <p className="px-6 text-center text-xs">Camera access is only used to read the QR code.</p>
          </div>
        )}
        {camera === 'starting' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-100/90 text-slate-500">
            <Loader2 className="animate-spin" size={24} />
            <p className="text-sm font-semibold">Starting camera…</p>
          </div>
        )}
        {camera === 'scanning' && (
          <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-2 bg-slate-900/70 px-3 py-2">
            <span className="flex items-center gap-1.5 text-xs font-semibold text-white">
              <Loader2 size={13} className="animate-spin" />Scanning…
            </span>
            <div className="flex items-center gap-2">
              {torchSupported && (
                <button
                  onClick={() => void toggleTorch()}
                  className={`min-h-9 rounded-lg px-2.5 text-xs font-semibold transition-colors ${torchOn ? 'bg-amber-400 text-slate-900' : 'bg-white/90 text-slate-700'}`}
                >
                  {torchOn ? 'Torch on' : 'Torch'}
                </button>
              )}
              <button
                onClick={() => void stopCameraManually()}
                className="min-h-9 rounded-lg bg-white/90 px-2.5 text-xs font-semibold text-slate-700"
              >
                Stop
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search size={17} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && void lookup()}
            placeholder="Asset ID or serial number"
            className="min-h-12 w-full rounded-xl border border-slate-200 bg-white pl-10 pr-3 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-200"
          />
        </div>
        <button
          onClick={() => void lookup()}
          className="min-h-12 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700"
        >
          Search
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm font-semibold text-red-700">
          <AlertTriangle size={17} className="mt-0.5 shrink-0" />
          {error}
        </div>
      )}

      {asset && (
        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-start gap-3 rounded-t-2xl bg-slate-900 p-4 text-white">
            <CheckCircle2 className="mt-0.5 text-emerald-400" size={20} />
            <div>
              <p className="font-mono font-bold">{asset.id}</p>
              <p className="text-sm text-slate-300">
                {asset.details.make} {asset.details.model} • {asset.technical.status}
              </p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 p-4 text-sm">
            <Info label="Type" value={asset.details.type} />
            <Info label="Capacity" value={asset.details.capacity} />
            <Info label="Serial" value={asset.details.serialNumber} />
            <Info label="Location" value={`${asset.location.block} / ${asset.location.floor} / ${asset.location.room}`} />
          </div>
          <button
            onClick={() => router.push(`/technician/assets/${encodeURIComponent(asset.id)}`)}
            className="m-4 mt-0 flex min-h-12 w-[calc(100%-2rem)] items-center justify-center rounded-xl bg-blue-600 text-sm font-semibold text-white hover:bg-blue-700"
          >
            View asset details
          </button>
        </div>
      )}

      {resolving && (
        <div className="flex items-center justify-center gap-2 text-sm text-blue-600">
          <Loader2 className="animate-spin" size={16} />Opening asset…
        </div>
      )}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-slate-50 p-3">
      <p className="text-[10px] font-bold uppercase text-slate-400">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-slate-800">{value || 'Not available'}</p>
    </div>
  );
}
