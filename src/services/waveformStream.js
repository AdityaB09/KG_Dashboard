const SAME_ORIGIN = typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8000";
const DEFAULT_WAVEFORM_STREAM_URL = `${SAME_ORIGIN}/api/waveforms/stream?batch_ms=50`;


function createSessionId() {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID ===
      "function"
  ) {
    return (
      `waveform-${crypto.randomUUID()}`
    );
  }

  return (
    "waveform-" +
    Date.now().toString(36) +
    "-" +
    Math.random()
      .toString(36)
      .slice(2)
  );
}


const SESSION_STORAGE_KEY =
  "kardiogenics.waveformSessionId";

function loadOrCreateSessionId() {
  try {
    const existing = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (existing) return existing;

    const created = createSessionId();
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, created);
    return created;
  } catch {
    return createSessionId();
  }
}

let browserWaveformSessionId =
  loadOrCreateSessionId();


export function getWaveformSessionId() {
  return browserWaveformSessionId;
}


export function resetWaveformSessionId() {
  browserWaveformSessionId =
    createSessionId();

  try {
    window.sessionStorage.setItem(
      SESSION_STORAGE_KEY,
      browserWaveformSessionId
    );
  } catch {
    // In-memory ID still works when storage is unavailable.
  }

  return browserWaveformSessionId;
}


export function connectWaveformStream({
  source = "physionet",
  sessionId =
    browserWaveformSessionId,
  onFrame,
  onError,
}) {
  const baseUrl =
    import.meta.env
      .VITE_WAVEFORM_STREAM_URL ||
    DEFAULT_WAVEFORM_STREAM_URL;

  const url =
    new URL(baseUrl);

  url.searchParams.set(
    "source",
    source
  );

  url.searchParams.set(
    "session_id",
    sessionId
  );

  console.info(
    "[KGEN WAVEFORM SSE] connecting",
    {
      source,
      sessionId,
      url: url.toString(),
    }
  );

  const eventSource =
    new EventSource(
      url.toString(),
      {
        withCredentials: true,
      }
    );

  eventSource.addEventListener(
    "waveform-frame",
    (event) => {
      try {
        onFrame?.(
          JSON.parse(
            event.data
          )
        );
      } catch (error) {
        console.error(
          "[KGEN WAVEFORM FRAME ERROR]",
          error
        );

        onError?.(error);
      }
    }
  );

  eventSource.onerror = (
    error
  ) => {
    console.error(
      "[KGEN WAVEFORM SSE ERROR]",
      {
        source,
        sessionId,
        error,
      }
    );

    onError?.(error);
  };

  return () => {
    console.info(
      "[KGEN WAVEFORM SSE] disconnecting",
      {
        source,
        sessionId,
      }
    );

    eventSource.close();
  };
}
