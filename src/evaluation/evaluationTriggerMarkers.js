function finiteNumber(
  value,
  fallback = null
) {
  const numeric =
    Number(value);

  return Number.isFinite(numeric)
    ? numeric
    : fallback;
}


function normalizeKind(
  value
) {
  const normalized =
    String(value || "")
      .trim()
      .toLowerCase();

  if (
    normalized.includes(
      "detect"
    ) ||
    normalized.includes(
      "automatic"
    ) ||
    normalized.includes(
      "trigger"
    )
  ) {
    return "detected";
  }

  return "reference";
}


function normalizeMarker(
  marker,
  index
) {
  if (!marker) {
    return null;
  }

  const captureOffsetSeconds =
    finiteNumber(
      marker.captureOffsetSeconds ??
      marker.offsetSeconds ??
      marker.seconds ??
      marker.timeSeconds
    );

  if (
    captureOffsetSeconds ===
    null
  ) {
    return null;
  }

  const kind =
    normalizeKind(
      marker.kind ??
      marker.type ??
      marker.source
    );

  return {
    id:
      marker.id ||
      `${kind}-${index}-${captureOffsetSeconds}`,

    kind,

    label:
      marker.label ||
      (
        kind === "detected"
          ? "Detected trigger"
          : "Reference onset"
      ),

    captureOffsetSeconds,

    ruleId:
      marker.ruleId ||
      marker.detectorRule ||
      null,

    source:
      marker.source ||
      (
        kind === "detected"
          ? "detector"
          : "evaluation-reference"
      ),
  };
}


function deduplicateMarkers(
  markers
) {
  const seen =
    new Set();

  return markers.filter(
    (marker) => {
      const key = [
        marker.kind,
        marker.captureOffsetSeconds
          .toFixed(3),
        marker.ruleId || "",
      ].join("|");

      if (seen.has(key)) {
        return false;
      }

      seen.add(key);
      return true;
    }
  );
}


export function buildEvaluationTriggerMarkers({
  episode,
  capture,
}) {
  const durationSeconds =
    finiteNumber(
      episode?.ecg
        ?.durationSeconds ??
      episode?.episode
        ?.durationSeconds,
      8
    );

  const referenceOnsetSeconds =
    finiteNumber(
      capture
        ?.referenceOnsetSeconds ??
      capture
        ?.injectionStartOffsetSeconds ??
      episode
        ?.episode
        ?.eventStartOffsetSeconds ??
      episode
        ?.eventStartOffsetSeconds,
      0
    );

  const rawDetectedMarkers = [
    ...(
      Array.isArray(
        capture?.triggerAnnotations
      )
        ? capture
            .triggerAnnotations
        : []
    ),

    ...(
      Array.isArray(
        episode?.triggerAnnotations
      )
        ? episode
            .triggerAnnotations
        : []
    ),

    ...(
      Array.isArray(
        episode?.episode
          ?.triggerAnnotations
      )
        ? episode
            .episode
            .triggerAnnotations
        : []
    ),
  ];

  const normalized =
    rawDetectedMarkers
      .map(normalizeMarker)
      .filter(Boolean)
      .map(
        (marker) => ({
          ...marker,

          captureOffsetSeconds:
            Math.max(
              0,
              Math.min(
                durationSeconds,
                marker
                  .captureOffsetSeconds
              )
            ),
        })
      );

  const hasReferenceMarker =
    normalized.some(
      (marker) =>
        marker.kind ===
        "reference"
    );

  const markers =
    hasReferenceMarker
      ? normalized
      : [
          {
            id:
              "evaluation-reference-onset",

            kind:
              "reference",

            label:
              "Reference scenario onset",

            captureOffsetSeconds:
              Math.max(
                0,
                Math.min(
                  durationSeconds,
                  referenceOnsetSeconds
                )
              ),

            ruleId:
              null,

            source:
              "evaluation-reference",
          },

          ...normalized,
        ];

  return deduplicateMarkers(
    markers
  ).sort(
    (left, right) =>
      left
        .captureOffsetSeconds -
      right
        .captureOffsetSeconds
  );
}


export function summarizeTriggerMarkers(
  markers = []
) {
  const referenceCount =
    markers.filter(
      (marker) =>
        marker?.kind ===
        "reference"
    ).length;

  const detectedCount =
    markers.filter(
      (marker) =>
        marker?.kind ===
        "detected"
    ).length;

  return {
    referenceCount,
    detectedCount,
    totalCount:
      referenceCount +
      detectedCount,
  };
}
