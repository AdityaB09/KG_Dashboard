const SAME_ORIGIN =
  typeof window !== "undefined"
    ? window.location.origin
    : "http://127.0.0.1:8000";

const API_BASE_URL = (
  import.meta.env.VITE_BACKEND_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  SAME_ORIGIN
).replace(/\/$/, "");


async function requestJson(
  path,
  options = {}
) {
  const method =
    options.method ||
    "GET";

  const startedAt =
    performance.now();

  console.info(
    "[KGEN EVAL API] request",
    {
      method,
      path,
    }
  );

  const response = await fetch(
    `${API_BASE_URL}${path}`,
    {
      credentials: "include",
      headers: {
        "Content-Type":
          "application/json",
        ...(options.headers || {}),
      },
      ...options,
    }
  );

  const payload = await response
    .json()
    .catch(() => null);

  const elapsedMs = Math.round(
    performance.now() -
      startedAt
  );

  if (!response.ok) {
    console.error(
      "[KGEN EVAL API] failed",
      {
        method,
        path,
        status:
          response.status,
        elapsedMs,
        detail:
          payload?.detail,
      }
    );

    throw new Error(
      payload?.detail ||
      `Evaluation request failed with ${response.status}.`
    );
  }

  console.info(
    "[KGEN EVAL API] completed",
    {
      method,
      path,
      status:
        response.status,
      elapsedMs,
    }
  );

  return payload;
}


export function getEvaluationHealth() {
  return requestJson(
    "/api/evaluation/health"
  );
}


export function listEvaluationEpisodes() {
  return requestJson(
    "/api/evaluation/episodes"
  );
}


export function getEvaluationEpisode(
  episodeId
) {
  return requestJson(
    "/api/evaluation/episodes/" +
      encodeURIComponent(
        episodeId
      )
  );
}


export function listEvaluationRuns() {
  return requestJson(
    "/api/evaluation/runs"
  );
}


export function getEvaluationRun(
  runId
) {
  return requestJson(
    "/api/evaluation/runs/" +
      encodeURIComponent(
        runId
      )
  );
}


export async function getLatestCompletedEvaluationRun(
  episodeId,
  modelName = null
) {
  const result =
    await listEvaluationRuns();

  const runs =
    Array.isArray(
      result?.runs
    )
      ? result.runs
      : [];

  const match = runs.find(
    (run) => {
      const sameEpisode =
        run.episodeId ===
        episodeId;

      const complete =
        run.status ===
        "complete";

      const sameModel =
        !modelName ||
        run.model ===
          modelName;

      return (
        sameEpisode &&
        complete &&
        sameModel &&
        run.runId
      );
    }
  );

  if (!match) {
    console.info(
      "[KGEN EVAL API] no matching saved run",
      {
        episodeId,
        modelName,
      }
    );

    return null;
  }

  console.info(
    "[KGEN EVAL API] matching saved run found",
    {
      episodeId,
      modelName,
      runId:
        match.runId,
    }
  );

  return getEvaluationRun(
    match.runId
  );
}


export function runEvaluationSlm(
  episodeId,
  {
    model = null,
    temperature = 0,
  } = {}
) {
  console.info(
    "[KGEN EVAL API] starting SLM evaluation",
    {
      episodeId,
      model:
        model ||
        "backend SLM_MODEL",
      temperature,
    }
  );

  return requestJson(
    "/api/evaluation/episodes/" +
      encodeURIComponent(
        episodeId
      ) +
      "/run-slm",
    {
      method: "POST",
      body: JSON.stringify({
        model,
        temperature,
      }),
    }
  );
}
