function asObject(value) {
  return (
    value &&
    typeof value === "object" &&
    !Array.isArray(value)
      ? value
      : {}
  );
}

function asArray(value) {
  return Array.isArray(value)
    ? value
    : [];
}

function valueAndUnit(entry) {
  if (
    entry &&
    typeof entry === "object" &&
    !Array.isArray(entry)
  ) {
    return {
      value:
        entry.value ??
        entry.result ??
        entry.latestValue ??
        null,
      unit:
        entry.unit ??
        entry.units ??
        "",
      status:
        entry.status ??
        entry.flag ??
        entry.classification ??
        "",
    };
  }

  return {
    value: entry ?? null,
    unit: "",
    status: "",
  };
}

function hasValue(value) {
  return !(
    value === null ||
    value === undefined ||
    value === ""
  );
}

function prettify(value) {
  return String(value || "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) =>
      letter.toUpperCase()
    );
}

function statusLabel(entry) {
  const status = String(
    entry?.status || ""
  ).toLowerCase();

  if (
    status.includes("critical") ||
    status.includes("high") ||
    status.includes("low") ||
    status.includes("abnormal")
  ) {
    return entry.status || "Abnormal";
  }

  return "Episode context";
}

function normalizeSex(value) {
  const text = String(value || "").trim();

  if (!text) return "--";
  if (text.toLowerCase() === "m") return "Male";
  if (text.toLowerCase() === "f") return "Female";

  return (
    text[0].toUpperCase() +
    text.slice(1)
  );
}

function splitMedicationText(value) {
  const text = String(value || "").trim();
  const match = text.match(
    /^(.*?)(?:\s+)(\d+(?:\.\d+)?\s*(?:mcg|mg|g|ml|mL|units?|IU)(?:\s*\/\s*\w+)?)$/i
  );

  if (!match) {
    return {
      name: text,
      dose: "Dose not specified",
    };
  }

  return {
    name: match[1].trim(),
    dose: match[2].trim(),
  };
}

export function getEpisodePack(episode) {
  return (
    episode?.evaluationScenario ||
    episode?.episodePack ||
    null
  );
}

export function buildEpisodePackPatient(episode) {
  const pack = getEpisodePack(episode);
  const patient = asObject(pack?.patient);

  if (!Object.keys(patient).length) {
    return null;
  }

  const name =
    patient.name ||
    patient.display ||
    "Episode patient";

  return {
    ...patient,
    id:
      patient.id ||
      patient.mrn ||
      pack?.episodeId,
    fhirId: null,
    name,
    display: name,
    mrn:
      patient.mrn ||
      patient.id ||
      "--",
    dob:
      patient.dob ||
      patient.birthDate ||
      "--",
    age: patient.age ?? null,
    sex: normalizeSex(
      patient.sex ||
      patient.gender
    ),
    gender:
      patient.gender ||
      patient.sex ||
      null,
    room:
      patient.room ||
      patient.unit ||
      "Episode pack",
    unit: "Episode pack",
    location: "Episode pack",
    source: "Complete episode pack",
  };
}

export function buildEpisodePackLabCards(episode) {
  const pack = getEpisodePack(episode);
  const labs = asObject(pack?.labs);

  return Object.entries(labs)
    .map(([field, raw]) => {
      const entry = valueAndUnit(raw);

      if (!hasValue(entry.value)) {
        return null;
      }

      return {
        field,
        name:
          raw?.label ||
          raw?.display ||
          prettify(field),
        value: entry.value,
        unit: entry.unit,
        status: statusLabel(entry),
        meta: "Episode time",
        trend: [entry.value],
        source: "Complete episode pack",
        temporalBucket: "episode_near",
      };
    })
    .filter(Boolean);
}

function vitalRow({
  field,
  label,
  value,
  unit = "",
  detail = "",
}) {
  return {
    field,
    label,
    parameter: label,
    value,
    unit,
    date: "Controlled event",
    relationLabel: "Controlled event",
    timing: "Controlled event",
    detail,
    source: "Complete episode pack",
  };
}

