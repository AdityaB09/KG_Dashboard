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

export function connectEpisodeEvents({
  onEvent,
  onError,
}) {
  const eventSource = new EventSource(
    `${API_BASE}/api/episodes/events`,
    {
      withCredentials: true,
    }
  );

  const eventNames = [
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

  const handlers = eventNames.map(
    (eventName) => {
      const handler = (event) => {
        try {
          onEvent?.(
            JSON.parse(event.data)
          );
        } catch (error) {
          onError?.(error);
        }
      };

      eventSource.addEventListener(
        eventName,
        handler
      );

      return [
        eventName,
        handler,
      ];
    }
  );

  eventSource.onerror = (error) => {
    onError?.(error);
  };

  return () => {
    handlers.forEach(
      ([
        eventName,
        handler,
      ]) => {
        eventSource
          .removeEventListener(
            eventName,
            handler
          );
      }
    );

    eventSource.close();
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