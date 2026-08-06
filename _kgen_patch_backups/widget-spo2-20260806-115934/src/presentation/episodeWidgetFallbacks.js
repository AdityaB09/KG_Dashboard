/*
 * KGEN presentation-only bedside fallbacks.
 *
 * These values are used only by the five small bedside widgets on the
 * SevenLeadWaveformPage when the active source or episode pack does not
 * provide a value.
 *
 * They are never written to episode files, Phase 6, SLM evidence, prompts,
 * validation, scoring, or precomputed MedGemma artifacts.
 */

const SCENARIO_WIDGET_VALUES = {
  "VFIB-STEMI-001": {
    pre: {
      heartRate: 84,
      spo2: 97,
      systolic: 136,
      diastolic: 82,
      respiratoryRate: 18,
      temperature: 36.9,
    },
    event: {
      heartRate: 0,
      spo2: 84,
      systolic: 0,
      diastolic: 0,
      respiratoryRate: 0,
      temperature: 36.9,
    },
    post: {
      heartRate: 88,
      spo2: 95,
      systolic: 102,
      diastolic: 64,
      respiratoryRate: 20,
      temperature: 36.9,
    },
  },

  "TORSADES-LQT-002": {
    pre: {
      heartRate: 82,
      spo2: 96,
      systolic: 124,
      diastolic: 74,
      respiratoryRate: 18,
      temperature: 36.7,
    },
    event: {
      heartRate: 228,
      spo2: 88,
      systolic: 68,
      diastolic: 40,
      respiratoryRate: 24,
      temperature: 36.7,
    },
    post: {
      heartRate: 96,
      spo2: 94,
      systolic: 104,
      diastolic: 62,
      respiratoryRate: 20,
      temperature: 36.7,
    },
  },

  "VT-ISCHEMIC-003": {
    pre: {
      heartRate: 78,
      spo2: 95,
      systolic: 106,
      diastolic: 66,
      respiratoryRate: 18,
      temperature: 36.8,
    },
    event: {
      heartRate: 182,
      spo2: 94,
      systolic: 88,
      diastolic: 58,
      respiratoryRate: 22,
      temperature: 36.8,
    },
    post: {
      heartRate: 92,
      spo2: 94,
      systolic: 98,
      diastolic: 62,
      respiratoryRate: 20,
      temperature: 36.8,
    },
  },

  "AFIB-RVR-SEPSIS-004": {
    pre: {
      heartRate: 108,
      spo2: 94,
      systolic: 96,
      diastolic: 58,
      respiratoryRate: 26,
      temperature: 38.9,
    },
    event: {
      heartRate: 150,
      spo2: 93,
      systolic: 92,
      diastolic: 54,
      respiratoryRate: 28,
      temperature: 38.9,
    },
    post: {
      heartRate: 128,
      spo2: 93,
      systolic: 94,
      diastolic: 56,
      respiratoryRate: 26,
      temperature: 38.8,
    },
  },

  "CHB-HYPERK-005": {
    pre: {
      heartRate: 52,
      spo2: 95,
      systolic: 88,
      diastolic: 54,
      respiratoryRate: 18,
      temperature: 36.5,
    },
    event: {
      heartRate: 34,
      spo2: 95,
      systolic: 78,
      diastolic: 50,
      respiratoryRate: 18,
      temperature: 36.5,
    },
    post: {
      heartRate: 42,
      spo2: 95,
      systolic: 82,
      diastolic: 50,
      respiratoryRate: 18,
      temperature: 36.5,
    },
  },

  "BRADY-DIGTOX-006": {
    pre: {
      heartRate: 52,
      spo2: 96,
      systolic: 110,
      diastolic: 66,
      respiratoryRate: 16,
      temperature: 36.6,
    },
    event: {
      heartRate: 38,
      spo2: 96,
      systolic: 104,
      diastolic: 62,
      respiratoryRate: 16,
      temperature: 36.6,
    },
    post: {
      heartRate: 46,
      spo2: 96,
      systolic: 106,
      diastolic: 64,
      respiratoryRate: 16,
      temperature: 36.6,
    },
  },

  "SVT-PSVT-007": {
    pre: {
      heartRate: 82,
      spo2: 98,
      systolic: 122,
      diastolic: 78,
      respiratoryRate: 16,
      temperature: 36.8,
    },
    event: {
      heartRate: 190,
      spo2: 98,
      systolic: 118,
      diastolic: 76,
      respiratoryRate: 18,
      temperature: 36.8,
    },
    post: {
      heartRate: 96,
      spo2: 98,
      systolic: 120,
      diastolic: 76,
      respiratoryRate: 18,
      temperature: 36.8,
    },
  },

  "NSVT-ECTOPY-008": {
    pre: {
      heartRate: 76,
      spo2: 96,
      systolic: 126,
      diastolic: 78,
      respiratoryRate: 16,
      temperature: 37.0,
    },
    event: {
      heartRate: 78,
      spo2: 96,
      systolic: 128,
      diastolic: 80,
      respiratoryRate: 16,
      temperature: 37.0,
    },
    post: {
      heartRate: 82,
      spo2: 96,
      systolic: 126,
      diastolic: 78,
      respiratoryRate: 16,
      temperature: 37.0,
    },
  },
};