export function buildEpisodePackVitalRows(episode) {
  const pack = getEpisodePack(episode);
  const vitals = asObject(pack?.vitals);
  const bloodPressure = asObject(
    vitals.bloodPressure ||
    vitals.bp
  );
  const rows = [];

  const heartRate = valueAndUnit(
    vitals.heartRateBpm ??
    vitals.heartRate
  );
  const respiratoryRate = valueAndUnit(
    vitals.respiratoryRateBpm ??
    vitals.respiratoryRate
  );
  const spo2 = valueAndUnit(
    vitals.spo2Pct ??
    vitals.spo2
  );
  const temperature = valueAndUnit(
    vitals.temperatureC ??
    vitals.temperature
  );

  if (hasValue(heartRate.value)) {
    rows.push(vitalRow({
      field: "heartRateBpm",
      label: "Heart Rate",
      value: heartRate.value,
      unit: heartRate.unit || "bpm",
    }));
  }

  if (hasValue(respiratoryRate.value)) {
    rows.push(vitalRow({
      field: "respiratoryRateBpm",
      label: "Respiratory Rate",
      value: respiratoryRate.value,
      unit:
        respiratoryRate.unit ||
        "breaths/min",
    }));
  }

  if (hasValue(spo2.value)) {
    rows.push(vitalRow({
      field: "spo2Pct",
      label: "SpO₂",
      value: spo2.value,
      unit: spo2.unit || "%",
    }));
  }

  if (hasValue(temperature.value)) {
    rows.push(vitalRow({
      field: "temperatureC",
      label: "Temperature",
      value: temperature.value,
      unit: temperature.unit || "°C",
    }));
  }

  const systolic =
    bloodPressure.systolic ??
    vitals.systolic;
  const diastolic =
    bloodPressure.diastolic ??
    vitals.diastolic;
  const map =
    bloodPressure.map ??
    bloodPressure.meanArterialPressure ??
    vitals.map ??
    vitals.meanArterialPressure;
  const note =
    bloodPressure.note ||
    vitals.bloodPressureNote ||
    vitals.note ||
    "";

  if (
    hasValue(systolic) &&
    hasValue(diastolic)
  ) {
    rows.push(vitalRow({
      field: "bloodPressure",
      label: "Blood Pressure",
      value: `${systolic}/${diastolic}`,
      unit: "mmHg",
      detail: note,
    }));
  }

  if (hasValue(map)) {
    rows.push(vitalRow({
      field: "meanArterialPressure",
      label: "Mean Arterial Pressure",
      value: map,
      unit: "mmHg",
      detail: note,
    }));
  }

  if (
    note &&
    !rows.some((row) => row.detail === note)
  ) {
    rows.push(vitalRow({
      field: "hemodynamicContext",
      label: "Hemodynamic Context",
      value: note,
      unit: "",
      detail: "Episode-pack observation",
    }));
  }

  return rows;
}

export function buildEpisodePackMedicationRows(
  episode,
  limit = 10
) {
  const pack = getEpisodePack(episode);
  const patient = asObject(pack?.patient);

  const medicationSources = [
    ...asArray(pack?.medications).map(
      (value) => ({
        value,
        context: "During episode",
        status: "Episode medication",
      })
    ),
    ...asArray(patient?.homeMedications).map(
      (value) => ({
        value,
        context: "Home medication",
        status: "Medication history",
      })
    ),
  ];

  const seen = new Set();
  const rows = [];

  for (
    let index = 0;
    index < medicationSources.length;
    index += 1
  ) {
    const sourceItem = medicationSources[index];
    const raw = sourceItem.value;
    const item =
      typeof raw === "string"
        ? { name: raw }
        : asObject(raw);

    const rawName =
      item.name ||
      item.medication ||
      item.drug ||
      item.display;

    if (!rawName) continue;

    const parsed = splitMedicationText(rawName);
    const name =
      item.medicationName ||
      parsed.name;
    const dose =
      item.doseDisplay ||
      item.dose ||
      item.doseText ||
      parsed.dose;
    const route =
      item.route ||
      "Route not specified";
    const contextTiming =
      item.contextTiming ||
      item.relationLabel ||
      sourceItem.context;
    const status =
      item.status ||
      sourceItem.status;

    const key = [
      String(name).toLowerCase(),
      String(dose).toLowerCase(),
      String(route).toLowerCase(),
    ].join("|");

    if (seen.has(key)) continue;
    seen.add(key);

    rows.push({
      id:
        item.id ||
        `episode-pack-med-${index}`,
      name,
      med: name,
      dose,
      doseDisplay: dose,
      route,
      status,
      resourceType: "Episode pack",
      evidenceLevel: "episode_pack",
      contextTiming,
      relationLabel: contextTiming,
      source: "Complete episode pack",
    });

    if (rows.length >= limit) break;
  }

  return rows;
}

export function buildEpisodePackLivePatch(episode) {
  const pack = getEpisodePack(episode);
  const vitals = asObject(pack?.vitals);
  const bloodPressure = asObject(
    vitals.bloodPressure ||
    vitals.bp
  );

  return {
    heartRate:
      valueAndUnit(
        vitals.heartRateBpm ??
        vitals.heartRate
      ).value,
    respiratoryRate:
      valueAndUnit(
        vitals.respiratoryRateBpm ??
        vitals.respiratoryRate
      ).value,
    spo2:
      valueAndUnit(
        vitals.spo2Pct ??
        vitals.spo2
      ).value,
    temperature:
      valueAndUnit(
        vitals.temperatureC ??
        vitals.temperature
      ).value,
    systolic:
      bloodPressure.systolic ??
      vitals.systolic ??
      null,
    diastolic:
      bloodPressure.diastolic ??
      vitals.diastolic ??
      null,
    meanArterialPressure:
      bloodPressure.map ??
      bloodPressure.meanArterialPressure ??
      vitals.map ??
      vitals.meanArterialPressure ??
      null,
  };
}
