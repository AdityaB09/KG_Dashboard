const DEFAULT_WAVEFORM_STREAM_URL =
  "http://127.0.0.1:8000/api/waveforms/stream?sample_rate=200&batch_ms=50";

export function connectWaveformStream({ onFrame, onError }) {
  const streamUrl =
    import.meta.env.VITE_WAVEFORM_STREAM_URL || DEFAULT_WAVEFORM_STREAM_URL;

  const eventSource = new EventSource(streamUrl, {
    withCredentials: true,
  });

  eventSource.addEventListener("waveform-frame", (event) => {
    try {
      onFrame?.(JSON.parse(event.data));
    } catch (error) {
      console.error("[KGEN WAVEFORM FRAME ERROR]", error);
      onError?.(error);
    }
  });

  eventSource.onerror = (error) => {
    console.error("[KGEN WAVEFORM SSE ERROR]", error);
    onError?.(error);
  };

  return () => eventSource.close();
}