const LEAD_MAP = {
  I: "lead1",
  II: "lead2",
  III: "lead3",
  aVR: "avr",
  aVL: "avl",
  aVF: "avf",
};

function numberOrNull(value) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return null;
  }

  const number = Number(value);

  return Number.isFinite(number)
    ? number
    : null;
}

function centerLeadForDisplay(values = []) {
  const clean = values
    .map(Number)
    .filter(Number.isFinite);

  if (!clean.length) {
    return [];
  }

  const sorted = [...clean].sort(
    (a, b) => a - b
  );

  const middle = Math.floor(
    sorted.length / 2
  );

  const baseline =
    sorted.length % 2 === 0
      ? (sorted[middle - 1] +
          sorted[middle]) /
        2
      : sorted[middle];

  return clean.map((value) =>
    Number((value - baseline).toFixed(6))
  );
}

function mapWaveforms(ecg) {
  const leadNames = Array.isArray(
    ecg?.leadNames
  )
    ? ecg.leadNames
    : Object.keys(ecg?.waveform || {});

  const waveforms = Object.fromEntries(
    leadNames
      .filter((leadName) => LEAD_MAP[leadName])
      .map((leadName) => {
        const rawValues = Array.isArray(
          ecg?.waveform?.[leadName]
        )
          ? ecg.waveform[leadName]
          : [];

        return [
          LEAD_MAP[leadName],
          centerLeadForDisplay(rawValues),
        ];
      })
  );

  return {
    leadNames,
    leadIds: leadNames
      .map((leadName) => LEAD_MAP[leadName])
      .filter(Boolean),
    waveforms,
  };
}

export function adaptEvaluationEpisode(record) {
  const ecg = record?.ecg || {};
  const ppg = record?.ppg || {};
  const vitals = record?.vitals || {};
  const bloodPressure =
    vitals.bloodPressure || {};
  const mapped = mapWaveforms(ecg);

  return {
    mode: "evaluation",
    synthetic: true,

    schemaVersion:
      record?.schemaVersion ||
      "episode-slm-eval-v1",

    episodeId: record?.episodeId,
    capturedAt: record?.capturedAt,

    patient: {
      ...(record?.patient || {}),
      synthetic: true,
      sourceLabel:
        "Synthetic CARDINAL evaluation patient",
    },

    episode: {
      ...(record?.episode || {}),
      id: record?.episodeId,
    },

    ecg: {
      leadNames: mapped.leadNames,
      leadIds: mapped.leadIds,
      waveforms: mapped.waveforms,

      sampleRate:
        numberOrNull(ecg.sampleRate) || 250,

      durationSeconds:
        numberOrNull(
          ecg.durationSeconds ??
            record?.episode?.durationSeconds
        ) || 8,

      measurements: ecg.measurements || {},

      paperSpeedMmPerSec:
        numberOrNull(
          ecg.gridPaperSpeedMmPerSec
        ) || 25,

      gainMmPerMv:
        numberOrNull(ecg.gainMmPerMv) || 10,
    },

    ppg: {
      ...ppg,

      sampleRate:
        numberOrNull(ppg.sampleRate) || 125,

      durationSeconds:
        numberOrNull(ppg.durationSeconds) || 8,

      waveform: Array.isArray(ppg.waveform)
        ? ppg.waveform
            .map(Number)
            .filter(Number.isFinite)
        : [],
    },

    vitals: {
      heartRate: numberOrNull(
        vitals.heartRateBpm
      ),

      spo2: numberOrNull(vitals.spo2Pct),

      respiratoryRate: numberOrNull(
        vitals.respiratoryRateBpm
      ),

      temperature: numberOrNull(
        vitals.temperatureC
      ),

      systolic: numberOrNull(
        bloodPressure.systolic
      ),

      diastolic: numberOrNull(
        bloodPressure.diastolic
      ),

      map: numberOrNull(bloodPressure.map),

      bloodPressureNote:
        bloodPressure.note || null,
    },

    labs: record?.labs || {},
    medications: record?.medications || {},

    clinicalContext:
      record?.clinicalContext || {},
  };
}