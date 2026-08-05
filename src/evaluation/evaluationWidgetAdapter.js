function asMetric(
  key,
  label,
  value,
  unit = ""
) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return null;
  }

  return {
    key,
    label,
    value,
    unit,
  };
}

function findLab(
  labs,
  possibleKeys
) {
  for (const key of possibleKeys) {
    const value = labs?.[key];

    if (
      value !== null &&
      value !== undefined
    ) {
      return value;
    }
  }

  return null;
}

function labMetric(
  labs,
  key,
  label,
  possibleKeys
) {
  const lab = findLab(
    labs,
    possibleKeys
  );

  if (
    lab === null ||
    lab === undefined
  ) {
    return null;
  }

  if (typeof lab === "object") {
    return asMetric(
      key,
      label,

      lab.value ??
        lab.result ??
        lab.numericValue ??
        lab.measurement,

      lab.unit || ""
    );
  }

  return asMetric(
    key,
    label,
    lab
  );
}

function responseText(value) {
  if (
    value === null ||
    value === undefined
  ) {
    return "";
  }

  if (typeof value === "string") {
    return value.trim();
  }

  if (Array.isArray(value)) {
    return value
      .map(responseText)
      .filter(Boolean)
      .join("; ");
  }

  if (typeof value === "object") {
    const action = responseText(
      value.action
    );

    const text = responseText(
      value.text ||
        value.summary ||
        value.conclusion ||
        value.finding ||
        value.title ||
        value.name
    );

    const rationale = responseText(
      value.rationale
    );

    if (action && rationale) {
      return `${action}: ${rationale}`;
    }

    return (
      action ||
      text ||
      rationale
    );
  }

  return String(value);
}

function responseList(value) {
  const source = Array.isArray(value)
    ? value
    : value == null
    ? []
    : [value];

  return source
    .map(responseText)
    .filter(Boolean);
}

function buildKeyMetrics(episode) {
  const measurements =
    episode?.ecg?.measurements || {};

  const vitals =
    episode?.vitals || {};

  const labs =
    episode?.labs || {};

  return [
    asMetric(
      "heart-rate",
      "Heart rate",

      measurements.heartRateBpm ??
        measurements.heartRate ??
        vitals.heartRateBpm ??
        vitals.heartRate,

      "bpm"
    ),

    asMetric(
      "qrs",
      "QRS duration",
      measurements.qrsDurationMs,
      "ms"
    ),

    asMetric(
      "qtc",
      "QTc",
      measurements.qtcMs,
      "ms"
    ),

    asMetric(
      "pr",
      "PR interval",
      measurements.prIntervalMs,
      "ms"
    ),

    asMetric(
      "spo2",
      "SpO₂",
      vitals.spo2Pct ??
      vitals.spo2,
      "%"
    ),

    asMetric(
      "blood-pressure",
      "Blood pressure",

      (vitals.systolic ??
        vitals.bloodPressure?.systolic) != null &&
        (vitals.diastolic ??
        vitals.bloodPressure?.diastolic) != null
        ? `${
            vitals.systolic ??
            vitals.bloodPressure?.systolic
          }/${
            vitals.diastolic ??
            vitals.bloodPressure?.diastolic
          }`
        : null,

      "mmHg"
    ),

    labMetric(
      labs,
      "potassium",
      "Potassium",
      [
        "potassium",
        "Potassium",
        "K",
      ]
    ),

    labMetric(
      labs,
      "magnesium",
      "Magnesium",
      [
        "magnesium",
        "Magnesium",
        "Mg",
      ]
    ),

    labMetric(
      labs,
      "creatinine",
      "Creatinine",
      [
        "creatinine",
        "Creatinine",
      ]
    ),

    labMetric(
      labs,
      "troponin",
      "Troponin",
      [
        "troponin",
        "Troponin",
        "troponinI",
        "troponinT",
      ]
    ),

    labMetric(
      labs,
      "wbc",
      "WBC",
      [
        "wbc",
        "WBC",
        "whiteBloodCellCount",
      ]
    ),

    labMetric(
      labs,
      "lactate",
      "Lactate",
      [
        "lactate",
        "Lactate",
      ]
    ),
  ].filter(Boolean);
}

function vitalSummary(vitals) {
  const parts = [];

  if (
    vitals?.heartRate != null
  ) {
    parts.push(
      `HR ${vitals.heartRate} bpm`
    );
  }

  if (vitals?.spo2 != null) {
    parts.push(
      `SpO₂ ${vitals.spo2}%`
    );
  }

  if (
    vitals?.systolic != null &&
    vitals?.diastolic != null
  ) {
    parts.push(
      `BP ${vitals.systolic}/${vitals.diastolic} mmHg`
    );
  }

  if (
    vitals?.respiratoryRate != null
  ) {
    parts.push(
      `RR ${vitals.respiratoryRate}`
    );
  }

  return (
    parts.join(" • ") ||
    "Current vital measurements are incomplete."
  );
}

