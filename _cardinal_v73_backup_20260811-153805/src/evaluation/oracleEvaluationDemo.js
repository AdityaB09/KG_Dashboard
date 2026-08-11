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
        payload?.message ||
        `Oracle evaluation request failed: ${response.status}`
    );
  }

  return payload;
}

export function getOracleEvaluationBootstrap() {
  return requestJson("/api/evaluation-demo/bootstrap");
}

export function startOracleEvaluationDemo(waveformSessionId) {
  const normalizedSessionId = String(
    waveformSessionId || ""
  ).trim();

  if (!normalizedSessionId) {
    return Promise.reject(
      new Error(
        "A waveform session ID is required to start the Oracle evaluation."
      )
    );
  }

  return requestJson("/api/evaluation-demo/start", {
    method: "POST",
    body: JSON.stringify({
      waveformSessionId: normalizedSessionId,
    }),
  });
}

export function getOracleEvaluationDemoStatus(
  waveformSessionId
) {
  const normalizedSessionId = String(
    waveformSessionId || ""
  ).trim();

  if (!normalizedSessionId) {
    return Promise.reject(
      new Error(
        "A waveform session ID is required to read Oracle evaluation status."
      )
    );
  }

  return requestJson(
    `/api/evaluation-demo/status/${encodeURIComponent(
      normalizedSessionId
    )}`
  );
}

export function cancelOracleEvaluationDemo(
  waveformSessionId
) {
  const normalizedSessionId = String(
    waveformSessionId || ""
  ).trim();

  if (!normalizedSessionId) {
    return Promise.reject(
      new Error(
        "A waveform session ID is required to cancel the Oracle evaluation."
      )
    );
  }

  return requestJson(
    `/api/evaluation-demo/cancel/${encodeURIComponent(
      normalizedSessionId
    )}`,
    {
      method: "POST",
    }
  );
}
