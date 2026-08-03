import { useEffect, useMemo, useState } from "react";
import "./ClinicalPhysiologyPage.css";
import "./CloudDemoAnalyticsAdditions.css";
import { connectFhirStream } from "../services/fhirStream";
import WebGLWaveformCanvas from "./WebGLWaveformCanvas";
import {
  adaptEvaluationRunToWidget,
} from "../evaluation/evaluationWidgetAdapter";
import {
  buildEvaluationTriggerMarkers,
  summarizeTriggerMarkers,
} from "../evaluation/evaluationTriggerMarkers";
import {
  buildOracleLabCards,
  buildOracleVitalRows,
  buildOracleLivePatch,
  selectOracleMedicationRows,
} from "../evaluation/clinicalContextDisplay";
import {
  buildEpisodePackLabCards,
  buildEpisodePackLivePatch,
  buildEpisodePackMedicationRows,
  buildEpisodePackPatient,
  buildEpisodePackVitalRows,
  getEpisodePack,
} from "../evaluation/episodePackDisplay";
import {
  connectEpisodeEvents,
  getEpisode,
  getEpisodeWaveforms,
  getLatestEpisode,
  getIncidentContext,
  loadIncidentContext,
  listIncidents,
  getIncidentEpisodes,
  getSlmWidget,
} from "../services/episodeService";
import IncidentEpisodeCarousel
  from "./IncidentEpisodeCarousel";

import CriticalInterpretationWidget
  from "./CriticalInterpretationWidget";


const MAX_POINTS = 360;
const CURRENT_MARK_RATIO = 0.47;
const STATIC_ANALYTICS_MODE = false;

const STATIC_ANALYTICS_LABS = [
  {
    name: "Glucose",
    value: 234,
    status: "High/Critical",
    meta: "07/18",
    trend: [125, 139, 141, 234],
    color: "red",
  },
  {
    name: "Potassium",
    value: "5.5",
    status: "High/Critical",
    meta: "07/18",
    trend: [3.9, 4.2, 5.1, 5.5],
    color: "red",
  },
  {
    name: "Creatinine",
    value: "1.47",
    status: "High/Critical",
    meta: "07/18",
    trend: [0.89, 1.05, 1.23, 1.47],
    color: "red",
  },
  {
    name: "WBC",
    value: "11.6",
    status: "High/Critical",
    meta: "07/18",
    trend: [8.2, 9.1, 10.4, 11.6],
    color: "red",
  },
];

const STATIC_ANALYTICS_VITAL_ROWS = [
  ["BP", "127/84", "mmHg", "07/16/25"],
  ["SpO2", "97", "%", "07/16/25"],
  ["Oral Temperature", "37.2", "°C", "07/16/25"],
];

const BASE_PATIENT = {
  name: "Leslie Abbott",
  sex: "FEMALE",
  dob: "1946-08-22",
  id: "87675858"
};

function formatLiveClock(date = new Date()) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(date);
}

const MEDICATION_ROWS = [
  {
    med: "Simvastatin",
    sub: "Bedtime",
    dose: "5mg",
    taken: [{ ok: true, time: "20:00" }],
    date: "07/16/25"
  },
  {
    med: "Spironolactone",
    sub: "q12hr",
    dose: "25mg",
    taken: [
      { ok: true, time: "08:00" },
      { ok: false, time: "20:00" }
    ],
    date: "07/16/25"
  },
  {
    med: "Oral Temperature",
    sub: "",
    dose: "37.2",
    warning: true,
    taken: [{ ok: false, time: "20:00" }],
    date: "07/10/25"
  }
];

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function appendValues(series, values) {
  return [...series.slice(values.length), ...values];
}

function buildStrip(factory, tick = 0) {
  return Array.from({ length: MAX_POINTS }, (_, index) => factory(index, tick));
}

function ecgValue(index, tick = 0) {
  const crisisStart = Math.floor(MAX_POINTS * CURRENT_MARK_RATIO);
  const animatedIndex = index + tick * 1.8;
  const beat = (animatedIndex * 0.083) % 1;

  let value = 0.5 + Math.sin(animatedIndex * 0.035) * 0.015;

  if (index < crisisStart) {
    if (beat < 0.025) value = 0.5;
    else if (beat < 0.043) value = 0.62;
    else if (beat < 0.058) value = 0.25;
    else if (beat < 0.076) value = 0.91;
    else if (beat < 0.11) value = 0.43;
    else if (beat < 0.22) value = 0.52 + Math.sin(beat * Math.PI * 6) * 0.045;
    else value = 0.5 + Math.sin(animatedIndex * 0.1) * 0.018;
  } else {
    const wideBeat = (animatedIndex * 0.038) % 1;
    value =
      0.5 +
      Math.sin(wideBeat * Math.PI * 2) * 0.31 +
      Math.sin(animatedIndex * 0.24) * 0.055;
  }

  return clamp(value, 0.08, 0.95);
}

function redRhythmValue(index, tick = 0) {
  const crisisStart = Math.floor(MAX_POINTS * CURRENT_MARK_RATIO);
  const animatedIndex = index + tick * 1.6;
  const beat = (animatedIndex * 0.085) % 1;

  let value = 0.5 + Math.sin(animatedIndex * 0.04) * 0.012;

  if (index < crisisStart) {
    if (beat < 0.03) value = 0.5;
    else if (beat < 0.047) value = 0.62;
    else if (beat < 0.06) value = 0.34;
    else if (beat < 0.078) value = 0.74;
    else if (beat < 0.13) value = 0.48;
  } else {
    value =
      0.5 +
      Math.sin(animatedIndex * 0.18) * 0.18 +
      Math.sin(animatedIndex * 0.42) * 0.035;
  }

  return clamp(value, 0.12, 0.88);
}

function ppgValue(index, tick = 0, soft = false) {
  const crisisStart = Math.floor(MAX_POINTS * CURRENT_MARK_RATIO);
  const animatedIndex = index + tick * 1.4;
  const beat = (animatedIndex * 0.058) % 1;

  let pulse =
    beat < 0.11
      ? Math.sin((beat / 0.11) * Math.PI) * 0.58
      : Math.exp(-beat * 4.8) * 0.23;

  if (soft) pulse *= 0.62;

  let value = 0.34 + pulse + Math.sin(animatedIndex * 0.055) * 0.018;

  if (index > crisisStart) {
    value += Math.sin(animatedIndex * 0.35) * 0.045;
  }

  return clamp(value, 0.08, 0.94);
}

function buildSeries(factory) {
  return Array.from({ length: MAX_POINTS }, (_, index) => factory(index));
}

const DEFAULT_ALERT_INTERPRETATION = {
  title: "(!) Critical abnormalities detected",
  rhythm:
    "Sinus rhythm with peaked T waves progressing to QRS widening, sine-wave morphology with loss of P waves, agonal complexes, and ventricular fibrillation.",
  ppg:
    "Normal pulsatile waveform with dicrotic notch, degrading amplitude, lasting to ventricular fibrillation onset.",
  likelyEtiology:
    "Hyperkalemic arrest in a patient on spironolactone with history of intermittent hyperkalemia, possibly precipitated by drug interaction, drug overdose, or recent renal impairment during K+ to lethal levels."
};

function appendOne(series, value, max = 8) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) {
    return series;
  }

  return [...series, Number(value)].slice(-max);
}

function normalizeColor(color, fallback = "blue") {
  if (color === "red" || color === "yellow" || color === "blue") {
    return color;
  }

  return fallback;
}

function statusFromColor(color) {
  if (color === "red") return "High/Critical";
  if (color === "yellow") return "Warning";
  return "Stable";
}

function formatStreamDate(timestamp) {
  if (!timestamp) return "07/16/25";

  const date = new Date(timestamp);

  if (Number.isNaN(date.getTime())) {
    return "07/16/25";
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "2-digit",
    day: "2-digit",
    year: "2-digit"
  }).format(date);
}

function mergeFirelyFrameIntoLive(prev, frame) {
  if (!frame) return prev;

  if (frame.status === "error" || frame.error) {
    return {
      ...prev,
      firelyStatus: "error",
      alertColor: "yellow",
      alertInterpretation: frame.interpretation || {
        title: "Firely stream warning",
        rhythm: "The dashboard could not fetch the latest Firely Observations.",
        ppg: "Local waveform simulation is still running.",
        likelyEtiology: "Check whether the FastAPI backend is running on port 8000."
      }
    };
  }

  const vitals = frame.vitals || {};
  const labs = frame.labs || {};
  const colors = frame.colors || {};

  const next = {
    ...prev,
    firelyStatus: frame.status || "connected",
    firelySource: frame.source || "firely-public-sandbox",
    streamTimestamp: frame.timestamp || frame.receivedAt,
    fallbackUsed: frame.fallbackUsed || [],


  priorityTrends: frame.priorityTrends || prev.priorityTrends || [],
  medicationRows: frame.medicationRows || prev.medicationRows || MEDICATION_ROWS,
  contextAlerts: frame.contextAlerts || prev.contextAlerts || [],


    alertColor: normalizeColor(frame.overallColor || frame.color, prev.alertColor || "red"),
    alertInterpretation: frame.interpretation || prev.alertInterpretation,

    colors: {
      ...prev.colors,
      ...colors
    },

    heartRate: vitals.heartRate ?? prev.heartRate,
    respiratoryRate: vitals.respiratoryRate ?? prev.respiratoryRate,
    spo2: vitals.spo2 ?? prev.spo2,
    systolic: vitals.systolic ?? prev.systolic,
    diastolic: vitals.diastolic ?? prev.diastolic,
    temperature: vitals.temperature ?? prev.temperature,

    glucose: labs.glucose ?? prev.glucose,
    potassium: labs.potassium ?? prev.potassium,
    creatinine: labs.creatinine ?? prev.creatinine,
    wbc: labs.wbc ?? prev.wbc
  };

  return {
    ...next,
    heartTrend: appendOne(prev.heartTrend, next.heartRate),
    respTrend: appendOne(prev.respTrend, next.respiratoryRate),
    spo2Trend: appendOne(prev.spo2Trend, next.spo2),
    glucoseTrend: appendOne(prev.glucoseTrend, next.glucose),
    potassiumTrend: appendOne(prev.potassiumTrend, next.potassium),
    creatinineTrend: appendOne(prev.creatinineTrend, next.creatinine),
    wbcTrend: appendOne(prev.wbcTrend, next.wbc)
  };
}