const DEFAULTS = {
  pre: {
    heartRate: 80,
    spo2: 97,
    systolic: 122,
    diastolic: 76,
    respiratoryRate: 16,
    temperature: 36.8,
  },
  event: {
    heartRate: 96,
    spo2: 96,
    systolic: 118,
    diastolic: 72,
    respiratoryRate: 18,
    temperature: 36.8,
  },
  post: {
    heartRate: 84,
    spo2: 96,
    systolic: 118,
    diastolic: 72,
    respiratoryRate: 17,
    temperature: 36.8,
  },
};

function finite(value) {
  const numeric = Number(value);

  return Number.isFinite(numeric)
    ? numeric
    : null;
}

function phaseFromState(state) {
  const normalized = String(
    state || ""
  ).trim().toUpperCase();

  if (normalized === "INJECTING") {
    return "event";
  }

  if (
    normalized === "POST_EVENT" ||
    normalized === "ANALYZING" ||
    normalized === "COMPLETE"
  ) {
    return "post";
  }

  return "pre";
}

function liveValues(vitals = {}) {
  return {
    heartRate: finite(
      vitals.heartRate ??
      vitals.hr ??
      vitals.heart_rate
    ),
    spo2: finite(
      vitals.spo2 ??
      vitals.SpO2 ??
      vitals.oxygenSaturation ??
      vitals.oxygen_saturation
    ),
    systolic: finite(
      vitals.systolic ??
      vitals.sbp ??
      vitals.systolicBloodPressure
    ),
    diastolic: finite(
      vitals.diastolic ??
      vitals.dbp ??
      vitals.diastolicBloodPressure
    ),
    respiratoryRate: finite(
      vitals.respiratoryRate ??
      vitals.rr ??
      vitals.respiratory_rate
    ),
    temperature: finite(
      vitals.temperature ??
      vitals.temp ??
      vitals.bodyTemperature ??
      vitals.body_temperature
    ),
  };
}

export function resolveBedsideWidgetValues({
  enabled,
  scenarioId,
  injectionState,
  vitals,
}) {
  const live = liveValues(vitals);

  if (!enabled) {
    return live;
  }

  const phase = phaseFromState(
    injectionState
  );
  const scenario =
    SCENARIO_WIDGET_VALUES[
      scenarioId
    ] || DEFAULTS;
  const fallback =
    scenario[phase] ||
    DEFAULTS[phase];

  /*
   * During the controlled episode, the scenario values represent the
   * SLM_Eval episode pack. During pre/post capture, live source values are
   * retained when present and only missing values receive a fallback.
   */
  if (phase === "event") {
    return {
      heartRate:
        finite(fallback.heartRate),
      spo2:
        finite(fallback.spo2),
      systolic:
        finite(fallback.systolic),
      diastolic:
        finite(fallback.diastolic),
      respiratoryRate:
        finite(
          fallback.respiratoryRate
        ),
      temperature:
        finite(fallback.temperature),
    };
  }

  return {
    heartRate:
      live.heartRate ??
      fallback.heartRate,
    spo2:
      live.spo2 ??
      fallback.spo2,
    systolic:
      live.systolic ??
      fallback.systolic,
    diastolic:
      live.diastolic ??
      fallback.diastolic,
    respiratoryRate:
      live.respiratoryRate ??
      fallback.respiratoryRate,
    temperature:
      live.temperature ??
      fallback.temperature,
  };
}
