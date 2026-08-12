const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ||
  import.meta.env.VITE_BACKEND_URL ||
  "http://127.0.0.1:8000"
).replace(/\/$/, "");

async function requestJson(
  path,
  options = {}
) {
  const response = await fetch(
    `${API_BASE}${path}`,
    {
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    }
  );

  const payload = await response
    .json()
    .catch(() => null);

  if (!response.ok) {
    const detail = payload?.detail;

    const message =
      typeof detail === "string"
        ? detail
        : detail?.message ||
          `Episode request failed: ${response.status}`;

    const error = new Error(message);

    error.status = response.status;
    error.detail = detail;

    throw error;
  }

  return payload;
}

export async function getIncidentContext(
  incidentId
) {
  return requestJson(
    `/api/incidents/${encodeURIComponent(
      incidentId
    )}/context`
  );
}

export async function loadIncidentContext(
  incidentId
) {
  return requestJson(
    `/api/incidents/${encodeURIComponent(
      incidentId
    )}/context/load`,
    {
      method: "POST",
    }
  );
}

export async function listEpisodes() {
  const result = await requestJson(
    "/api/episodes"
  );

  return result.episodes || [];
}

export async function getLatestEpisode() {
  const result = await requestJson(
    "/api/episodes/latest"
  );

  return result.episode || null;
}

export async function getEpisode(
  episodeId
) {
  return requestJson(
    `/api/episodes/${encodeURIComponent(
      episodeId
    )}`
  );
}

export async function getEpisodeWaveforms(
  episodeId
) {
  return requestJson(
    `/api/episodes/${encodeURIComponent(
      episodeId
    )}/waveforms?leads=lead2,lead1,avf&max_points=1800`
  );
}

export async function getEpisodeAnalysis(
  episodeId
) {
  return requestJson(
    `/api/episodes/${encodeURIComponent(
      episodeId
    )}/analysis`
  );
}

export async function analyzeEpisode(
  episodeId,
  { force = false } = {}
) {
  return requestJson(
    `/api/episodes/${encodeURIComponent(
      episodeId
    )}/analyze?force=${
      force ? "true" : "false"
    }`,
    {
      method: "POST",
    }
  );
}

export async function getIncidentAnalysis(
  incidentId
) {
  return requestJson(
    `/api/incidents/${encodeURIComponent(
      incidentId
    )}/analysis`
  );
}

export async function analyzeIncident(
  incidentId,
  { force = false } = {}
) {
  return requestJson(
    `/api/incidents/${encodeURIComponent(
      incidentId
    )}/analyze?force=${
      force ? "true" : "false"
    }`,
    {
      method: "POST",
    }
  );
}

const EPISODE_EVENT_NAMES = [
  "episode.detected",
  "episode.captured",
  "episode.analysis_ready",
  "episode.error",
  "phase7.started",
  "phase7.ready",
  "phase7.failed",
  "clinical.context.updated",
  "clinical.context.checked",
  "evaluation.injection.armed",
  "evaluation.injection.started",
  "evaluation.injection.detected",
  "evaluation.injection.event_complete",
  "evaluation.injection.captured",
  "evaluation.injection.complete",
  "evaluation.injection.failed",
  "evaluation.injection.cancelled",
];

// App.jsx, SevenLeadWaveformPage and ClinicalPhysiologyPage all subscribe to
// episode events. Opening one EventSource per component consumes multiple
// Cloud Run concurrent-request slots because SSE requests remain open. Keep
// exactly one transport per browser bundle and multicast events to subscribers.
let episodeEventSource = null;
const episodeEventSubscribers = new Set();
const episodeEventHandlers = new Map();

function ensureEpisodeEventSource() {
  if (episodeEventSource) return episodeEventSource;

  const source = new EventSource(
    `${API_BASE}/api/episodes/events`,
    { withCredentials: true }
  );

  EPISODE_EVENT_NAMES.forEach((eventName) => {
    const handler = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        episodeEventSubscribers.forEach((subscriber) => {
          subscriber.onError?.(error);
        });
        return;
      }

      episodeEventSubscribers.forEach((subscriber) => {
        subscriber.onEvent?.(payload);
      });
    };

    episodeEventHandlers.set(eventName, handler);
    source.addEventListener(eventName, handler);
  });

  source.onerror = (error) => {
    episodeEventSubscribers.forEach((subscriber) => {
      subscriber.onError?.(error);
    });
  };

  episodeEventSource = source;

  if (import.meta.env.DEV) {
    console.info("[KGEN EPISODE SSE] shared connection opened");
  }

  return source;
}

function closeEpisodeEventSourceIfUnused() {
  if (episodeEventSubscribers.size > 0 || !episodeEventSource) return;

  episodeEventHandlers.forEach((handler, eventName) => {
    episodeEventSource.removeEventListener(eventName, handler);
  });
  episodeEventHandlers.clear();

  episodeEventSource.close();
  episodeEventSource = null;

  if (import.meta.env.DEV) {
    console.info("[KGEN EPISODE SSE] shared connection closed");
  }
}

export function connectEpisodeEvents({
  onEvent,
  onError,
}) {
  const subscriber = { onEvent, onError };
  episodeEventSubscribers.add(subscriber);
  ensureEpisodeEventSource();

  return () => {
    episodeEventSubscribers.delete(subscriber);
    closeEpisodeEventSourceIfUnused();
  };
}


export async function listIncidents() {
  const result = await requestJson(
    "/api/incidents"
  );

  if (Array.isArray(result)) {
    return result;
  }

  return (
    result?.incidents ||
    result?.items ||
    []
  );
}

export async function getIncidentEpisodes(
  incidentId
) {
  const result = await requestJson(
    `/api/incidents/${encodeURIComponent(
      incidentId
    )}/episodes`
  );

  return result?.episodes || [];
}

export async function getSlmWidget(
  incidentId
) {
  return requestJson(
    `/api/slm-widget/incidents/${encodeURIComponent(
      incidentId
    )}`
  );
}

export async function getPhase7Status(
  incidentId
) {
  return requestJson(
    `/api/phase7/incidents/${encodeURIComponent(
      incidentId
    )}/status`
  );
}