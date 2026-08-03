const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ||
  import.meta.env.VITE_BACKEND_URL ||
  "http://127.0.0.1:8000"
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
    throw new Error(
      payload?.detail ||
        `Evaluation injection request failed: ${response.status}`
    );
  }

  return payload;
}

export function getEvaluationInjectionHealth() {
  return requestJson("/api/evaluation-injection/health");
}

export function listEvaluationInjectionScenarios() {
  return requestJson("/api/evaluation-injection/scenarios");
}

export function armEvaluationInjection(
  sessionId,
  {
    scenarioId,
    baselineSeconds = 10,
    preSeconds = 6,
    postSeconds = 6,
    runSlm = true,
  }
) {
  console.info("[KGEN EVAL INJECTION UI] arming", {
    sessionId,
    scenarioId,
    baselineSeconds,
    preSeconds,
    postSeconds,
    runSlm,
  });

  return requestJson(
    `/api/evaluation-injection/sessions/${encodeURIComponent(sessionId)}/arm`,
    {
      method: "POST",
      body: JSON.stringify({
        scenarioId,
        baselineSeconds,
        preSeconds,
        postSeconds,
        runSlm,
      }),
    }
  );
}

export function getEvaluationInjectionStatus(sessionId) {
  return requestJson(
    `/api/evaluation-injection/sessions/${encodeURIComponent(sessionId)}`
  );
}

export function cancelEvaluationInjection(sessionId) {
  return requestJson(
    `/api/evaluation-injection/sessions/${encodeURIComponent(sessionId)}/cancel`,
    { method: "POST" }
  );
}
