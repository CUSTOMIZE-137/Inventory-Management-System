import { useEffect, useRef, useState } from "react";
import { Camera, ScanBarcode } from "lucide-react";
import { scanVideo, startVideoStream, stopVideoStream } from "../scanner";
import { Button, Modal } from "./ui/primitives";

interface BarcodeScannerModalProps {
  open: boolean;
  onClose: () => void;
  onDetected: (code: string) => void;
}

export function BarcodeScannerModal({ open, onClose, onDetected }: BarcodeScannerModalProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [cameraReady, setCameraReady] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const videoEl = videoRef.current;
    if (!open) {
      stopVideoStream(videoEl);
      setCameraReady(false);
      setError(null);
      return;
    }
    if (!videoEl) return;
    let active = true;
    setError(null);
    void startVideoStream(videoEl)
      .then(() => {
        if (active) setCameraReady(true);
      })
      .catch((err: unknown) => {
        if (active) {
          setCameraReady(false);
          setError(err instanceof Error ? err.message : "Unable to access camera.");
        }
      });

    return () => {
      active = false;
      stopVideoStream(videoEl);
      setCameraReady(false);
    };
  }, [open]);

  const handleCapture = async () => {
    if (!videoRef.current) return;
    setScanning(true);
    setError(null);
    try {
      const code = await scanVideo(videoRef.current);
      if (!code) {
        setError("No barcode detected. Center the barcode in the frame and try again.");
        return;
      }
      onDetected(code);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to read barcode.");
    } finally {
      setScanning(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Scan Item Barcode"
      description="Uses native BarcodeDetector with automatic zxing-wasm fallback."
    >
      <div className="space-y-4">
        <div className="relative overflow-hidden rounded-xl border border-zinc-800 bg-black aspect-video flex items-center justify-center">
          <video
            ref={videoRef}
            className="h-full w-full object-cover"
            playsInline
            muted
            autoPlay
          />
          {!cameraReady && !error && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-zinc-950/80 text-zinc-400">
              <Camera className="h-7 w-7 animate-pulse text-amber-400" />
              <span className="text-xs">Starting rear camera...</span>
            </div>
          )}
          <div className="pointer-events-none absolute inset-6 rounded-lg border-2 border-dashed border-amber-400/60" />
        </div>

        {error && (
          <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!cameraReady || scanning}
            onClick={() => void handleCapture()}
          >
            <ScanBarcode className="h-4 w-4" />
            {scanning ? "Scanning..." : "Capture Barcode"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