function getLiveColor(live, field, fallback = "blue") {
  return normalizeColor(live.colors?.[field], fallback);
}

function createInitialLiveState() {
  return {
    tick: 0,
    clockText: formatLiveClock(),
    heartRate: 160,
    respiratoryRate: 35,
    spo2: 99,
    systolic: 130,
    diastolic: 85,
    temperature: 37.2,
    glucose: 225,
    potassium: 5.4,
    creatinine: 1.42,
    wbc: 12.1,
    ecg: buildStrip(ecgValue, 0),
    resp: buildStrip(redRhythmValue, 0),
    ppg: buildStrip((index, tick) => ppgValue(index, tick, false), 0),
    ppgSoft: buildStrip((index, tick) => ppgValue(index, tick, true), 0),
    heartTrend: [122, 130, 139, 148, 160],
    respTrend: [18, 21, 24, 29, 35],
    spo2Trend: [97, 98, 97, 99, 99],
    glucoseTrend: [125, 139, 141, 205, 225],
    potassiumTrend: [3.9, 4.2, 4.6, 5.1, 5.4],
    creatinineTrend: [0.89, 0.96, 1.05, 1.23, 1.42],
    wbcTrend: [8.2, 9.1, 10.4, 11.2, 12.1],
    firelyStatus: "local",
firelySource: "local-simulation",
streamTimestamp: null,
fallbackUsed: [],
priorityTrends: [],
medicationRows: MEDICATION_ROWS,
alertColor: "red",
alertInterpretation: DEFAULT_ALERT_INTERPRETATION,
colors: {
  heartRate: "red",
  respiratoryRate: "yellow",
  spo2: "blue",
  systolic: "blue",
  diastolic: "blue",
  temperature: "yellow",
  glucose: "red",
  potassium: "red",
  creatinine: "red",
  wbc: "red"
}
  };
}

function nextLiveState(prev) {
  const tick = prev.tick + 1;
  const usingFirely = prev.firelyStatus === "connected";

  const simulatedHeartRate = Math.round(
    clamp(160 + Math.sin(tick / 4) * 7 + Math.sin(tick / 11) * 4, 146, 174)
  );

  const simulatedRespiratoryRate = Math.round(
    clamp(35 + Math.sin(tick / 5) * 3 + Math.sin(tick / 13) * 2, 29, 40)
  );

  const simulatedSpo2 = Math.round(
    clamp(98.5 + Math.sin(tick / 7) * 1.2, 96, 100)
  );

  const simulatedSystolic = Math.round(
    clamp(130 + Math.sin(tick / 8) * 4, 124, 138)
  );

  const simulatedDiastolic = Math.round(
    clamp(85 + Math.sin(tick / 9) * 3, 80, 90)
  );

  const simulatedTemperature = Number(
    clamp(37.2 + Math.sin(tick / 10) * 0.15, 37.0, 37.5).toFixed(1)
  );

  const simulatedGlucose = Math.round(
    clamp(225 + Math.sin(tick / 6) * 9, 214, 238)
  );

  const simulatedPotassium = Number(
    clamp(5.4 + Math.sin(tick / 8) * 0.15, 5.2, 5.7).toFixed(1)
  );

  const simulatedCreatinine = Number(
    clamp(1.42 + Math.sin(tick / 9) * 0.06, 1.34, 1.52).toFixed(2)
  );

  const simulatedWbc = Number(
    clamp(12.1 + Math.sin(tick / 7) * 0.5, 11.4, 12.9).toFixed(1)
  );

  const heartRate = usingFirely ? prev.heartRate : simulatedHeartRate;
  const respiratoryRate = usingFirely ? prev.respiratoryRate : simulatedRespiratoryRate;
  const spo2 = usingFirely ? prev.spo2 : simulatedSpo2;
  const systolic = usingFirely ? prev.systolic : simulatedSystolic;
  const diastolic = usingFirely ? prev.diastolic : simulatedDiastolic;
  const temperature = usingFirely ? prev.temperature : simulatedTemperature;
  const glucose = usingFirely ? prev.glucose : simulatedGlucose;
  const potassium = usingFirely ? prev.potassium : simulatedPotassium;
  const creatinine = usingFirely ? prev.creatinine : simulatedCreatinine;
  const wbc = usingFirely ? prev.wbc : simulatedWbc;

  const nextState = {
    ...prev,
    tick,
    clockText: formatLiveClock(),

    heartRate,
    respiratoryRate,
    spo2,
    systolic,
    diastolic,
    temperature,
    glucose,
    potassium,
    creatinine,
    wbc,

    ecg: buildStrip(ecgValue, tick),
    resp: buildStrip(redRhythmValue, tick),
    ppg: buildStrip((index, currentTick) => ppgValue(index, currentTick, false), tick),
    ppgSoft: buildStrip((index, currentTick) => ppgValue(index, currentTick, true), tick)
  };

  if (usingFirely) {
    return nextState;
  }

  return {
    ...nextState,
    heartTrend: appendValues(prev.heartTrend, [heartRate]).slice(-8),
    respTrend: appendValues(prev.respTrend, [respiratoryRate]).slice(-8),
    spo2Trend: appendValues(prev.spo2Trend, [spo2]).slice(-8),
    glucoseTrend: appendValues(prev.glucoseTrend, [glucose]).slice(-8),
    potassiumTrend: appendValues(prev.potassiumTrend, [potassium]).slice(-8),
    creatinineTrend: appendValues(prev.creatinineTrend, [creatinine]).slice(-8),
    wbcTrend: appendValues(prev.wbcTrend, [wbc]).slice(-8)
  };
}

function readEvaluationValue(entry) {
  if (
    entry === null ||
    entry === undefined ||
    entry === ""
  ) {
    return null;
  }

  const rawValue =
    typeof entry === "object"
      ? (
          entry.value ??
          entry.result ??
          entry.numericValue ??
          entry.measurement ??
          null
        )
      : entry;

  if (
    rawValue === null ||
    rawValue === undefined ||
    rawValue === ""
  ) {
    return null;
  }

  const numeric = Number(rawValue);

  return Number.isFinite(numeric)
    ? numeric
    : null;
}

function normalizeEvaluationMedications(
  medications
) {
  const source = Array.isArray(medications)
    ? medications
    : Array.isArray(medications?.items)
    ? medications.items
    : Array.isArray(medications?.current)
    ? medications.current
    : Array.isArray(medications?.active)
    ? medications.active
    : Array.isArray(medications?.medications)
    ? medications.medications
    : [];

  return source.map((item, index) => ({
    id:
      item.id ||
      `evaluation-med-${index}`,
    name:
      item.name ||
      item.medication ||
      item.drug ||
      "Medication",
    doseDisplay:
      item.doseDisplay ||
      item.dose ||
      "Unavailable",
    route: item.route || "",
    status: item.status || "listed",
    contextTiming:
      item.contextTiming ||
      item.timing ||
      item.time ||
      "Episode context",
    relationLabel:
      item.relationLabel ||
      item.contextTiming ||
      item.timing ||
      item.time ||
      "Episode context",
    resourceType: "Medication",
  }));
}

function formatClinicalNumber(
  value,
  decimals = 0
) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return "--";
  }

  const numeric = Number(value);

  if (!Number.isFinite(numeric)) {
    return "--";
  }

  return numeric.toFixed(decimals);
}

function formatBloodPressure(
  systolic,
  diastolic
) {
  const systolicText = formatClinicalNumber(
    systolic,
    0
  );
  const diastolicText = formatClinicalNumber(
    diastolic,
    0
  );

  if (
    systolicText === "--" ||
    diastolicText === "--"
  ) {
    return "--";
  }

  return `${systolicText}/${diastolicText}`;
}

function toPolylineNormalized(values, width, height, padding = 6) {
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - padding - value * (height - padding * 2);
      return `${x},${clamp(y, padding, height - padding)}`;
    })
    .join(" ");
}

function toPolylineScaled(values, width = 80, height = 34, padding = 4) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - padding - ((value - min) / range) * (height - padding * 2);
      return `${x},${clamp(y, padding, height - padding)}`;
    })
    .join(" ");
}

