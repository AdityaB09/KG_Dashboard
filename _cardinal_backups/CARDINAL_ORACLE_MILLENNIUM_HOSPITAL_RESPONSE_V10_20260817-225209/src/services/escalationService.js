const SAME_ORIGIN =
  typeof window !== "undefined"
    ? window.location.origin
    : "http://127.0.0.1:8000";

const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ||
  import.meta.env.VITE_BACKEND_URL ||
  SAME_ORIGIN
).replace(/\/$/, "");

async function requestJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message ||
          payload?.message ||
          `Escalation request failed: ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return payload;
}

export function getEscalation(eventId) {
  return requestJson(
    `/api/escalation/${encodeURIComponent(eventId)}`
  );
}

export function getEscalationForEpisode(episodeId) {
  return requestJson(
    `/api/escalation/episode/${encodeURIComponent(episodeId)}`
  );
}

export function getEscalationForIncident(incidentId) {
  return requestJson(
    `/api/escalation/incident/${encodeURIComponent(incidentId)}`
  );
}

export function acknowledgeEscalation(eventId, acknowledgedBy = "") {
  return requestJson(
    `/api/escalation/${encodeURIComponent(eventId)}/acknowledge`,
    {
      method: "POST",
      body: JSON.stringify({ actor: acknowledgedBy }),
    }
  );
}

export function escalateEscalation(eventId, reason = "manual_escalation") {
  return requestJson(
    `/api/escalation/${encodeURIComponent(eventId)}/escalate`,
    {
      method: "POST",
      body: JSON.stringify({ reason }),
    }
  );
}

export function resolveEscalation(eventId, resolvedBy = "") {
  return requestJson(
    `/api/escalation/${encodeURIComponent(eventId)}/resolve`,
    {
      method: "POST",
      body: JSON.stringify({ actor: resolvedBy }),
    }
  );
}
