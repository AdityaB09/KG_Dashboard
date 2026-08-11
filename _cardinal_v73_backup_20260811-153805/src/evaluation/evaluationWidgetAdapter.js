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
  // Prefer the native V7 nine-field response. displayModelResponse exists only
  // as a compatibility view for older UI paths and intentionally omits fields.
  const modelResponse =
    run?.clinicalInterpretation ||
    run?.modelResponse ||
    run?.displayModelResponse ||
    {};

  const score = run?.score || {};
  const validation =
    run?.validation ||
    score?.responseValidation ||
    {};

  const validationStatus =
    run?.validationStatus ||
    validation?.groundingStatus ||
    validation?.status ||
    (run?.validContract ? "contract_valid" : "unknown");

  const episodeSummary = responseText(
    modelResponse.episodeSummary
  );
  const rhythm = responseText(
    modelResponse.rhythm
  );
  const primaryEtiology = responseText(
    modelResponse.primaryEtiology ||
      modelResponse.mostLikelyEtiology
  );
  const mechanism = responseText(
    modelResponse.mechanism
  );
  const legacyEtiologyContext = responseText(
    modelResponse.mostLikelyEtiologyAndClinicalContext ||
      modelResponse.clinicalContext
  );
  const etiologyContextNarrative =
    [primaryEtiology, mechanism]
      .filter(Boolean)
      .join(". ") ||
    legacyEtiologyContext;

  const keyECGEvidence = responseList(
    modelResponse.keyECGEvidence
  );
  const contributingFactors = responseList(
    modelResponse.contributingFactors
  );
  const uncertainty = responseList(
    modelResponse.uncertainty ||
      modelResponse.materialEtiologicUncertainty ||
      modelResponse.uncertaintyAndMissingData
  );
  const recommendedActions = responseList(
    modelResponse.recommendedActions
  );
  const rejectedAlternatives = Array.isArray(
    modelResponse.rejectedAlternatives
  )
    ? modelResponse.rejectedAlternatives
        .map((item) => {
          if (!item) return null;
          if (typeof item === "string") {
            return {
              alternative: item,
              why: "",
            };
          }
          return {
            alternative: responseText(
              item.alternative ||
                item.title ||
                item.name
            ),
            why: responseText(
              item.why ||
                item.reason ||
                item.rationale
            ),
          };
        })
        .filter((item) => item?.alternative)
    : [];

  const precomputed = Boolean(
    run?.modelState?.precomputed ||
      run?.model?.precomputed ||
      run?.precomputedResponse
  );
  const modelAlias =
    run?.modelState?.modelAlias ||
    run?.model?.name ||
    run?.model ||
    "evaluation model";

  return {
    incidentId:
      run?.incidentId ||
      `evaluation:${episode?.episodeId || "unknown"}`,
    scenarioId:
      run?.scenarioId ||
      episode?.episodeId ||
      null,
    status:
      Boolean(
        run?.modelResponse ||
          run?.clinicalInterpretation
      )
        ? "complete"
        : run?.status || "ready",

    modelState: {
      available: Boolean(
        run?.modelResponse ||
          run?.displayModelResponse ||
          run?.clinicalInterpretation
      ),
      modelAlias,
      precomputed,
      liveInference:
        run?.modelState?.liveInference ??
        run?.model?.liveInference ??
        run?.liveInference ??
        !precomputed,
    },

    responseProvenanceLabel:
      run?.responseProvenanceLabel ||
      (precomputed
        ? `Precomputed ${modelAlias} response`
        : "Live model response"),
    precomputedResponse:
      run?.precomputedResponse || null,
    responseMeta:
      run?.responseMeta || {},
    provenance: {
      pipeline: "etiology_v7",
      inferenceMode: precomputed
        ? "precomputed"
        : "live",
      phase6Used: false,
      deterministicOverlay: false,
      ...(run?.provenance || {}),
    },

    clinicalInterpretation: modelResponse,

    widgetInterpretation: {
      schemaVersion:
        "cardinal-etiology-widget-interpretation-v7.1",
      severity:
        episode?.episode?.severity ||
        "warning",
      statusLabel: precomputed
        ? "Precomputed SLM interpretation"
        : "SLM interpretation",
      displayPolicy:
        "always_show_model_response",
      headline:
        rhythm ||
        episode?.episode?.title ||
        "Evaluation episode",

      episodeSummary,
      rhythm,
      primaryEtiology,
      mechanism,
      keyECGEvidence,
      contributingFactors,
      rejectedAlternatives,
      recommendedActions,
      uncertainty,

      // Compatibility fields for components that still read the V6 widget DTO.
      episodeNarrative:
        episodeSummary ||
        "No model episode summary was returned.",
      etiologyContextNarrative:
        etiologyContextNarrative ||
        "No model-generated etiologic explanation was returned.",
      rootCauseNarrative:
        etiologyContextNarrative ||
        "No model-generated etiologic explanation was returned.",
      arrhythmiaNarrative: rhythm,
      morphologyNarrative:
        keyECGEvidence.join(" "),
      currentSituation: {
        narrative: "",
      },
      keyMetrics:
        buildKeyMetrics(episode),
      possibleContributors:
        contributingFactors.map(
          (value) => ({
            title: value,
            confidenceLabel:
              "model-generated",
            temporalFit:
              "episode evidence",
            evidenceAgainst: [],
          })
        ),
      importantLimitations: uncertainty,
      materialEtiologicUncertainty:
        uncertainty,
      validationSummary: {
        status: validationStatus,
        strictlyAccepted:
          validationStatus ===
          "contract_valid",
        displayableWithReview: true,
        validatorPassed:
          validationStatus ===
          "contract_valid",
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
          run?.responseMeta?.elapsedSeconds ??
          null,
        rawResponseDisplayed: true,
        validatedResponseAvailable:
          validationStatus ===
          "contract_valid",
      },
    },
  };
}