function WaveChart({
  label,
  color,
  values,
  compact = false,
  currentTime = false,
  clockText,
  onOpen,
  ariaLabel
}) {
  const ChartTag = onOpen ? "button" : "div";

  return (
    <ChartTag
      type={onOpen ? "button" : undefined}
      className={`kgen-wave-card ${compact ? "compact" : ""} ${color} ${
        onOpen ? "kgen-clickable-wave" : ""
      }`}
      onClick={onOpen}
      aria-label={ariaLabel}
    >
      {label && <span className={`kgen-wave-label ${color}`}>{label}</span>}

      {currentTime && (
        <>
          <span className="kgen-current-marker" />
          <span className="kgen-current-time">
            Current time: {clockText || formatLiveClock()}
          </span>
        </>
      )}

      <WebGLWaveformCanvas
        values={values}
        points={values?.length || 360}
        color={color}
        mode="unit"
      />

      {onOpen && <span className="kgen-open-wave-hint">↗</span>}
    </ChartTag>
  );
}
function MiniTrend({ values, color = "red", onOpen, ariaLabel }) {
  const TrendTag = onOpen ? "button" : "div";

  return (
    <TrendTag
      type={onOpen ? "button" : undefined}
      className={`kgen-mini-trend-box ${color} ${
        onOpen ? "kgen-clickable-trend" : ""
      }`}
      onClick={onOpen}
      aria-label={ariaLabel}
    >
      <WebGLWaveformCanvas
        values={values}
        points={96}
        color={color}
        mode="auto"
        className={`kgen-mini-trend ${color}`}
      />

      {onOpen && <span className="kgen-mini-open-dot">↗</span>}
    </TrendTag>
  );
}
function LabTile({
  name,
  value,
  status,
  meta,
  trend,
  color = "red",
  onOpenTrend
}) {
  const [firstDate = "06/23", secondDate = "07/18"] = String(
    meta || "06/23 07/18"
  ).split(" ");

  return (
    <article className={`kgen-lab-tile ${color}`}>
      <div className="kgen-lab-title">
        <span>{name}</span>

        <button
          type="button"
          aria-label={`Open ${name} lab trend`}
          onClick={onOpenTrend}
        >
          ›
        </button>
      </div>

      <div className="kgen-lab-value-row">
        <div className="kgen-lab-reading">
          <strong>{value}</strong>
          <small>{status}</small>
        </div>

        <div className="kgen-lab-spark-wrap">
          <MiniTrend
            values={trend}
            color={color}
            onOpen={onOpenTrend}
            ariaLabel={`Open ${name} trend popup`}
          />

          <div className="kgen-lab-spark-dates">
            <span>{firstDate}</span>
            <span>{secondDate}</span>
          </div>
        </div>
      </div>
    </article>
  );
}
function WaveformOverlay({ config, onClose }) {
  useEffect(() => {

    function handleKeyDown(event) {
      if (event.key === "Escape") onClose();
    }

    document.addEventListener("keydown", handleKeyDown);
    document.body.classList.add("kgen-wave-modal-open");

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.classList.remove("kgen-wave-modal-open");
    };
  }, [onClose]);

  if (!config) return null;

  const width = 980;
  const height = 340;

  const points =
    config.scaleMode === "scaled"
      ? toPolylineScaled(config.values, width, height, 24)
      : toPolylineNormalized(config.values, width, height, 24);

  const minValue = Math.min(...config.values);
  const maxValue = Math.max(...config.values);
  const latestValue = config.values[config.values.length - 1];

  return (
    <div className="kgen-wave-overlay-backdrop" onMouseDown={onClose}>
      <section
        className="kgen-wave-overlay-card"
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={config.title}
      >
        <header className="kgen-wave-overlay-header">
          <div>
            <p>{config.section}</p>
            <h2>{config.title}</h2>
            <span>{config.subtitle}</span>
          </div>

          <button
            type="button"
            className="kgen-wave-overlay-close"
            onClick={onClose}
            aria-label="Close waveform popup"
          >
            ×
          </button>
        </header>

        <div className="kgen-wave-overlay-stats">
          <div>
            <span>Current</span>
            <strong>
              {config.currentValue}
              {config.unit}
            </strong>
          </div>

          <div>
            <span>Status</span>
            <strong>{config.status}</strong>
          </div>

          <div>
            <span>Min</span>
            <strong>
              {config.scaleMode === "scaled" ? minValue.toFixed(config.decimals ?? 0) : "Live"}
            </strong>
          </div>

          <div>
            <span>Max</span>
            <strong>
              {config.scaleMode === "scaled" ? maxValue.toFixed(config.decimals ?? 0) : "Live"}
            </strong>
          </div>
        </div>

      <div className={`kgen-wave-overlay-chart ${config.color}`}>
  <WebGLWaveformCanvas
    values={config.values}
    points={Math.max(720, config.values?.length || 360)}
    color={config.color}
    mode={config.scaleMode === "scaled" ? "auto" : "unit"}
  />
</div>

        <footer className="kgen-wave-overlay-footer">
          <span>{config.footerLeft}</span>
          <span>{config.footerRight}</span>
        </footer>
      </section>
    </div>
  );
}


function normalizeEpisodeValues(values = []) {
  const clean = values
    .map(Number)
    .filter(Number.isFinite);

  if (clean.length < 2) {
    return Array.from(
      { length: MAX_POINTS },
      () => 0.5
    );
  }

  const stride = Math.max(
    1,
    Math.floor(clean.length / MAX_POINTS)
  );

  const sampled = clean.filter(
    (_, index) => index % stride === 0
  );

  const sorted = [...sampled].sort(
    (a, b) => a - b
  );

  const center =
    sorted[Math.floor(sorted.length / 2)];

  const absoluteValues = sampled
    .map((value) => Math.abs(value - center))
    .sort((a, b) => a - b);

  const scale =
    absoluteValues[
      Math.floor(
        (absoluteValues.length - 1) * 0.98
      )
    ] || 1;

  return sampled.map((value) =>
    clamp(
      0.5 + (value - center) / (scale * 2.4),
      0.05,
      0.95
    )
  );
}

function EpisodeWaveRow({
  label,
  values,
  color,
  compact = false,
  eventStartRatio,
  eventEndRatio,
  triggerAnnotations = [],
  duration = 1,
}) {
  return (
    <div
      className={`kgen-episode-wave-row ${
        compact ? "compact" : ""
      }`}
    >
      <WaveChart
        label={label}
        color={color}
        values={values}
        compact={compact}
      />

      <span
        className="kgen-episode-marker start"
        style={{
          left: `${eventStartRatio * 100}%`,
        }}
      />

      <span
        className="kgen-episode-marker end"
        style={{
          left: `${eventEndRatio * 100}%`,
        }}
      />

      {triggerAnnotations.map(
        (annotation) => {
          const seconds = Number(
            annotation
              ?.captureOffsetSeconds
          );

          if (
            !Number.isFinite(seconds)
          ) {
            return null;
          }

          const ratio = Math.max(
            0,
            Math.min(
              1,
              seconds / duration
            )
          );

          return (
            <span
              key={
                annotation.id ||
                `${annotation.kind}-${seconds}`
              }
              className={
                `kgen-episode-marker ` +
                (
                  annotation.kind ===
                  "detected"
                    ? "detected"
                    : "reference"
                )
              }
              style={{
                left: `${ratio * 100}%`,
              }}
              title={
                annotation.kind ===
                "detected"
                  ? "Detector activation"
                  : "Event onset reference"
              }
            />
          );
        }
      )}
    </div>
  );
}

function EpisodePhysiology({
  episode,
  waveforms,
}) {
  const duration = Number(
    waveforms.durationSeconds ||
      episode.durationSeconds ||
      1
  );

  const eventStart = Number(
    waveforms.eventStartSeconds ||
      episode.eventStartOffsetSeconds ||
      0
  );

  const eventEnd = Number(
    waveforms.eventEndSeconds ||
      episode.eventEndOffsetSeconds ||
      eventStart
  );

  const eventStartRatio = clamp(
    eventStart / duration,
    0,
    1
  );

  const eventEndRatio = clamp(
    eventEnd / duration,
    eventStartRatio,
    1
  );

  const lead2 = normalizeEpisodeValues(
    waveforms.leadsMv?.lead2
  );

  const lead1 = normalizeEpisodeValues(
    waveforms.leadsMv?.lead1
  );

  const avf = normalizeEpisodeValues(
    waveforms.leadsMv?.avf
  );

const isEvaluationEpisode =
  Boolean(
    episode
      ?.isEvaluationEpisode ||
    episode?.mode ===
      "evaluation_injection"
  );

const explicitTriggerMarkers = (
  waveforms.annotations ||
  episode.annotations ||
  []
).filter(
  (item) =>
    item?.kind === "reference" ||
    item?.kind === "detected"
);

const fallbackTriggerMarkers = [
  ...(
    Array.isArray(
      episode?.referenceAnnotations
    )
      ? episode.referenceAnnotations
      : []
  ),
  ...(
    Array.isArray(
      waveforms.triggerAnnotations
    )
      ? waveforms.triggerAnnotations
      : Array.isArray(
          episode?.triggerAnnotations
        )
      ? episode.triggerAnnotations
      : []
  ),
];

const triggerAnnotations =
  Array.from(
    new Map(
      (
        explicitTriggerMarkers.length
          ? explicitTriggerMarkers
          : fallbackTriggerMarkers
      ).map(
        (annotation, index) => [
          annotation?.id ||
            [
              annotation?.kind,
              annotation
                ?.captureOffsetSeconds,
              index,
            ].join("-"),
          annotation,
        ]
      )
    ).values()
  );

const triggerSummary =
  summarizeTriggerMarkers(
    triggerAnnotations
  );

  return (
    <div className="kgen-live-content">
      <div className="kgen-wave-stack">
        <EpisodeWaveRow
          label="Lead II"
          color="red"
          values={lead2}
          eventStartRatio={eventStartRatio}
          eventEndRatio={eventEndRatio}
          triggerAnnotations={triggerAnnotations}
          duration={duration}
        />

        <EpisodeWaveRow
          label="Lead I"
          color="red"
          values={lead1}
          compact
          eventStartRatio={eventStartRatio}
          eventEndRatio={eventEndRatio}
          triggerAnnotations={triggerAnnotations}
          duration={duration}
        />

        <EpisodeWaveRow
          label="aVF"
          color="blue"
          values={avf}
          eventStartRatio={eventStartRatio}
          eventEndRatio={eventEndRatio}
          triggerAnnotations={triggerAnnotations}
          duration={duration}
        />


        <div className="kgen-time-axis">
  <span>
    {episode.captureCompleteness?.preContextComplete
      ? `−${episode.preSecondsCaptured}s`
      : `${episode.preSecondsCaptured}s available`}
  </span>

  <span>Event start</span>
  <span>Event end</span>

  <span>
    +{episode.postSecondsCaptured}s
  </span>
</div>
      </div>

      <aside className="kgen-side-vitals">
        <div className="kgen-side-vital">
          <span>Trigger HR</span>

          <strong className="blue">
            {episode.triggerHeartRate ?? "--"}
          </strong>

          <small className="kgen-episode-vital-note">
            bpm
          </small>
        </div>

        {isEvaluationEpisode ? (
          <div className="kgen-side-vital">
            <span>Trigger latency</span>

            <strong className="blue">
              {episode.triggerLatencySeconds != null
                ? `${Number(
                    episode.triggerLatencySeconds
                  ).toFixed(2)}s`
                : "--"}
            </strong>

            <small className="kgen-episode-vital-note">
              reference to detected
            </small>
          </div>
        ) : (
          <div className="kgen-side-vital">
            <span>Event span</span>

            <strong className="blue">
              {Number(
                episode.eventDurationSeconds || 0
              ).toFixed(1)}
              s
            </strong>

            <small className="kgen-episode-vital-note">
              reference window
            </small>
          </div>
        )}

        <div className="kgen-side-vital">
          <span>
            {isEvaluationEpisode
              ? "Capture"
              : "Annotations"}
          </span>

          <strong className="blue">
            {isEvaluationEpisode
              ? (
                  episode.captureCompleteness
                    ?.captureComplete
                    ? "Complete"
                    : "Partial"
                )
              : (
                  episode
                    .triggerAnnotationCount ??
                  triggerAnnotations.length
                )}
          </strong>

          <small className="kgen-episode-vital-note">
            {isEvaluationEpisode
              ? (
                  `${triggerSummary.referenceCount}` +
                  " / " +
                  `${triggerSummary.detectedCount}` +
                  " reference / detected"
                )
              : "automatic triggers"}
          </small>
        </div>
      </aside>
    </div>
  );
}

