function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function fieldKey(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

function findTrend(trends, aliases) {
  const keys = new Set(aliases.map(fieldKey));
  return (Array.isArray(trends) ? trends : []).find((item) =>
    keys.has(fieldKey(item?.field || item?.label))
  ) || null;
}

function trendValues(item) {
  return (Array.isArray(item?.points) ? item.points : [])
    .map((point) => finiteNumber(point?.value))
    .filter((value) => value !== null);
}

export function formatContextDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return new Intl.DateTimeFormat("en-US", {
    month: "2-digit",
    day: "2-digit",
    year: "2-digit",
  }).format(date);
}

export function humanizeContextRelation(item) {
  const minutes = finiteNumber(
    item?.minutesFromAnchor ?? item?.latestMinutesFromAnchor
  );

  if (minutes !== null) {
    const absolute = Math.abs(minutes);
    const direction = minutes > 0 ? "after event" : "before event";
    if (absolute < 120) return `${Math.round(absolute)} min ${direction}`;
    if (absolute < 2880) return `${(absolute / 60).toFixed(1)} h ${direction}`;
    return `${(absolute / 1440).toFixed(1)} d ${direction}`;
  }

  const label = String(
    item?.latestRelationLabel || item?.relationLabel || ""
  ).trim();

  const match = label.match(/^([\d.]+)\s*min\s*(before|after)/i);
  if (match) {
    const parsed = Number(match[1]);
    if (Number.isFinite(parsed)) {
      const signed = match[2].toLowerCase() === "after" ? parsed : -parsed;
      return humanizeContextRelation({ minutesFromAnchor: signed });
    }
  }

  return label || "Timing unavailable";
}

export function buildOracleLabCards(context) {
  return (Array.isArray(context?.labTrends) ? context.labTrends : [])
    .map((item) => {
      const values = trendValues(item);
      const points = Array.isArray(item?.points) ? item.points : [];
      const first = points[0] || null;
      const last = points[points.length - 1] || null;
      const relation = humanizeContextRelation(item);
      const relationMinutes = finiteNumber(
        item?.latestMinutesFromAnchor ?? last?.minutesFromAnchor
      );
      const historical =
        item?.temporalBucket === "historical_remote" ||
        (relationMinutes !== null && Math.abs(relationMinutes) > 60 * 24 * 30);

      return {
        name: item?.label || item?.field || "Laboratory result",
        value: item?.latestValue,
        unit: item?.unit || "",
        status: historical
          ? "Historical"
          : item?.classification === "red" || item?.color === "red"
          ? "High/Critical"
          : item?.classification === "yellow" || item?.color === "yellow"
          ? "Review"
          : "Recorded",
        trend: values,
        color: historical ? "blue" : item?.color || item?.classification || "blue",
        firstDate: formatContextDate(first?.observedAt),
        lastDate: formatContextDate(item?.latestAt || last?.observedAt),
        relation,
        source: "Oracle FHIR",
        meta: `${formatContextDate(item?.latestAt || last?.observedAt)} • ${relation}`,
        historical,
      };
    })
    .filter((item) => item.value !== null && item.value !== undefined)
    .slice(0, 8);
}

export function buildOracleVitalRows(context) {
  const trends = Array.isArray(context?.vitalTrends)
    ? context.vitalTrends
    : [];

  const definitions = [
    ["Heart Rate", ["heartRate", "heart rate"], "bpm", 0],
    ["Respiratory Rate", ["respiratoryRate", "respiratory rate"], "br/min", 0],
    ["SpO₂", ["spo2", "oxygen saturation"], "%", 0],
    ["Temperature", ["temperature", "body temperature"], "°C", 1],
    ["Systolic BP", ["systolic", "systolic blood pressure"], "mmHg", 0],
    ["Diastolic BP", ["diastolic", "diastolic blood pressure"], "mmHg", 0],
  ];

  return definitions
    .map(([label, aliases, fallbackUnit, decimals]) => {
      const item = findTrend(trends, aliases);
      const value = finiteNumber(item?.latestValue);
      if (!item || value === null) return null;

      return [
        label,
        value.toFixed(decimals),
        item?.unit || fallbackUnit,
        `${formatContextDate(item?.latestAt)} • ${humanizeContextRelation(item)}`,
      ];
    })
    .filter(Boolean);
}

