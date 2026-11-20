declare global {
  interface Window {
    BarcodeDetector?: new (options?: { formats: string[] }) => {
      detect(source: ImageBitmapSource): Promise<Array<{ rawValue?: string }>>;
    };
  }
}

export function stopVideoStream(video: HTMLVideoElement | null): void {
  if (!video) return;
  const stream = video.srcObject as MediaStream | null;
  if (stream) {
    for (const track of stream.getTracks()) {
      track.stop();
    }
    video.srcObject = null;
  }
}

export async function startVideoStream(video: HTMLVideoElement): Promise<void> {
  if (video.srcObject) return;
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Camera access is not supported in this browser or context.");
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: "environment" } }
  });
  video.srcObject = stream;
  await video.play();
}

/**
 * Decode a single video frame into a barcode/QR string using progressive enhancement:
 * 1. Native `window.BarcodeDetector` (hardware-accelerated on supported browsers).
 * 2. Fallback: capture frame to an offscreen canvas and lazy-load `zxing-wasm/reader`
 *    so browsers without native detection still scan without bloating the initial bundle.
 */
export async function scanVideo(video: HTMLVideoElement): Promise<string | null> {
  if (video.readyState < 2 || video.videoWidth === 0 || video.videoHeight === 0) {
    throw new Error("Camera feed is still starting. Wait a moment and try again.");
  }
  if (window.BarcodeDetector) {
    const detector = new window.BarcodeDetector({
      formats: ["ean_13", "ean_8", "code_128", "qr_code"]
    });
    const codes = await detector.detect(video);
    return codes[0]?.rawValue ?? null;
  }
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.drawImage(video, 0, 0);
  const image = context.getImageData(0, 0, canvas.width, canvas.height);
  // Lazy-load the WASM barcode reader only when native BarcodeDetector is unavailable.
  const { readBarcodesFromImageData } = await import("zxing-wasm/reader");
  const result = await readBarcodesFromImageData(image);
  return result[0]?.text ?? null;
}