export default function ClinicalPhysiologyPage({
  patient,
  episodeId,
  incidentId,
  onOpenLabs,
  evaluationDemo,
  onExitEvaluation,
}) {
  const [
    incidentList,
    setIncidentList,
  ] = useState([]);
const isEvaluation =
  Boolean(
    evaluationDemo?.active &&
    evaluationDemo?.episode
  );
  const [
    activeIncidentId,
    setActiveIncidentId,
  ] = useState(incidentId || null);

  const [
    activeEpisodeId,
    setActiveEpisodeId,
  ] = useState(episodeId || null);

  const [
    incidentEpisodes,
    setIncidentEpisodes,
  ] = useState([]);

  const [
    activeEpisodeIndex,
    setActiveEpisodeIndex,
  ] = useState(0);

  const [
    slmWidgetResult,
    setSlmWidgetResult,
  ] = useState(null);

  const [
    slmWidgetStatus,
    setSlmWidgetStatus,
  ] = useState("loading");
  const [live, setLive] = useState(createInitialLiveState);
  const [activeWaveformId, setActiveWaveformId] = useState(null);
  const [episode, setEpisode] = useState(null);
  const isEvaluationInjection =
    episode?.mode ===
    "evaluation_injection";
  // Every evaluation-injection episode uses the complete episode package.
  // Oracle SMART is never a clinical-context fallback for this page.
  const episodePackMode =
    isEvaluationInjection;
const [
  episodeWaveforms,
  setEpisodeWaveforms,
] = useState(null);

const [
  episodeStatus,
  setEpisodeStatus,
] = useState("loading");

const [
  episodeContext,
  setEpisodeContext,
] = useState(null);

const [
  contextStatus,
  setContextStatus,
] = useState("not_loaded");

function incidentIdentity(item) {
  return (
    item?.id ||
    item?.incidentId ||
    null
  );
}

function episodeIdentity(item) {
  return (
    item?.id ||
    item?.episodeId ||
    null
  );
}

function openIncident(index) {
  const selected =
    incidentList[index];

  const selectedIncidentId =
    incidentIdentity(selected);

  if (!selectedIncidentId) return;

  const preferredEpisodeId =
    selected?.bestContextEpisodeId ||
    selected?.primaryEpisodeId ||
    null;

  setActiveIncidentId(
    selectedIncidentId
  );

  setActiveEpisodeId(
    preferredEpisodeId
  );

  setActiveEpisodeIndex(0);
  setIncidentEpisodes([]);

  setEpisode(null);
  setEpisodeWaveforms(null);
  setEpisodeContext(null);

  setEpisodeStatus("loading");
  setContextStatus("loading");
  setSlmWidgetResult(null);
  setSlmWidgetStatus("loading");
}

function openIncidentEpisode(index) {
  const selected =
    incidentEpisodes[index];

  const selectedId =
    episodeIdentity(selected);

  if (!selectedId) return;

  setActiveEpisodeIndex(index);
  setActiveEpisodeId(selectedId);
}

const activeIncidentIndex =
  useMemo(() => {
    const index =
      incidentList.findIndex(
        (item) =>
          incidentIdentity(item) ===
          activeIncidentId
      );

    return index >= 0
      ? index
      : 0;
  }, [
    incidentList,
    activeIncidentId,
  ]);

useEffect(() => {
  if (isEvaluation) {
  return undefined;
}
  const resolvedIncidentId =
    activeIncidentId ||
    episode?.incidentId ||
    incidentId;

  if (!resolvedIncidentId) {
    setSlmWidgetResult(null);
    setSlmWidgetStatus("empty");
    return undefined;
  }

  let active = true;

  async function loadWidget() {
    try {
      setSlmWidgetStatus("loading");

      const result =
        await getSlmWidget(
          resolvedIncidentId
        );

      if (!active) return;

      setSlmWidgetResult(result);
      setSlmWidgetStatus("ready");
    } catch (error) {
      if (!active) return;

      console.error(
        "[KGEN SLM WIDGET ERROR]",
        error
      );

      setSlmWidgetResult(null);
      setSlmWidgetStatus("error");
    }
  }

  loadWidget();

  const disconnect =
    connectEpisodeEvents({
      onEvent: (event) => {
        if (
          event.incidentId ===
            resolvedIncidentId &&
          [
            "phase7.ready",
            "clinical.context.updated",
          ].includes(event.type)
        ) {
          loadWidget();
        }
      },
      onError: () => {},
    });

  return () => {
    active = false;
    disconnect?.();
  };
}, [
  activeIncidentId,
  episode?.incidentId,
  incidentId,isEvaluation
]);

async function ensureEpisodeContext(metadata) {
  const metadataEpisodePackMode =
    metadata?.mode ===
      "evaluation_injection" ||
    metadata?.clinicalContextMode ===
      "episode_pack_only" ||
    metadata?.evaluationContextMode ===
      "episode_pack_only" ||
    Boolean(getEpisodePack(metadata));

  if (metadataEpisodePackMode) {
    setContextStatus("ready");
    setEpisodeContext({
      status: "ready",
      source: "complete_episode_pack",
    });
    return;
  }

  if (!metadata?.incidentId) {
    setEpisodeContext(null);
    setContextStatus("not_loaded");
    return;
  }

  setContextStatus("loading");

  try {
    let context = await getIncidentContext(
      metadata.incidentId
    );

    if (
      !context ||
      ["not_loaded", "unavailable", "error"].includes(
        context.status
      )
    ) {
      context = await loadIncidentContext(
        metadata.incidentId
      );
    }

    setEpisodeContext(context || null);

    setContextStatus(
      context?.status || "empty"
    );
  } catch (error) {
    console.error(
      "[KGEN CONTEXT LOAD ERROR]",
      error
    );

    setEpisodeContext(null);
    setContextStatus("error");
  }
}
  useEffect(() => {
    if (isEvaluation) {
  return undefined;
}
    if (incidentId) {
      setActiveIncidentId(
        incidentId
      );
    }
  }, [incidentId, isEvaluation]);

  useEffect(() => {
    if (isEvaluation) {
  return undefined;
}
    if (episodeId) {
      setActiveEpisodeId(
        episodeId
      );
    }
  }, [episodeId,isEvaluation,]);

  useEffect(() => {
    if (isEvaluation) {
  return undefined;
}
    let active = true;

    async function loadIncidentList() {
      try {
        const items =
          await listIncidents();

        if (!active) return;

        setIncidentList(items);

        const requestedIncident =
          incidentId ||
          activeIncidentId ||
          episode?.incidentId;

        if (
          requestedIncident &&
          items.some(
            (item) =>
              incidentIdentity(item) ===
              requestedIncident
          )
        ) {
          setActiveIncidentId(
            requestedIncident
          );
          return;
        }

        const firstIncidentId =
          incidentIdentity(items[0]);

        if (
          !activeIncidentId &&
          firstIncidentId
        ) {
          setActiveIncidentId(
            firstIncidentId
          );
        }
      } catch (error) {
        console.error(
          "[KGEN INCIDENT LIST ERROR]",
          error
        );
      }
    }

    loadIncidentList();

    return () => {
      active = false;
    };
  }, [
    incidentId,
    isEvaluation,
  ]);

  useEffect(() => {
    if (isEvaluation) {
  return undefined;
}
    if (!activeIncidentId) {
      setIncidentEpisodes([]);
      return undefined;
    }

    let active = true;

    async function loadIncidentViews() {
      try {
        const views =
          await getIncidentEpisodes(
            activeIncidentId
          );

        if (!active) return;

        setIncidentEpisodes(views);

        const selectedIndex =
          views.findIndex(
            (item) =>
              episodeIdentity(item) ===
              activeEpisodeId
          );

        if (selectedIndex >= 0) {
          setActiveEpisodeIndex(
            selectedIndex
          );
          return;
        }

        const incident =
          incidentList.find(
            (item) =>
              incidentIdentity(item) ===
              activeIncidentId
          );

        const preferredEpisodeId =
          incident?.bestContextEpisodeId ||
          incident?.primaryEpisodeId ||
          episodeIdentity(views[0]);

        setActiveEpisodeIndex(0);

        if (preferredEpisodeId) {
          setActiveEpisodeId(
            preferredEpisodeId
          );
        }
      } catch (error) {
        if (!active) return;

        console.error(
          "[KGEN INCIDENT EPISODES ERROR]",
          error
        );

        setIncidentEpisodes([]);
      }
    }

    loadIncidentViews();

    return () => {
      active = false;
    };
  }, [
    activeIncidentId,
    incidentList,
    isEvaluation,
  ]);

  useEffect(() => {
    if (
      isEvaluation ||
      isEvaluationInjection
    ) {
      return undefined;
    }

    const interval = setInterval(() => {
      setLive((prev) => nextLiveState(prev));
    }, 420);

    return () => clearInterval(interval);
  }, [
    isEvaluation,
    isEvaluationInjection,
  ]);

useEffect(() => {
if (
  isEvaluation ||
  isEvaluationInjection
) {
  return undefined;
}
  if (STATIC_ANALYTICS_MODE) {
    return undefined;
  }

  const provider = "oracle";

  console.log("[KGEN FHIR STREAM CONFIG]", {
  provider,
  streamUrl: import.meta.env.VITE_FHIR_STREAM_URL,
  envPatientId: import.meta.env.VITE_FHIR_PATIENT_ID
});

  const streamPatientId = "";

  const disconnect = connectFhirStream({
    provider,
    patientId: streamPatientId,
    onFrame: (frame) => {
  console.log("[KGEN PAGE FRAME RECEIVED]", {
    source: frame.source,
    status: frame.status,
    receivedAt: frame.receivedAt,
    fhirFields: frame.dataQuality?.fhirFields,
    fallbackFields: frame.dataQuality?.fallbackFields,
    observationCount: frame.dataQuality?.observationCount,
    matchedObservationCount: frame.dataQuality?.matchedObservationCount,
    vitals: frame.vitals,
    labs: frame.labs
  });

  setLive((prev) => mergeFirelyFrameIntoLive(prev, frame));
},
    onHeartbeat: () => {
      setLive((prev) => ({
        ...prev,
        firelyStatus:
          prev.firelyStatus === "local" ? "connecting" : prev.firelyStatus
      }));
    },
    onError: () => {
      setLive((prev) => ({
        ...prev,
        firelyStatus: "error",
        alertColor: "yellow",
        alertInterpretation: {
          title: "FHIR stream warning",
          rhythm: "The dashboard could not receive the latest FHIR stream frame.",
          ppg: "Local waveform simulation is still running.",
          likelyEtiology: "Check whether FastAPI is running on http://127.0.0.1:8000."
        }
      }));
    }
  });

  return disconnect;
}, [
  patient?.fhirId,
  patient?.id,
  isEvaluation,
  isEvaluationInjection,
]);

const evaluationWidgetResult =
  useMemo(() => {
    if (!isEvaluation) {
      return null;
    }

    return adaptEvaluationRunToWidget({
      episode:
        evaluationDemo.episode,

      run:
        evaluationDemo.run,
    });
  }, [
    isEvaluation,
    evaluationDemo?.episode,
    evaluationDemo?.run,
  ]);
  const displayedSlmWidgetResult =
    useMemo(() => {
      if (isEvaluation) {
        return evaluationWidgetResult;
      }

      if (
        episode?.mode !==
          "evaluation_injection" ||
        !slmWidgetResult
      ) {
        return slmWidgetResult;
      }

      const score =
        episode
          ?.evaluationScore;

      if (!score) {
        return slmWidgetResult;
      }

      const interpretation = {
        ...(
          slmWidgetResult
            .widgetInterpretation ||
          {}
        ),
      };

      const existing =
        Array.isArray(
          interpretation
            .importantLimitations
        )
          ? interpretation
              .importantLimitations
          : [];

      const scoreNotes = [];

      if (
        score.total != null
      ) {
        scoreNotes.push(
          `Evaluator score: ${score.total}/100`
        );
      }

      if (
        score.safetyPass != null
      ) {
        scoreNotes.push(
          `Safety gate: ${
            score.safetyPass
              ? "PASS"
              : "FAIL"
          }`
        );
      }

      interpretation
        .importantLimitations = [
          ...existing,
          ...scoreNotes,
        ];

      const existingMetrics =
        Array.isArray(
          interpretation.keyMetrics
        )
          ? interpretation.keyMetrics
          : [];

      interpretation.keyMetrics = [
        ...existingMetrics.filter(
          (metric) =>
            metric?.key !==
              "evaluation-score" &&
            metric?.key !==
              "evaluation-safety"
        ),
        ...(score.total != null
          ? [
              {
                key:
                  "evaluation-score",
                label:
                  "Evaluator score",
                value: score.total,
                unit: "/100",
              },
            ]
          : []),
        ...(score.safetyPass != null
          ? [
              {
                key:
                  "evaluation-safety",
                label:
                  "Safety gate",
                value:
                  score.safetyPass
                    ? "PASS"
                    : "FAIL",
                unit: "",
              },
            ]
          : []),
      ];

      return {
        ...slmWidgetResult,
        widgetInterpretation:
          interpretation,
      };
    }, [
      isEvaluation,
      evaluationWidgetResult,
      slmWidgetResult,
      episode?.mode,
      episode?.evaluationScore,
    ]);

  const evaluationTriggerMarkers =
  useMemo(() => {
    if (!isEvaluation) {
      return [];
    }

    return buildEvaluationTriggerMarkers({
      episode:
        evaluationDemo
          ?.episode,

      capture:
        evaluationDemo
          ?.capture,
    });
  }, [
    isEvaluation,
    evaluationDemo?.episode,
    evaluationDemo?.capture,
  ]);
const evaluationEpisodeView =
  useMemo(() => {
    const source =
      evaluationDemo?.episode;

    if (
      !isEvaluation ||
      !source
    ) {
      return null;
    }

    const duration =
      Number(
        source.ecg
          ?.durationSeconds ||
        source.episode
          ?.durationSeconds ||
        8
      );

    return {
      id:
        source.episodeId ||
        evaluationDemo
          ?.episodeId,

      display:
        source.episode
          ?.display ||
        source.episode
          ?.title ||
        source.episode
          ?.type ||
        evaluationDemo
          ?.episodeId,

      severity:
        source.episode
          ?.severity ||
        source.severity ||
        "warning",

      durationSeconds:
        duration,

      eventDurationSeconds:
        duration,

      eventStartOffsetSeconds:
        0,

      eventEndOffsetSeconds:
        duration,

      triggerHeartRate:
        source.ecg
          ?.measurements
          ?.heartRateBpm ??
        source.ecg
          ?.measurements
          ?.heartRate ??
        source.vitals
          ?.heartRate ??
        null,

      isEvaluationEpisode:
  true,

triggerAnnotationCount:
  evaluationTriggerMarkers
    .filter(
      (marker) =>
        marker.kind ===
        "detected"
    )
    .length,

triggerAnnotations:
  evaluationTriggerMarkers,

triggerMarkerSummary:
  summarizeTriggerMarkers(
    evaluationTriggerMarkers
  ),

      preSecondsCaptured:
        0,

      postSecondsCaptured:
        0,

      captureCompleteness: {
        preContextComplete:
          true,
        postContextComplete:
          true,
        captureComplete:
          true,
      },
    };
  },  [
  isEvaluation,
  evaluationDemo?.episode,
  evaluationDemo?.episodeId,
  evaluationTriggerMarkers,
]);


const evaluationWaveformsView =
  useMemo(() => {
    const source =
      evaluationDemo?.episode;

    if (
      !isEvaluation ||
      !source
    ) {
      return null;
    }

    const duration =
      Number(
        source.ecg
          ?.durationSeconds ||
        8
      );

    return {
      durationSeconds:
        duration,

      eventStartSeconds:
        0,

      eventEndSeconds:
        duration,

      sampleRate:
        source.ecg
          ?.sampleRate ||
        250,

      leadsMv:
        source.ecg
          ?.waveforms ||
        {},

      triggerAnnotations:
  evaluationTriggerMarkers,
    };
 }, [
  isEvaluation,
  evaluationDemo?.episode,
  evaluationTriggerMarkers,
]);

useEffect(() => {
  if (!isEvaluation) {
    return;
  }

  const scenario = evaluationDemo?.episode;

  if (!scenario) {
    return;
  }

  const vitals = scenario.vitals || {};
  const labs = scenario.labs || {};
  const bloodPressure =
    vitals.bloodPressure ||
    vitals.bp ||
    {};

  const heartRate = readEvaluationValue(
    vitals.heartRate
  );
  const respiratoryRate =
    readEvaluationValue(
      vitals.respiratoryRate
    );
  const spo2 = readEvaluationValue(
    vitals.spo2
  );
  const systolic = readEvaluationValue(
    vitals.systolic ??
      bloodPressure.systolic
  );
  const diastolic = readEvaluationValue(
    vitals.diastolic ??
      bloodPressure.diastolic
  );
  const temperature = readEvaluationValue(
    vitals.temperature
  );
  const glucose = readEvaluationValue(
    labs.glucose ?? labs.Glucose
  );
  const potassium = readEvaluationValue(
    labs.potassium ??
      labs.Potassium ??
      labs.K
  );
  const creatinine = readEvaluationValue(
    labs.creatinine ?? labs.Creatinine
  );
  const wbc = readEvaluationValue(
    labs.wbc ??
      labs.WBC ??
      labs.whiteBloodCellCount
  );

  const primaryMedicationRows =
    normalizeEvaluationMedications(
      scenario.medications
    );

  const contextMedicationRows =
    normalizeEvaluationMedications(
      scenario.clinicalContext
        ?.medications
    );

  setLive((previous) => ({
    ...previous,
    firelyStatus: "evaluation",
    firelySource:
      "cardinal-evaluation",
    streamTimestamp:
      scenario.capturedAt ||
      previous.streamTimestamp,
    heartRate,
    respiratoryRate,
    spo2,
    systolic,
    diastolic,
    temperature,
    glucose,
    potassium,
    creatinine,
    wbc,
    heartTrend:
      heartRate == null
        ? []
        : [heartRate, heartRate],
    respTrend:
      respiratoryRate == null
        ? []
        : [
            respiratoryRate,
            respiratoryRate,
          ],
    spo2Trend:
      spo2 == null
        ? []
        : [spo2, spo2],
    glucoseTrend:
      glucose == null
        ? []
        : [glucose, glucose],
    potassiumTrend:
      potassium == null
        ? []
        : [potassium, potassium],
    creatinineTrend:
      creatinine == null
        ? []
        : [creatinine, creatinine],
    wbcTrend:
      wbc == null
        ? []
        : [wbc, wbc],
    medicationRows:
      primaryMedicationRows.length
        ? primaryMedicationRows
        : contextMedicationRows,
  }));
}, [
  isEvaluation,
  evaluationDemo?.episode,
]);

useEffect(() => {
  if (
    !isEvaluationInjection ||
    episode?.oracleDemo ||
    episodePackMode
  ) {
    return;
  }

  const scenario =
    episode?.evaluationScenario || {};
  const vitals = scenario.vitals || {};
  const labs = scenario.labs || {};
  const bloodPressure =
    vitals.bloodPressure ||
    vitals.bp ||
    {};

  const heartRate = readEvaluationValue(
    vitals.heartRate ??
      vitals.heartRateBpm ??
      episode?.triggerHeartRate
  );
  const respiratoryRate =
    readEvaluationValue(
      vitals.respiratoryRate ??
        vitals.respiratoryRateBpm
    );
  const spo2 = readEvaluationValue(
    vitals.spo2 ??
      vitals.spo2Pct
  );
  const systolic = readEvaluationValue(
    vitals.systolic ??
      bloodPressure.systolic
  );
  const diastolic = readEvaluationValue(
    vitals.diastolic ??
      bloodPressure.diastolic
  );
  const temperature = readEvaluationValue(
    vitals.temperature ??
      vitals.temperatureC
  );
  const glucose = readEvaluationValue(
    labs.glucose ?? labs.Glucose
  );
  const potassium = readEvaluationValue(
    labs.potassium ??
      labs.Potassium ??
      labs.K
  );
  const creatinine = readEvaluationValue(
    labs.creatinine ?? labs.Creatinine
  );
  const wbc = readEvaluationValue(
    labs.wbc ??
      labs.WBC ??
      labs.whiteBloodCellCount
  );

  const medicationRows =
    normalizeEvaluationMedications(
      scenario.medications ||
        scenario.patient
          ?.homeMedications ||
        []
    );

  setLive((previous) => ({
    ...previous,
    firelyStatus: "evaluation",
    firelySource:
      "incart-evaluation-injection",
    streamTimestamp:
      episode?.capturedAt ||
      previous.streamTimestamp,
    heartRate,
    respiratoryRate,
    spo2,
    systolic,
    diastolic,
    temperature,
    glucose,
    potassium,
    creatinine,
    wbc,
    heartTrend:
      heartRate == null
        ? []
        : [heartRate, heartRate],
    respTrend:
      respiratoryRate == null
        ? []
        : [
            respiratoryRate,
            respiratoryRate,
          ],
    spo2Trend:
      spo2 == null
        ? []
        : [spo2, spo2],
    glucoseTrend:
      glucose == null
        ? []
        : [glucose, glucose],
    potassiumTrend:
      potassium == null
        ? []
        : [potassium, potassium],
    creatinineTrend:
      creatinine == null
        ? []
        : [creatinine, creatinine],
    wbcTrend:
      wbc == null
        ? []
        : [wbc, wbc],
    medicationRows,
  }));
}, [
  isEvaluationInjection,
  episode?.oracleDemo,
  episodePackMode,
  episode?.evaluationScenario,
  episode?.capturedAt,
  episode?.triggerHeartRate,
]);

useEffect(() => {
  if (!episodePackMode) {
    return;
  }

  const patch =
    buildEpisodePackLivePatch(
      episode
    );

  setLive((previous) => ({
    ...previous,
    ...patch,
  }));
}, [
  episodePackMode,
  episode,
]);

useEffect(() => {
  if (
    episodePackMode ||
    !isEvaluationInjection ||
    !episode?.oracleDemo ||
    contextStatus !== "ready"
  ) {
    return;
  }

  const patch =
    buildOracleLivePatch(
      episodeContext
    );

  setLive((previous) => ({
    ...previous,
    ...Object.fromEntries(
      Object.entries(patch).filter(
        ([, value]) =>
          value !== null &&
          value !== undefined
      )
    ),
  }));
}, [
  episodePackMode,
  isEvaluationInjection,
  episode?.oracleDemo,
  episodeContext,
  contextStatus,
]);

useEffect(() => {
  if (isEvaluation) {
  return undefined;
}

  if (
    activeIncidentId &&
    !activeEpisodeId
  ) {
    setEpisodeStatus("loading");
    return undefined;
  }

  let active = true;

 async function loadEpisode(
  targetEpisodeId = activeEpisodeId
 ){
    try {
      setEpisodeStatus("loading");

      const metadata = targetEpisodeId
        ? await getEpisode(targetEpisodeId)
        : await getLatestEpisode();

      if (!active) return;

      if (!metadata?.id) {
  setEpisode(null);
  setEpisodeWaveforms(null);
  setEpisodeContext(null);
  setContextStatus("not_loaded");
  setEpisodeStatus("empty");
  return;
}

      const waveforms =
        await getEpisodeWaveforms(
          metadata.id
        );

      if (!active) return;

      setEpisode(metadata);
setEpisodeWaveforms(waveforms);
setEpisodeStatus("ready");
const metadataIncidentId =
  metadata.incidentId || null;

if (
  metadataIncidentId &&
  metadataIncidentId !==
    activeIncidentId
) {
  setActiveIncidentId(
    metadataIncidentId
  );
}

void ensureEpisodeContext(metadata);
    } catch (error) {
      if (!active) return;

      console.error(
        "[KGEN EPISODE LOAD ERROR]",
        error
      );

      setEpisode(null);
      setEpisodeWaveforms(null);
      setEpisodeStatus("error");
    }
  }

  loadEpisode();

  const disconnect =
    connectEpisodeEvents({
      onEvent: (event) => {
        if (
          event.type === "episode.captured" &&
          (
            !activeEpisodeId ||
            event.episodeId === activeEpisodeId
          )
        ) {
          loadEpisode(event.episodeId);
        }
      },
      onError: () => {},
    });

  return () => {
    active = false;
    disconnect?.();
  };
}, [
  activeEpisodeId,
  activeIncidentId,
  isEvaluation
]);

const currentPatient =
  useMemo(() => {
    if (
      isEvaluation &&
      evaluationDemo
        ?.episode
        ?.patient
    ) {
      const evaluationPatient =
        evaluationDemo
          .episode
          .patient;

      return {
        name:
          evaluationPatient.name ||
          "Synthetic Patient",

        sex:
          evaluationPatient.sex
            ?.toUpperCase?.() ||
          "--",

        dob:
          evaluationPatient.dob ||
          "--",

        id:
          evaluationPatient.mrn ||
          evaluationPatient.id ||
          evaluationDemo.episodeId,
      };
    }

    if (
      isEvaluationInjection &&
      episode?.patient
    ) {
      const evaluationPatient =
        episode.patient;

      return {
        name:
          evaluationPatient.name ||
          "Evaluation Patient",

        sex:
          evaluationPatient.sex
            ?.toUpperCase?.() ||
          "--",

        dob:
          evaluationPatient.dob ||
          "--",

        id:
          evaluationPatient.mrn ||
          evaluationPatient.id ||
          episode.id,
      };
    }

    if (!patient) {
      return BASE_PATIENT;
    }

    return {
      name:
        patient.name ||
        BASE_PATIENT.name,

      sex:
        patient.sex
          ?.toUpperCase?.() ||
        BASE_PATIENT.sex,

      dob:
        patient.dob ||
        BASE_PATIENT.dob,

      id:
        patient.mrn ||
        patient.id ||
        BASE_PATIENT.id,
    };
  }, [
    patient,
    isEvaluation,
    evaluationDemo?.episode,
    evaluationDemo?.episodeId,
    isEvaluationInjection,
    episode?.patient,
    episode?.id,
  ]);

const episodePackPatient =
  useMemo(
    () =>
      buildEpisodePackPatient(
        episode
      ),
    [episode]
  );

const displayedPatient =
  episodePackMode &&
  episodePackPatient
    ? episodePackPatient
    : currentPatient;

const streamDate = formatStreamDate(live.streamTimestamp);

const labCards = useMemo(() => {
  return [
    {
      name: "Glucose",
      value: formatClinicalNumber(
        live.glucose,
        0
      ),
      status: statusFromColor(getLiveColor(live, "glucose", "blue")),
      meta: streamDate,
      trend: live.glucoseTrend,
      color: getLiveColor(live, "glucose", "blue"),
    },
    {
      name: "Potassium",
      value: formatClinicalNumber(
        live.potassium,
        1
      ),
      status: statusFromColor(getLiveColor(live, "potassium", "blue")),
      meta: streamDate,
      trend: live.potassiumTrend,
      color: getLiveColor(live, "potassium", "blue"),
    },
    {
      name: "Creatinine",
      value: formatClinicalNumber(
        live.creatinine,
        2
      ),
      status: statusFromColor(getLiveColor(live, "creatinine", "blue")),
      meta: streamDate,
      trend: live.creatinineTrend,
      color: getLiveColor(live, "creatinine", "blue"),
    },
    {
      name: "WBC",
      value: formatClinicalNumber(
        live.wbc,
        1
      ),
      status: statusFromColor(getLiveColor(live, "wbc", "blue")),
      meta: streamDate,
      trend: live.wbcTrend,
      color: getLiveColor(live, "wbc", "blue"),
    },
  ];
}, [
  live.glucose,
  live.potassium,
  live.creatinine,
  live.wbc,
  live.glucoseTrend,
  live.potassiumTrend,
  live.creatinineTrend,
  live.wbcTrend,
  live.colors,
  streamDate,
]);

const oracleLabCards =
  useMemo(
    () =>
      buildOracleLabCards(
        episodeContext
      ),
    [episodeContext]
  );

const episodePackLabCards =
  useMemo(
    () =>
      buildEpisodePackLabCards(
        episode
      ),
    [episode]
  );

const existingDisplayedLabCards =
  contextStatus === "ready"
    ? oracleLabCards
    : isEvaluationInjection
    ? []
    : labCards;

const displayedLabCards =
  episodePackMode
    ? episodePackLabCards
    : existingDisplayedLabCards;



// const labCards = useMemo(() => {
//   return STATIC_ANALYTICS_LABS;
// }, []);

const oracleVitalRows =
  useMemo(
    () =>
      buildOracleVitalRows(
        episodeContext
      ),
    [episodeContext]
  );

const episodePackVitalRows =
  useMemo(
    () =>
      buildEpisodePackVitalRows(
        episode
      ),
    [episode]
  );

const existingVitalRows =
  isEvaluationInjection &&
  episode?.oracleDemo
    ? oracleVitalRows
    : [
        [
          "BP",
          formatBloodPressure(
            live.systolic,
            live.diastolic
          ),
          "mmHg",
          streamDate,
        ],
        [
          "SpO2",
          formatClinicalNumber(
            live.spo2,
            0
          ),
          "%",
          streamDate,
        ],
        [
          "Oral Temperature",
          formatClinicalNumber(
            live.temperature,
            1
          ),
          "°C",
          streamDate,
        ],
      ];

const vitalRows =
  episodePackMode
    ? episodePackVitalRows
    : existingVitalRows;

const waveformOverlay = useMemo(() => {
    if (!activeWaveformId) return null;

    const liveWaveforms = {
      ecg: {
        section: "01. Live Physiology",
        title: "ECG waveform",
        subtitle: `${displayedPatient.name} • Hyperkalemic rhythm progression`,
        scaleMode: "normalized",
        values: live.ecg,
        currentValue: live.heartRate,
        unit: " bpm",
        status: "Critical",
        footerLeft: "0s",
        footerRight: "16s",
         color: getLiveColor(live, "heartRate", live.alertColor || "red")
      },
      resp: {
        section: "01. Live Physiology",
        title: "Respiratory rhythm waveform",
        subtitle: `${displayedPatient.name} • Respiratory waveform strip`,
       color: getLiveColor(live, "respiratoryRate", "yellow"),
        scaleMode: "normalized",
        values: live.resp,
        currentValue: live.respiratoryRate,
        unit: " rpm",
        status: "Warning",
        footerLeft: "0s",
        footerRight: "16s"
      },
      ppg: {
        section: "01. Live Physiology",
        title: "PPG waveform",
        subtitle: `${displayedPatient.name} • Pulse plethysmography signal`,
        color: getLiveColor(live, "spo2", "blue"),
        scaleMode: "normalized",
        values: live.ppg,
        currentValue: live.spo2,
        unit: "%",
        status: "Monitored",
        footerLeft: "0s",
        footerRight: "16s"
      },
      ppgSoft: {
        section: "01. Live Physiology",
        title: "Secondary PPG waveform",
        subtitle: `${displayedPatient.name} • Low amplitude pulse trend`,
        color: getLiveColor(live, "spo2", "blue"),
        scaleMode: "normalized",
        values: live.ppgSoft,
        currentValue: live.spo2,
        unit: "%",
        status: "Monitored",
        footerLeft: "0s",
        footerRight: "16s"
      },
      heartTrend: {
        section: "01. Live Physiology",
        title: "Heart rate trend",
        subtitle: `${displayedPatient.name} • Live heart rate mini trend`,
        color: getLiveColor(live, "heartRate", "red"),
        scaleMode: "scaled",
        values: live.heartTrend,
        currentValue: live.heartRate,
        unit: " bpm",
        status: "Critical",
        decimals: 0,
        footerLeft: "Earlier",
        footerRight: "Now"
      },
      respTrend: {
        section: "01. Live Physiology",
        title: "Respiratory rate trend",
        subtitle: `${displayedPatient.name} • Live respiratory trend`,
          color: getLiveColor(live, "respiratoryRate", "yellow"),
        scaleMode: "scaled",
        values: live.respTrend,
        currentValue: live.respiratoryRate,
        unit: " rpm",
        status: "Warning",
        decimals: 0,
        footerLeft: "Earlier",
        footerRight: "Now"
      },
      spo2Trend: {
        section: "01. Live Physiology",
        title: "SpO2 trend",
        subtitle: `${displayedPatient.name} • Oxygen saturation trend`,
         color: getLiveColor(live, "spo2", "blue"),
        scaleMode: "scaled",
        values: live.spo2Trend,
        currentValue: live.spo2,
        unit: "%",
        status: "Stable",
        decimals: 0,
        footerLeft: "Earlier",
        footerRight: "Now"
      }
    };

    if (liveWaveforms[activeWaveformId]) {
      return liveWaveforms[activeWaveformId];
    }

    const labName = activeWaveformId.replace("lab-", "");
    const selectedLab =
  displayedLabCards.find((item) => item.name === labName);
    
  


    if (!selectedLab) return null;

    return {
      section: "03. Recent Lab Results & Trends",
      title: `${selectedLab.name} trend`,
      subtitle: `${displayedPatient.name} • Lab trend over recent draws`,
      color: selectedLab.color || "red",
      scaleMode: "scaled",
      values: selectedLab.trend,
      currentValue: selectedLab.value,
      unit: "",
      status: selectedLab.status,
      decimals:
        selectedLab.name === "Creatinine"
          ? 2
          : selectedLab.name === "Potassium"
          ? 1
          : selectedLab.name === "WBC"
          ? 1
          : 0,
      footerLeft: "06/23",
      footerRight: "07/18"
    };
  }, [activeWaveformId, displayedPatient.name, live, displayedLabCards]);

const interpretation =
  live.alertInterpretation || DEFAULT_ALERT_INTERPRETATION;

const alertColor = normalizeColor(live.alertColor, "red");

const episodeReady = Boolean(
  episode &&
  episodeWaveforms &&
  episodeStatus === "ready"
);
const existingMedicationRows =
  useMemo(() => {
    if (
      isEvaluationInjection &&
      episode?.oracleDemo
    ) {
      return (
        selectOracleMedicationRows(
          episodeContext,
          8
        )
      );
    }

    return (
      live.medicationRows || []
    ).filter(
      (row) =>
        row.med !== "Oral Temperature" &&
        row.name !== "Oral Temperature"
    );
  }, [
    isEvaluationInjection,
    episode?.oracleDemo,
    episodeContext,
    live.medicationRows,
  ]);

const displayedMedicationRows =
  useMemo(
    () =>
      episodePackMode
        ? buildEpisodePackMedicationRows(
            episode,
            10
          )
        : existingMedicationRows,
    [
      episodePackMode,
      episode,
      existingMedicationRows,
    ]
  );

const episodeInterpretation = episodeReady
  ? isEvaluationInjection
    ? {
        title: `${episode.display} captured`,
        rhythm:
          `Reference onset ${Number(
            episode.referenceOnsetOffsetSeconds || 0
          ).toFixed(2)}s • detected trigger ${Number(
            episode.detectedTriggerOffsetSeconds || 0
          ).toFixed(2)}s • latency ${Number(
            episode.triggerLatencySeconds || 0
          ).toFixed(2)}s.`,
        ppg:
          "The captured physiology window contains the mixed ECG stream and the scenario-linked vital context.",
        likelyEtiology:
          episode.captureCompleteness
            ?.captureComplete
            ? "The requested 6-second pre-event, 8-second event, and 6-second post-event window was captured completely."
            : "The captured window is incomplete; review the capture-completeness details.",
      }
    : {
        title: `${episode.display} captured`,
        rhythm:
          `${episode.triggerAnnotationCount || 0} INCART reference annotation trigger(s) automatically created this episode. ` +
          `Trigger types: ${
            Object.entries(
              episode.triggerAnnotationCounts || {}
            )
              .map(
                ([symbol, count]) =>
                  `${symbol}: ${count}`
              )
              .join(", ") || "Unavailable"
          }.`,
        ppg:
          "PPG and SpO2 are not included in the INCART recording.",
        likelyEtiology:
          "This is an automatically selected reference-annotation episode. Deterministic ECG analysis and clinical context are available in the interpretation panel.",
      }
  : {
      title:
        episodeStatus === "loading"
          ? "Loading captured episode"
          : "No automatic episode captured",
      rhythm:
        episodeStatus === "loading"
          ? "Retrieving stored waveform and annotation data."
          : "Keep INCART running until a qualifying reference annotation is received.",
      ppg:
        "INCART provides ECG and reference annotations.",
      likelyEtiology:
        "No episode is currently available for analysis.",
    };

const episodeAlertColor =
  isEvaluation
    ? evaluationDemo?.run
      ? "yellow"
      : "blue"
    : isEvaluationInjection &&
      episode?.severity === "critical"
    ? "red"
    : episodeReady
    ? "yellow"
    : "blue";
  return (
    <section className="kgen-page">
      <header className="kgen-topbar">
        {(isEvaluation ||
          isEvaluationInjection) && (
          <button
            type="button"
            className="kgen-blue-btn"
            onClick={
              onExitEvaluation
            }
          >
            {isEvaluation
              ? "Exit Evaluation"
              : "Return to INCART"}
          </button>
        )}
        <div className="kgen-brand-box">
          <div className="kgen-logo">⌁</div>
          <span>KardioGenics</span>
        </div>

        <div className="kgen-patient-box">
          <strong>{displayedPatient.name}</strong>
          <span>
            {displayedPatient.sex} | DOB: {displayedPatient.dob} | ID: {displayedPatient.id}
          </span>
        </div>

        <div className="kgen-title-box">
  <span>CLINICAL DASHBOARD (REAL-TIME PHYSIOLOGY MONITOR)</span>
</div>
      </header>

      <main className="kgen-grid">
       <section className="kgen-panel kgen-live-panel">
  <div className="kgen-panel-title-row">
    <h2>01. Episode Physiology</h2>

    <span
      className={`kgen-episode-status ${
        (isEvaluation ||
          isEvaluationInjection)
          ? "ready"
          : episodeStatus
      }`}
    >
      <span className="kgen-clock-dot" />

      {isEvaluation
        ? "Evaluation episode"
        : isEvaluationInjection
        ? "Evaluation capture"
        : episodeStatus === "ready"
        ? "Captured episode"
        : episodeStatus === "loading"
        ? "Loading episode"
        : episodeStatus === "error"
        ? "Episode unavailable"
        : "Waiting for capture"}
    </span>
  </div>
  

{isEvaluation &&
evaluationEpisodeView &&
evaluationWaveformsView ? (
  <EpisodePhysiology
    episode={
      evaluationEpisodeView
    }
    waveforms={
      evaluationWaveformsView
    }
  />
) : episodeReady ? (
  <IncidentEpisodeCarousel
    incidents={incidentList}
    activeIncidentIndex={
      activeIncidentIndex
    }
    onIncidentChange={
      openIncident
    }
    episodes={
      incidentEpisodes
    }
    activeEpisodeIndex={
      activeEpisodeIndex
    }
    onEpisodeChange={
      openIncidentEpisode
    }
  >
    <EpisodePhysiology
      episode={episode}
      waveforms={
        episodeWaveforms
      }
    />
  </IncidentEpisodeCarousel>
) : (
  <div className="kgen-episode-empty">
      <strong>
        No captured episode selected.
      </strong>

      <p>
        Monitoring continues on the Main page.
        Keep INCART selected until the configured
        post-event interval is complete.
      </p>
    </div>
  )}
</section>

        <section className="kgen-panel kgen-labs-panel">
  <div className="kgen-panel-title-row">
    <h2>
      03. Recent Lab Results &amp; Trends
    </h2>

    <span
      className={`kgen-context-status ${
        (isEvaluation ||
          isEvaluationInjection)
          ? "ready"
          : contextStatus
      }`}
    >
      {isEvaluation
        ? "Evaluation scenario"
        : episodePackMode
        ? "Episode pack"
        : isEvaluationInjection
        ? "Oracle FHIR"
        : contextStatus === "ready"
        ? "Episode-linked FHIR"
        : contextStatus === "loading"
        ? "Loading FHIR context"
        : contextStatus === "error"
        ? "FHIR context error"
        : "Not episode-linked"}
    </span>
  </div>

          <div className="kgen-lab-grid">
           {displayedLabCards.map((item) => (
  <LabTile
    key={item.name}
    {...item}
    onOpenTrend={() => setActiveWaveformId(`lab-${item.name}`)}
  />
))}
          </div>

          {/* <div className="kgen-mini-table">
            <span>06/23</span>
            <span>06/28</span>
            <span>07/07</span>
            <span>07/18</span>

            <b>125</b>
            <b>139</b>
            <b>141</b>
            <b>{live.glucose}</b>

            <b>{live.creatinine.toFixed(2)}</b>
            <b>14</b>
            <b>0.89</b>
            <b>{live.potassium.toFixed(1)}</b>
          </div> */}

<div className="kgen-mini-table">
  <span>06/23</span>
  <span>06/28</span>
  <span>07/07</span>
  <span>07/18</span>

  <b>--</b>
  <b>--</b>
  <b>--</b>
  <b>
    {formatClinicalNumber(
      live.glucose,
      0
    )}
  </b>

  <b>
    {formatClinicalNumber(
      live.creatinine,
      2
    )}
  </b>
  <b>--</b>
  <b>--</b>
  <b>
    {formatClinicalNumber(
      live.potassium,
      1
    )}
  </b>
</div>
          <button className="kgen-blue-btn" type="button" onClick={onOpenLabs}>
            Access full table
          </button>
        </section>

    <section
  className={`kgen-panel kgen-alert-panel ${episodeAlertColor}`}
>
  <h2>
    02. Critical Alerts &amp; Interpretation
  </h2>

  <div className="kgen-alert-box">
 <CriticalInterpretationWidget
  result={
    displayedSlmWidgetResult
  }
  status={
    isEvaluation
      ? (
          evaluationDemo.run
            ? "ready"
            : "loading"
        )
      : slmWidgetStatus
  }
  fallback={
    isEvaluation
      ? {
          title:
            "evaluation episode",
          rhythm:
            "Run or load the evaluation SLM result.",
        }
      : live.alertInterpretation
  }
/>
</div>
</section>

        <section className="kgen-panel kgen-labs-small-panel">
          <h2>03. Recent Lab Results &amp; Trends</h2>

          <div className="kgen-lab-grid small">
          {displayedLabCards.slice(0, 2).map((item) => (
  <LabTile
    key={item.name}
    {...item}
    onOpenTrend={() => setActiveWaveformId(`lab-${item.name}`)}
  />
))}
          </div>

          <button className="kgen-blue-btn" type="button" onClick={onOpenLabs}>
            Access full table
          </button>
        </section>

        <section className="kgen-panel kgen-vitals-panel">
          <h2>04. Vital Signs Log</h2>

          <div className="kgen-table-scroll kgen-vitals-table-scroll">
            <table className="kgen-table vitals">
              <thead>
                <tr>
                  <th>Parameter</th>
                  <th>Value</th>
                  <th>Unit</th>
                  <th>Timing / Context</th>
                </tr>
              </thead>

              <tbody>
                {vitalRows.length ? (
                  vitalRows.map((row, index) => {
                    const isArrayRow =
                      Array.isArray(row);
                    const label = isArrayRow
                      ? row[0]
                      : row.label ||
                        row.parameter ||
                        row.name ||
                        "Vital sign";
                    const value = isArrayRow
                      ? row[1]
                      : row.value ?? "--";
                    const unit = isArrayRow
                      ? row[2]
                      : row.unit || "";
                    const timing = isArrayRow
                      ? row[3]
                      : row.relationLabel ||
                        row.timing ||
                        row.date ||
                        "Episode time";
                    const detail = isArrayRow
                      ? ""
                      : row.detail || "";

                    return (
                      <tr
                        key={
                          row.id ||
                          row.field ||
                          `${label}-${index}`
                        }
                      >
                        <td title={String(label)}>
                          <span>{label}</span>
                        </td>
                        <td title={String(value)}>
                          <span>{value}</span>
                          {detail && (
                            <small title={detail}>
                              {detail}
                            </small>
                          )}
                        </td>
                        <td title={String(unit)}>
                          <span>{unit || "—"}</span>
                        </td>
                        <td title={String(timing)}>
                          <span>{timing}</span>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td
                      colSpan="4"
                      className="kgen-table-empty-cell"
                    >
                      No episode-pack vital signs were supplied.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="kgen-panel kgen-med-panel">
          <h2>05. Medication Timeline</h2>

          <div className="kgen-table-scroll kgen-medication-table-scroll">
            <table className="kgen-table meds">
              <thead>
                <tr>
                  <th>Medication</th>
                  <th>Dose / Route</th>
                  <th>Status</th>
                  <th>Context Timing</th>
                </tr>
              </thead>

              <tbody>
                {displayedMedicationRows.length ? (
                  displayedMedicationRows.map(
                    (row, index) => {
                      const medicationName =
                        row.name ||
                        row.med ||
                        "Medication";
                      const dose =
                        row.doseDisplay ||
                        row.dose ||
                        "Dose not specified";
                      const route =
                        row.route &&
                        !["--", "Unavailable"].includes(
                          row.route
                        )
                          ? row.route
                          : "";
                      const status =
                        row.status ||
                        "Available";
                      const timing =
                        row.contextTiming ||
                        row.relationLabel ||
                        row.date ||
                        row.prescribed ||
                        "Episode context";

                      return (
                        <tr
                          key={
                            row.id ||
                            `${medicationName}-${index}`
                          }
                        >
                          <td className="kgen-med-name-cell">
                            <strong title={medicationName}>
                              {medicationName}
                            </strong>
                            <small>Episode pack</small>
                          </td>

                          <td>
                            <span title={String(dose)}>
                              {dose}
                            </span>
                            {route && (
                              <small title={route}>
                                {route}
                              </small>
                            )}
                          </td>

                          <td>
                            <span title={String(status)}>
                              {status}
                            </span>
                          </td>

                          <td>
                            <span title={String(timing)}>
                              {timing}
                            </span>
                          </td>
                        </tr>
                      );
                    }
                  )
                ) : (
                  <tr>
                    <td
                      colSpan="4"
                      className="kgen-table-empty-cell"
                    >
                      No episode-linked medications were supplied.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      <footer className="kgen-footer">
        <span>
          Supervisory Governance | Safety Checks | Compliance | Outcomes: Personalized Care,
          Real-time Decisions, Specialist Level Support
        </span>

        <strong>KardioGenics</strong>
      </footer>
      {waveformOverlay && (
  <WaveformOverlay
    config={waveformOverlay}
    onClose={() => setActiveWaveformId(null)}
  />
)}
    </section>
  );
}