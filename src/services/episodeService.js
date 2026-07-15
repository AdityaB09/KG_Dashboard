const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ||
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

  if (!response.ok) {
    throw new Error(
      `Episode request failed: ${response.status}`
    );
  }

  return response.json();
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

export async function getEpisode(episodeId) {
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
  ];

  const handlers = eventNames.map(
    (eventName) => {
      const handler = (event) => {
        try {
          onEvent?.(JSON.parse(event.data));
        } catch (error) {
          onError?.(error);
        }
      };

      eventSource.addEventListener(
        eventName,
        handler
      );

      return [eventName, handler];
    }
  );

  eventSource.onerror = (error) => {
    onError?.(error);
  };

  return () => {
    handlers.forEach(
      ([eventName, handler]) => {
        eventSource.removeEventListener(
          eventName,
          handler
        );
      }
    );

    eventSource.close();
  };
}