function morphologySummary(
  measurements
) {
  const parts = [];

  if (
    measurements?.qrsDurationMs != null
  ) {
    parts.push(
      `QRS ${measurements.qrsDurationMs} ms`
    );
  }

  if (
    measurements?.qtcMs != null
  ) {
    parts.push(
      `QTc ${measurements.qtcMs} ms`
    );
  }

  if (
    measurements?.prIntervalMs != null
  ) {
    parts.push(
      `PR ${measurements.prIntervalMs} ms`
    );
  }

  if (
    measurements?.rhythmRegularity
  ) {
    parts.push(
      measurements.rhythmRegularity
    );
  }

  return (
    parts.join(" • ") ||
    "No additional morphology measurements were supplied."
  );
}

export function adaptEvaluationRunToWidget({
  episode,
  run,
}) {
  const modelResponse =
    run?.displayModelResponse ||
    run?.modelResponse ||
    {};

  const validatedModelResponse =
    run?.validatedModelResponse ||
    run?.score?.normalizedModelResponse ||
    {};

  const score = run?.score || {};
  const validation =
    run?.validation ||
    score?.responseValidation ||
    {};

  const validationStatus =
    validation?.groundingStatus ||
    validation?.status ||
    run?.validationStatus ||
    "unknown";

  const episodeNarrative =
    responseText(modelResponse.episodeSummary);
  const etiologyContextNarrative =
    responseText(
      modelResponse.mostLikelyEtiologyAndClinicalContext ||
      modelResponse.mostLikelyEtiology ||
      modelResponse.clinicalContext
    );
  const possibleContributors =
    responseList(modelResponse.contributingFactors);
  const importantLimitations =
    responseList(
      modelResponse.materialEtiologicUncertainty ||
      modelResponse.uncertaintyAndMissingData
    );

  const strictlyAccepted =
    Boolean(validation?.accepted);
  const displayableWithReview =
    Boolean(validation?.displayableWithReview);

  return {
    incidentId:
      `evaluation:${episode?.episodeId || "unknown"}`,

    status:
      Boolean(run?.modelResponse)
        ? "complete"
        : run?.status || "ready",

    modelState: {
      available: Boolean(
        run?.modelResponse ||
        run?.displayModelResponse
      ),
      modelAlias:
        run?.model?.name ||
        "evaluation model",
      precomputed: Boolean(
        run?.model?.precomputed ||
        run?.precomputedResponse
      ),
      liveInference:
        run?.model?.liveInference ??
        run?.liveInference ??
        null,
    },

    responseProvenanceLabel:
      run?.precomputedResponse
        ? "Pre-evaluated MedGemma response"
        : null,
    precomputedResponse:
      run?.precomputedResponse || null,
    liveInference:
      run?.liveInference ??
      run?.model?.liveInference ??
      null,

    widgetInterpretation: {
      severity:
        episode?.episode?.severity ||
        "warning",
      statusLabel:
        "SLM response generated",
      displayPolicy:
        "always_show_model_response",
      headline:
        episode?.episode?.display ||
        episode?.episode?.title ||
        "Evaluation episode",
      episodeNarrative:
        episodeNarrative ||
        "No model episode summary was returned.",
      etiologyContextNarrative:
        etiologyContextNarrative ||
        "No model-generated etiologic explanation was returned.",
      rootCauseNarrative:
        etiologyContextNarrative ||
        "No model-generated etiologic explanation was returned.",

      // Compatibility fields are intentionally empty so the presentation
      // does not repeat context or merge deterministic limitations.
      arrhythmiaNarrative: "",
      morphologyNarrative: "",
      currentSituation: {
        narrative: "",
      },
      keyMetrics:
        buildKeyMetrics(episode),
      possibleContributors:
        possibleContributors.map(
          (value) => ({
            title: value,
            confidenceLabel:
              "model-generated",
            temporalFit:
              "evaluation evidence",
            evidenceAgainst: [],
          })
        ),
      importantLimitations,
      validationSummary: {
        status: validationStatus,
        strictlyAccepted,
        displayableWithReview,
        validatorPassed:
          strictlyAccepted ||
          displayableWithReview,
        hardErrorCount:
          (validation?.hardErrors || []).length,
        qualityErrorCount:
          (validation?.qualityErrors || []).length,
        contradictionCount:
          (validation?.contradictions || []).length,
        unsupportedFactCount:
          (validation?.unsupportedFacts || []).length,
        errors: validation?.errors || [],
        hardErrors:
          validation?.hardErrors || [],
        qualityErrors:
          validation?.qualityErrors || [],
        contradictions:
          validation?.contradictions || [],
        unsupportedFacts:
          validation?.unsupportedFacts || [],
      },
      evaluationStatistics: {
        scenarioScore:
          score?.total ?? null,
        overallPass:
          score?.overallPass ?? null,
        safetyPass:
          score?.safetyPass ?? null,
        attemptCount:
          run?.reliability?.attemptCount ??
          score?.attemptCount ??
          null,
        generationLatencySeconds:
          run?.model?.elapsedSeconds ??
          null,
        rawResponseDisplayed: true,
        validatedResponseAvailable:
          Boolean(
            Object.keys(
              validatedModelResponse
            ).length
          ),
      },
    },
  };
}