export function buildOracleLivePatch(context) {
  const vitals = Array.isArray(context?.vitalTrends)
    ? context.vitalTrends
    : [];
  const labs = Array.isArray(context?.labTrends)
    ? context.labTrends
    : [];

  const vital = (aliases) => findTrend(vitals, aliases);
  const lab = (aliases) => findTrend(labs, aliases);
  const latest = (item) => finiteNumber(item?.latestValue);

  const heartRate = vital(["heartRate", "heart rate"]);
  const respiratoryRate = vital(["respiratoryRate", "respiratory rate"]);
  const spo2 = vital(["spo2", "oxygen saturation"]);
  const temperature = vital(["temperature", "body temperature"]);
  const systolic = vital(["systolic", "systolic blood pressure"]);
  const diastolic = vital(["diastolic", "diastolic blood pressure"]);
  const glucose = lab(["glucose"]);
  const potassium = lab(["potassium"]);
  const creatinine = lab(["creatinine"]);
  const wbc = lab(["wbc", "white blood cell count"]);

  return {
    firelyStatus: "connected",
    firelySource: "oracle-smart-fhir",
    streamTimestamp:
      context?.contextAnchor?.value ||
      heartRate?.latestAt ||
      spo2?.latestAt ||
      temperature?.latestAt ||
      null,
    heartRate: latest(heartRate),
    respiratoryRate: latest(respiratoryRate),
    spo2: latest(spo2),
    systolic: latest(systolic),
    diastolic: latest(diastolic),
    temperature: latest(temperature),
    glucose: latest(glucose),
    potassium: latest(potassium),
    creatinine: latest(creatinine),
    wbc: latest(wbc),
    heartTrend: trendValues(heartRate),
    respTrend: trendValues(respiratoryRate),
    spo2Trend: trendValues(spo2),
    glucoseTrend: trendValues(glucose),
    potassiumTrend: trendValues(potassium),
    creatinineTrend: trendValues(creatinine),
    wbcTrend: trendValues(wbc),
  };
}

export function selectOracleMedicationRows(
  context,
  limit = 10
) {
  const rows = Array.isArray(
    context?.medicationTimeline
  )
    ? context.medicationTimeline
    : [];

  const evidenceRank = (item) => {
    const value = String(
      item?.evidenceLevel ||
        item?.resourceType ||
        ""
    ).toLowerCase();

    if (value.includes("administr")) {
      return 0;
    }
    if (value.includes("dispens")) {
      return 1;
    }
    return 2;
  };

  const ranked = [...rows].sort(
    (a, b) => {
      const rankDifference =
        evidenceRank(a) -
        evidenceRank(b);

      if (rankDifference) {
        return rankDifference;
      }

      return String(
        b?.eventTime || ""
      ).localeCompare(
        String(a?.eventTime || "")
      );
    }
  );

  const seen = new Set();
  const unique = [];

  for (const row of ranked) {
    const day = String(
      row?.eventTime || ""
    ).slice(0, 10);

    const key = [
      String(
        row?.name ||
          row?.med ||
          row?.medication ||
          ""
      )
        .trim()
        .toLowerCase(),
      String(
        row?.resourceType ||
          row?.evidenceLevel ||
          ""
      )
        .trim()
        .toLowerCase(),
      String(row?.status || "")
        .trim()
        .toLowerCase(),
      String(
        row?.doseDisplay ||
          row?.dose ||
          [
            row?.doseValue,
            row?.doseUnit,
          ]
            .filter(Boolean)
            .join(" ")
      )
        .trim()
        .toLowerCase(),
      String(row?.route || "")
        .trim()
        .toLowerCase(),
      day,
    ].join("|");

    if (seen.has(key)) {
      continue;
    }

    seen.add(key);
    unique.push(row);

    if (unique.length >= limit) {
      break;
    }
  }

  return unique.map((row) => {
    const timing =
      `${formatContextDate(
        row?.eventTime
      )} • ${humanizeContextRelation(
        row
      )}`;

    return {
      ...row,
      med:
        row?.med ||
        row?.name ||
        row?.medication ||
        "Medication",
      name:
        row?.name ||
        row?.med ||
        row?.medication ||
        "Medication",
      dose:
        row?.dose ||
        row?.doseDisplay ||
        row?.doseText ||
        [
          row?.doseValue,
          row?.doseUnit,
        ]
          .filter(Boolean)
          .join(" ") ||
        "--",
      route:
        row?.route || "--",
      status:
        row?.evidenceLevel ===
        "administration"
          ? "administered"
          : row?.evidenceLevel ===
            "dispense"
          ? "dispensed"
          : "ordered",
      contextTiming: timing,
      relationLabel: timing,
    };
  });
}
