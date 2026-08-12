import { useEffect, useMemo, useRef, useState } from "react";
import WebGLWaveformCanvas from "./WebGLWaveformCanvas";
import {
  connectWaveformStream,
  getWaveformSessionId,
} from "../services/waveformStream";
import "./SevenLeadWaveformPage.css";
import {
  connectEpisodeEvents,
  getLatestEpisode,
} from "../services/episodeService";
import {
  getEvaluationEpisode,
  getEvaluationHealth,
  getLatestCompletedEvaluationRun,
  listEvaluationEpisodes,
  runEvaluationSlm,
} from "../evaluation/evaluationApi";
import {
  adaptEvaluationEpisode,
} from "../evaluation/evaluationAdapter";
import {
  createEvaluationPlayback,
} from "../evaluation/evaluationPlayback";
import {
  armEvaluationInjection,
  cancelEvaluationInjection,
  listEvaluationInjectionScenarios,
} from "../evaluation/evaluationInjectionApi";
import {
  startOracleEvaluationDemo,
  getOracleEvaluationDemoStatus,
} from "../evaluation/oracleEvaluationDemo";
import {
  startEpicEvaluationDemo,
  getEpicEvaluationDemoStatus,
} from "../evaluation/epicEvaluationDemo";
import {
  nextStableSpo2State,
  resolveBedsideWidgetValues,
} from "../presentation/episodeWidgetFallbacks";

const ECG_PAPER_SPEED_MM_PER_SEC = 25;
const DEFAULT_VISIBLE_SECONDS = 6;
const DISPLAY_VISIBLE_SECONDS = Math.max(
  3,
  Math.min(
    8,
    Number(import.meta.env.VITE_WAVEFORM_VISIBLE_SECONDS) ||
      DEFAULT_VISIBLE_SECONDS
  )
);
const DEFAULT_GAIN_MM_PER_MV = 10;
const DEFAULT_GAIN_MODE = "auto";
const EVALUATION_ENABLED =
  import.meta.env
    .VITE_ENABLE_SLM_EVAL ===
  "true";

const EVALUATION_BASE_SOURCE = (
  import.meta.env
    .VITE_EVALUATION_BASE_WAVEFORM_SOURCE ||
  "api_range"
)
  .trim()
  .toLowerCase()
  .replace("-", "_");

const API_RANGE_EPISODE_SOURCE =
  "api_range_episode";

const EPISODE_SOURCE_LABEL =
  EVALUATION_BASE_SOURCE === "incart"
    ? "INCART + Episode"
    : "API Range + Episode";

function backendWaveformSource(
  source
) {
  return (
    source ===
    API_RANGE_EPISODE_SOURCE
      ? EVALUATION_BASE_SOURCE
      : source
  );
}
/*
  Per-lead calibrated display gain.

  This does not alter ECG data or morphology.
  Each lead independently selects the largest stable visual gain that keeps
  every visible peak inside its own tile.
*/
const ECG_GAIN_OPTIONS = [
  0.25,
  0.5,
  1,
  2.5,
  5,
  10,
  20,
  40,
];

// V7.3.9 display-only autoscale tuning.
// Keep morphology/data untouched; only the visual mV-to-pixel gain changes.
const AUTO_GAIN_MIN = 0.02;
const AUTO_GAIN_MAX = 40;

// V7.3.11: each ECG lead keeps its own calibrated display gain.
// Every visible point in that specific lead participates in the safety cap,
// so the lead can use the largest gain that still contains its hardest peak.
// Morphology/data are never modified; this is display-only scaling.
// Internal gain is continuous; the UI label may round it for readability.
const AUTO_GAIN_ROUNDING = 100; // 0.01 mm/mV increments.


const WAVEFORM_SOURCES = [
  {
    id: "physionet",
    label: "PhysioNet",
  },
  {
    id: "csv",
    label: "CSV",
  },
  {
    id: "api_range",
    label: "API Range",
  },

  ...(
    EVALUATION_ENABLED
      ? [
          {
            id:
              API_RANGE_EPISODE_SOURCE,
            label:
              EPISODE_SOURCE_LABEL,
          },
        ]
      : []
  ),

  {
    id: "incart",
    label: "INCART",
  },
];
const ECG_ZERO_MV = 0;

// V7.3.12 display-only autoscale policy.
// Each lead stays independent. The controller has two jobs:
// 1) a synchronous hard ceiling keeps every *currently visible* point inside
//    the real rendered canvas with margin; 2) hysteresis prevents gain hunting
//    when a stable API Range waveform scrolls through the window.
const AUTO_HEADROOM = 0.80;
const AUTO_TARGET_PEAK_FILL = 0.68;
const AUTO_GAIN_BOOTSTRAP = 0.75;
const AUTO_GAIN_MIN_CALIBRATION_SECONDS = 0.75;
const AUTO_GAIN_ZOOM_IN_DEADBAND_RATIO = 1.20;
const AUTO_GAIN_TARGET_STABILITY_MS = 1200;
const AUTO_GAIN_POST_SAFETY_HOLD_MS = 1800;
const AUTO_GAIN_ZOOM_IN_TAU_MS = 2800;
const AUTO_GAIN_MAX_ZOOM_IN_PER_SECOND = 0.22;
const AUTO_GAIN_SAFETY_PAD = 0.985;

const MIN_PX_PER_MM = 3.2;
const MAX_PX_PER_MM = 9.0;
const GRID_GAP_PX = 6;

const WAVEFORM_COLUMN_COUNT = 2;
const WAVEFORM_ROW_COUNT = 3;
const VITAL_RAIL_WIDTH_PX = 160;

const ECG_MINOR_BOXES_PER_MAJOR = 5;
const PPG_MINI_GRAPH_POINTS = 64;

const LEADS = [
  { id: "lead1", label: "Lead I", color: "cyan", area: "lead1" },
  { id: "lead2", label: "Lead II", color: "cyan", area: "lead2" },
  { id: "lead3", label: "Lead III", color: "cyan", area: "lead3" },
  { id: "avr", label: "aVR", color: "cyan", area: "avr" },
  { id: "avl", label: "aVL", color: "cyan", area: "avl" },
  { id: "avf", label: "aVF", color: "cyan", area: "avf" },
];

function createEmptyLeads() {
  return Object.fromEntries(
    LEADS.map((lead) => [lead.id, []])
  );
}

const EMPTY_LEADS = createEmptyLeads();

const EMPTY_FRAME = {
  source: "physionet-ptb-xl",
  sampleRate: 220,
  batchSize: 0,
  receivedAt: "",
  leadsMv: EMPTY_LEADS,
  latestMv: {},
  xAxis: {
    secondsVisible: DEFAULT_VISIBLE_SECONDS,
  },
  vitals: {},
};

function createEmptyFrame(source = "physionet") {
  return {
    ...EMPTY_FRAME,
    source,
    leadsMv: createEmptyLeads(),
    latestMv: {},
    vitals: {},
  };
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function percentile(values, p) {
  if (!values.length) return 0;

  const sorted = [...values].sort((a, b) => a - b);
  const index = (p / 100) * (sorted.length - 1);
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  const weight = index - lower;

  if (lower === upper) return sorted[lower];

  return sorted[lower] * (1 - weight) + sorted[upper] * weight;
}

function median(values) {
  if (!values.length) return 0;

  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);

  if (sorted.length % 2 === 0) {
    return (sorted[middle - 1] + sorted[middle]) / 2;
  }

  return sorted[middle];
}

function formatMvValue(value) {
  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) return "--";
  if (Math.abs(numericValue) >= 10) return numericValue.toFixed(1);
  if (Math.abs(numericValue) >= 1) return numericValue.toFixed(2);

  return numericValue.toFixed(3);
}

function formatGainValue(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return "--";
  if (numericValue >= 10) return numericValue.toFixed(1);
  return numericValue.toFixed(2);
}

function getLeadStats(samples = [], latestFallback) {
  const values = samples.map(Number).filter(Number.isFinite);

  if (!values.length) {
    return {
      latest: formatMvValue(latestFallback),
      min: "--",
      max: "--",
      p2p: "--",
    };
  }

  const latest = values[values.length - 1];
  const min = Math.min(...values);
  const max = Math.max(...values);

  return {
    latest: formatMvValue(latest),
    min: formatMvValue(min),
    max: formatMvValue(max),
    p2p: formatMvValue(max - min),
  };
}


function appendLeadWindows(prev, frame) {
  const sampleRate = frame.sampleRate || 220;
  // Display history is decoupled from the backend capture contract.
  // This changes only what is visible on screen, not the 8 s episode, detector,
  // persisted evidence, or 6 s post-capture window.
  const visibleSeconds = DISPLAY_VISIBLE_SECONDS;

  const maxPoints = Math.round(sampleRate * visibleSeconds);
  const next = {};

  for (const lead of LEADS) {
    const previousSamples = prev[lead.id] || [];
    const incomingSamples = frame.leadsMv?.[lead.id] || [];

    next[lead.id] = [...previousSamples, ...incomingSamples].slice(-maxPoints);
  }

  return next;
}


function getLeadAmplitude(samples = []) {
  const values = samples.map(Number).filter(Number.isFinite);

  if (!values.length) {
    return {
      minMv: 0,
      maxMv: 0,
      p2pMv: 0,
      robustP2pMv: 0,
      displayP2pMv: 0,
      robustAbsMv: 0,
      displayAbsMv: 0,
      hardAbsMv: 0,
      centerMv: 0,
    };
  }

  const minMv = Math.min(...values);
  const maxMv = Math.max(...values);
  const p2pMv = maxMv - minMv;
  const centerMv = median(values);

  // Use the central 96% of the visible ECG for visual gain. This keeps one
  // transition spike from shrinking the next several seconds of waveform.
  const p02 = percentile(values, 2);
  const p98 = percentile(values, 98);
  const robustP2pMv = Math.max(0, p98 - p02);

  const centeredAbs = values.map((value) => Math.abs(value - centerMv));
  const absFromZero = values.map((value) => Math.abs(value - ECG_ZERO_MV));

  const robustAbsMv = percentile(centeredAbs, 98);
  const displayAbsMv = percentile(absFromZero, 99);

  // Do not let a single hard outlier dominate the gain. True recurring QRS
  // complexes still influence p02/p98 and the 98th/99th-percentile guards.
  const displayP2pMv = Math.max(
    robustP2pMv,
    robustAbsMv * 2,
    p2pMv * 0.12,
    0.01
  );

  return {
    minMv,
    maxMv,
    p2pMv,
    robustP2pMv,
    displayP2pMv,
    robustAbsMv,
    displayAbsMv,
    hardAbsMv: Math.max(...absFromZero),
    centerMv,
  };
}

function getHalfRowMm(rowHeightPx, pxPerMm) {
  if (!rowHeightPx || !pxPerMm) return 0;
  return rowHeightPx / pxPerMm / 2;
}

function getPeakFillRatio({ absMv, gainMmPerMv, rowHeightPx, pxPerMm }) {
  const halfRowMm = getHalfRowMm(rowHeightPx, pxPerMm);

  if (!halfRowMm || !gainMmPerMv) return 0;

  return (absMv * gainMmPerMv) / halfRowMm;
}

function peakFitsAtGain({ hardAbsMv, gainMmPerMv, rowHeightPx, pxPerMm }) {
  const fill = getPeakFillRatio({
    absMv: hardAbsMv,
    gainMmPerMv,
    rowHeightPx,
    pxPerMm,
  });

  return fill <= AUTO_HEADROOM;
}

function getP2pFillRatio({ p2pMv, gainMmPerMv, rowHeightPx, pxPerMm }) {
  if (!rowHeightPx || !pxPerMm || !gainMmPerMv) return 0;

  const rowHeightMm = rowHeightPx / pxPerMm;

  if (!rowHeightMm) return 0;

  return (p2pMv * gainMmPerMv) / rowHeightMm;
}

function getClosestGain(allowedGains, idealGain) {
  if (!allowedGains.length) return DEFAULT_GAIN_MM_PER_MV;

  return allowedGains.reduce((bestGain, gain) => {
    const bestDistance = Math.abs(bestGain - idealGain);
    const nextDistance = Math.abs(gain - idealGain);

    return nextDistance < bestDistance ? gain : bestGain;
  }, allowedGains[0]);
}

function floorSafeGain(value) {
  const finite = Number(value);
  if (!Number.isFinite(finite)) return AUTO_GAIN_MIN;

  return Math.max(
    AUTO_GAIN_MIN,
    Math.floor(
      clamp(finite, AUTO_GAIN_MIN, AUTO_GAIN_MAX) *
        AUTO_GAIN_ROUNDING
    ) / AUTO_GAIN_ROUNDING
  );
}

function getHardSafeGain({
  samples,
  rowHeightPx,
  pxPerMm,
}) {
  const halfRowMm = getHalfRowMm(rowHeightPx, pxPerMm);
  const amplitude = getLeadAmplitude(samples);
  const hardAbsMv = amplitude.hardAbsMv;

  if (
    !halfRowMm ||
    !Number.isFinite(hardAbsMv) ||
    hardAbsMv < 0.005
  ) {
    return {
      hardSafeGain: AUTO_GAIN_MAX,
      amplitude,
    };
  }

  return {
    hardSafeGain: clamp(
      (
        AUTO_HEADROOM *
        AUTO_GAIN_SAFETY_PAD *
        halfRowMm
      ) / hardAbsMv,
      AUTO_GAIN_MIN,
      AUTO_GAIN_MAX
    ),
    amplitude,
  };
}

function chooseLeadAutoGain({
  samples,
  currentGain,
  rowHeightPx,
  pxPerMm,
  sampleRate,
  nowMs,
  controller,
}) {
  const current = clamp(
    Number(currentGain) || AUTO_GAIN_BOOTSTRAP,
    AUTO_GAIN_MIN,
    AUTO_GAIN_MAX
  );

  if (!rowHeightPx || !pxPerMm) {
    return current;
  }

  const {
    hardSafeGain,
    amplitude,
  } = getHardSafeGain({
    samples,
    rowHeightPx,
    pxPerMm,
  });

  const hardAbsMv = amplitude.hardAbsMv;

  if (!Number.isFinite(hardAbsMv) || hardAbsMv < 0.005) {
    return current;
  }

  const representativeAbsMv = Math.max(
    amplitude.displayAbsMv,
    amplitude.robustAbsMv,
    amplitude.displayP2pMv / 2,
    0.01
  );

  const halfRowMm = getHalfRowMm(rowHeightPx, pxPerMm);

  const preferredGain = clamp(
    (AUTO_TARGET_PEAK_FILL * halfRowMm) /
      representativeAbsMv,
    AUTO_GAIN_MIN,
    AUTO_GAIN_MAX
  );

  const targetGain = Math.min(
    preferredGain,
    hardSafeGain
  );

  const state = controller || {};
  const timestamp = Number.isFinite(nowMs)
    ? nowMs
    : performance.now();

  const requiredCalibrationSamples = Math.max(
    1,
    Math.round(
      (Number(sampleRate) || 220) *
        AUTO_GAIN_MIN_CALIBRATION_SECONDS
    )
  );

  // Start low and stable while the rolling window is filling. Once enough
  // actual samples exist, make one initial calibration instead of repeatedly
  // chasing every new peak during the first second of API Range playback.
  if (!state.initialized) {
    if (samples.length < requiredCalibrationSamples) {
      return Math.min(
        current,
        AUTO_GAIN_BOOTSTRAP,
        hardSafeGain
      );
    }

    state.initialized = true;
    state.lastUpdatedAt = timestamp;
    state.holdUntil =
      timestamp + AUTO_GAIN_POST_SAFETY_HOLD_MS;
    state.candidateGain = null;
    state.candidateSince = null;

    return floorSafeGain(
      Math.min(
        targetGain,
        hardSafeGain * AUTO_GAIN_SAFETY_PAD
      )
    );
  }

  // Absolute safety has priority over smoothness. If a new visible point would
  // exceed the real canvas, lower THIS lead's gain immediately. This is the
  // only intentionally immediate autoscale transition.
  if (current > hardSafeGain) {
    state.lastUpdatedAt = timestamp;
    state.holdUntil =
      timestamp + AUTO_GAIN_POST_SAFETY_HOLD_MS;
    state.candidateGain = null;
    state.candidateSince = null;

    return floorSafeGain(
      hardSafeGain * AUTO_GAIN_SAFETY_PAD
    );
  }

  // Once safe, do not "breathe" the gain for small target changes. A stable
  // waveform should look stable even while the 6 s rolling window advances.
  if (
    targetGain <=
    current * AUTO_GAIN_ZOOM_IN_DEADBAND_RATIO
  ) {
    state.candidateGain = null;
    state.candidateSince = null;
    state.lastUpdatedAt = timestamp;
    return current;
  }

  if (
    Number(state.holdUntil) > timestamp
  ) {
    return current;
  }

  // Require the higher-gain target to remain similar for a while before
  // zooming in. This prevents recurring QRS peaks entering/leaving the window
  // from repeatedly changing gain.
  const previousCandidate = Number(state.candidateGain);
  const candidateChanged =
    !Number.isFinite(previousCandidate) ||
    Math.abs(
      targetGain - previousCandidate
    ) /
      Math.max(previousCandidate, 0.01) >
      0.10;

  if (candidateChanged) {
    state.candidateGain = targetGain;
    state.candidateSince = timestamp;
    return current;
  }

  if (
    timestamp -
      Number(state.candidateSince || timestamp) <
    AUTO_GAIN_TARGET_STABILITY_MS
  ) {
    return current;
  }

  const previousUpdate = Number(state.lastUpdatedAt);
  const dtMs = Number.isFinite(previousUpdate)
    ? clamp(timestamp - previousUpdate, 16, 500)
    : 50;

  state.lastUpdatedAt = timestamp;

  // Time-based easing makes behavior independent of browser/render frequency.
  const alpha =
    1 - Math.exp(-dtMs / AUTO_GAIN_ZOOM_IN_TAU_MS);

  const eased =
    current + (targetGain - current) * alpha;

  // Additional rate limiter: even after the target is accepted, a lead cannot
  // suddenly zoom in because the browser happened to pause between renders.
  const maxRateGain =
    current *
    Math.exp(
      AUTO_GAIN_MAX_ZOOM_IN_PER_SECOND *
        (dtMs / 1000)
    );

  const next = Math.min(
    eased,
    maxRateGain,
    targetGain,
    hardSafeGain * AUTO_GAIN_SAFETY_PAD
  );

  return floorSafeGain(next);
}

function getDisplayScale({ samples, gainMmPerMv, rowHeightPx, pxPerMm }) {
  const amplitude = getLeadAmplitude(samples);
  const safeGain = gainMmPerMv || DEFAULT_GAIN_MM_PER_MV;
  const halfRowMm = getHalfRowMm(rowHeightPx, pxPerMm);
  const halfRangeMv = halfRowMm > 0 ? halfRowMm / safeGain : 1;

  const peakFill = getPeakFillRatio({
    absMv: amplitude.hardAbsMv,
    gainMmPerMv: safeGain,
    rowHeightPx,
    pxPerMm,
  });

  return {
    centerMv: 0,
    halfRangeMv,
    topMv: halfRangeMv,
    bottomMv: -halfRangeMv,
    mvPerLargeBox: 5 / safeGain,
    peakFillPercent: Math.round(Math.min(1.5, peakFill) * 100),
    clipRisk: peakFill > AUTO_HEADROOM,
  };
}

function isZeroVisible() {
  return true;
}

function getZeroLineTopPercent() {
  return 50;
}



export default function SevenLeadWaveformPage({
  patient,
  onOpenAnalytics,
  evaluationDemo,
  onEvaluationChange,
  onEvaluationAnalysisComplete,
  oracleAutoDemo,
  onOracleAutoDemoChange,
  epicAutoDemo,
  onEpicAutoDemoChange,
}) {
  // Treat the URL mode as authoritative from the very first React render.
  // oracleAutoDemo/epicAutoDemo are populated asynchronously after bootstrap;
  // relying only on those props briefly opened INCART before API Range.
  // On a single-instance Cloud Run service, repeated SMART launches could
  // accumulate long-lived SSE requests and exhaust request concurrency.
  const browserSmartAutoMode = useMemo(() => {
    if (typeof window === "undefined") return false;

    const mode = new URLSearchParams(window.location.search).get("mode");
    return (
      mode === "oracle-evaluation-auto" ||
      mode === "epic-evaluation-auto"
    );
  }, []);

  const autoEvaluationEnabled =
    browserSmartAutoMode ||
    Boolean(oracleAutoDemo?.enabled || epicAutoDemo?.enabled);

  const initialWaveformSource = autoEvaluationEnabled
    ? API_RANGE_EPISODE_SOURCE
    : "incart";

  const [waveFrame, setWaveFrame] = useState(() =>
    createEmptyFrame(backendWaveformSource(initialWaveformSource))
  );
  const [leadWindows, setLeadWindows] = useState(EMPTY_LEADS);
  const [streamStatus, setStreamStatus] = useState("connecting");
  const [waveformSource, setWaveformSource] =
    useState(initialWaveformSource);
  const [
  sourceBeforeEvaluation,
  setSourceBeforeEvaluation,
] = useState("incart");

  const waveformSessionIdRef =
    useRef(
      getWaveformSessionId()
    );

  const autoOracleStartIssuedRef =
    useRef(false);
  const autoEpicStartIssuedRef =
    useRef(false);
  const autoOracleCompletionNoticeRef =
    useRef("");
  const autoEpicCompletionNoticeRef =
    useRef("");

  const [
    injectionControlsOpen,
    setInjectionControlsOpen,
  ] = useState(false);

  const [
    injectionScenarioId,
    setInjectionScenarioId,
  ] = useState(
    "VT-ISCHEMIC-003"
  );

  const [
    injectionStatus,
    setInjectionStatus,
  ] = useState({
    state: "IDLE",
  });

  const [
    injectionScenarios,
    setInjectionScenarios,
  ] = useState([]);

  const [
    injectionScenariosLoading,
    setInjectionScenariosLoading,
  ] = useState(false);

  const [
    injectionError,
    setInjectionError,
  ] = useState("");

  const [
    evaluationEpisodes,
    setEvaluationEpisodes,
  ] = useState([]);

  const [
    selectedEvaluationId,
    setSelectedEvaluationId,
  ] = useState("");

  const [
    evaluationLoading,
    setEvaluationLoading,
  ] = useState(false);

  const [
    evaluationPlaying,
    setEvaluationPlaying,
  ] = useState(false);

  const [
    evaluationError,
    setEvaluationError,
  ] = useState("");

  const [
    evaluationAnalysisStatus,
    setEvaluationAnalysisStatus,
  ] = useState("idle");

  const evaluationPlaybackRef =
    useRef(null);



  const selectedEvaluationIdRef =
    useRef("");

  const evaluationRunRequestsRef =
    useRef(new Map());

  const [
  latestEpisodeId,
  setLatestEpisodeId,
] = useState(null);
 

  const [leadAutoGains, setLeadAutoGains] = useState(() =>
    Object.fromEntries(LEADS.map((lead) => [lead.id, AUTO_GAIN_BOOTSTRAP]))
  );

  // Actual rendered canvas geometry, measured per lead. The previous autoscale
  // estimated tile height from the outer grid; overlays/padding made that
  // estimate too optimistic and allowed peaks to hit the WebGL clamp.
  const [leadViewportMetrics, setLeadViewportMetrics] = useState({});

  const leadGainControllerRef = useRef(
    Object.fromEntries(
      LEADS.map((lead) => [
        lead.id,
        {
          initialized: false,
          holdUntil: 0,
          candidateGain: null,
          candidateSince: null,
          lastUpdatedAt: 0,
        },
      ])
    )
  );

  const [gridSize, setGridSize] = useState({
    width: 0,
    height: 0,
  });

  const gridRef = useRef(null);

  function updateLeadViewportMetrics(
    leadId,
    metrics
  ) {
    const widthPx = Number(metrics?.widthPx);
    const heightPx = Number(metrics?.heightPx);

    if (
      !Number.isFinite(widthPx) ||
      !Number.isFinite(heightPx) ||
      widthPx <= 0 ||
      heightPx <= 0
    ) {
      return;
    }

    setLeadViewportMetrics((previous) => {
      const prior = previous[leadId];

      if (
        prior &&
        Math.abs(prior.widthPx - widthPx) < 0.5 &&
        Math.abs(prior.heightPx - heightPx) < 0.5
      ) {
        return previous;
      }

      return {
        ...previous,
        [leadId]: {
          widthPx,
          heightPx,
        },
      };
    });
  }

  useEffect(() => {
    if (!oracleAutoDemo?.enabled) {
      autoOracleStartIssuedRef.current = false;
      return;
    }

    if (
      waveformSource !==
      API_RANGE_EPISODE_SOURCE
    ) {
      changeWaveformSource(
        API_RANGE_EPISODE_SOURCE
      );
    }
  }, [oracleAutoDemo?.enabled]);

  useEffect(() => {
    if (
      !oracleAutoDemo?.enabled ||
      oracleAutoDemo?.status !== "ready" ||
      waveformSource !==
        API_RANGE_EPISODE_SOURCE ||
      streamStatus !== "live" ||
      autoOracleStartIssuedRef.current
    ) {
      return;
    }

    autoOracleStartIssuedRef.current = true;
    setInjectionError("");
    onOracleAutoDemoChange?.({ status: "arming" });

    startOracleEvaluationDemo(
      waveformSessionIdRef.current
    )
      .then((result) => {
        setInjectionStatus(result);

        const state = String(
          result?.state || ""
        ).toUpperCase();

        onOracleAutoDemoChange?.({
          status:
            state === "COMPLETE"
              ? "complete"
              : ["FAILED", "CANCELLED"].includes(state)
              ? "error"
              : "running",
          result,
        });
      })
      .catch((error) => {
        const message =
          error instanceof Error
            ? error.message
            : "Automatic Oracle evaluation could not start.";

        setInjectionError(message);
        onOracleAutoDemoChange?.({
          status: "error",
          error: message,
        });
      });
  }, [
    oracleAutoDemo?.enabled,
    oracleAutoDemo?.status,
    waveformSource,
    streamStatus,
    onOracleAutoDemoChange,
  ]);

  useEffect(() => {
    if (!epicAutoDemo?.enabled) {
      autoEpicStartIssuedRef.current = false;
      return;
    }
    if (waveformSource !== API_RANGE_EPISODE_SOURCE) {
      changeWaveformSource(API_RANGE_EPISODE_SOURCE);
    }
  }, [epicAutoDemo?.enabled]);

  useEffect(() => {
    if (
      !epicAutoDemo?.enabled ||
      epicAutoDemo?.status !== "ready" ||
      waveformSource !== API_RANGE_EPISODE_SOURCE ||
      streamStatus !== "live" ||
      autoEpicStartIssuedRef.current
    ) return;

    autoEpicStartIssuedRef.current = true;
    setInjectionError("");
    onEpicAutoDemoChange?.({ status: "arming" });
    startEpicEvaluationDemo(waveformSessionIdRef.current)
      .then((result) => {
        setInjectionStatus(result);
        const state=String(result?.state||"").toUpperCase();
        onEpicAutoDemoChange?.({status:state==="COMPLETE"?"complete":["FAILED","CANCELLED"].includes(state)?"error":"running",result});
      })
      .catch((error) => {
        const message=error instanceof Error?error.message:"Automatic Epic evaluation could not start.";
        setInjectionError(message);
        onEpicAutoDemoChange?.({status:"error",error:message});
      });
  }, [epicAutoDemo?.enabled,epicAutoDemo?.status,waveformSource,streamStatus,onEpicAutoDemoChange]);

  // SSE remains the fast path, but Cloud Run/browser transitions can miss a
  // single completion event. Poll the provider-specific status endpoint while
  // an automatic SMART evaluation is running and emit the same Analytics
  // notice when COMPLETE is observed. This makes navigation deterministic.
  useEffect(() => {
    const state = String(oracleAutoDemo?.status || "").toLowerCase();
    if (!oracleAutoDemo?.enabled || !["arming", "running"].includes(state)) {
      return undefined;
    }

    let cancelled = false;
    let timerId = null;
    const sessionId = waveformSessionIdRef.current;

    const poll = async () => {
      try {
        const result = await getOracleEvaluationDemoStatus(sessionId);
        if (cancelled) return;

        setInjectionStatus(result);
        const resultState = String(result?.state || "").toUpperCase();

        if (resultState === "COMPLETE") {
          const completionKey = [
            result?.episodeId || "",
            result?.incidentId || "",
            result?.scenarioId || "",
          ].join("|");

          onOracleAutoDemoChange?.({ status: "complete", result });

          if (
            completionKey &&
            autoOracleCompletionNoticeRef.current !== completionKey
          ) {
            autoOracleCompletionNoticeRef.current = completionKey;
            onEvaluationAnalysisComplete?.({
              mode: "evaluation_injection",
              status: "ready",
              title: result?.title || "Evaluation analysis completed",
              message:
                result?.message ||
                "The captured evaluation episode is ready in Analytics.",
              episodeId: result?.episodeId || null,
              incidentId: result?.incidentId || null,
              scenarioId: result?.scenarioId || null,
              score: result?.score || null,
              oracleDemo:
                result?.oracleDemo || {
                  mode: "oracle_evaluation_auto",
                  patient: oracleAutoDemo?.patient || null,
                  scenarioId:
                    result?.scenarioId ||
                    oracleAutoDemo?.scenario?.scenarioId ||
                    null,
                },
            });
          }
          return;
        }

        if (["FAILED", "CANCELLED"].includes(resultState)) {
          const message =
            result?.error ||
            result?.message ||
            "Automatic Oracle evaluation did not complete.";
          setInjectionError(message);
          onOracleAutoDemoChange?.({ status: "error", error: message, result });
          return;
        }
      } catch (error) {
        if (cancelled) return;
        // A transient status read should not cancel an otherwise healthy run.
      }

      timerId = window.setTimeout(poll, 700);
    };

    timerId = window.setTimeout(poll, 450);

    return () => {
      cancelled = true;
      if (timerId != null) window.clearTimeout(timerId);
    };
  }, [oracleAutoDemo?.enabled, oracleAutoDemo?.status]);

  useEffect(() => {
    const state = String(epicAutoDemo?.status || "").toLowerCase();
    if (!epicAutoDemo?.enabled || !["arming", "running"].includes(state)) {
      return undefined;
    }

    let cancelled = false;
    let timerId = null;
    const sessionId = waveformSessionIdRef.current;

    const poll = async () => {
      try {
        const result = await getEpicEvaluationDemoStatus(sessionId);
        if (cancelled) return;

        setInjectionStatus(result);
        const resultState = String(result?.state || "").toUpperCase();

        if (resultState === "COMPLETE") {
          const completionKey = [
            result?.episodeId || "",
            result?.incidentId || "",
            result?.scenarioId || "",
          ].join("|");

          onEpicAutoDemoChange?.({ status: "complete", result });

          if (
            completionKey &&
            autoEpicCompletionNoticeRef.current !== completionKey
          ) {
            autoEpicCompletionNoticeRef.current = completionKey;
            onEvaluationAnalysisComplete?.({
              mode: "evaluation_injection",
              status: "ready",
              title: result?.title || "Evaluation analysis completed",
              message:
                result?.message ||
                "The captured evaluation episode is ready in Analytics.",
              episodeId: result?.episodeId || null,
              incidentId: result?.incidentId || null,
              scenarioId: result?.scenarioId || null,
              score: result?.score || null,
              epicDemo:
                result?.epicDemo || {
                  mode: "epic_evaluation_auto",
                  patient: epicAutoDemo?.patient || null,
                  scenarioId:
                    result?.scenarioId ||
                    epicAutoDemo?.scenario?.scenarioId ||
                    null,
                },
            });
          }
          return;
        }

        if (["FAILED", "CANCELLED"].includes(resultState)) {
          const message =
            result?.error ||
            result?.message ||
            "Automatic Epic evaluation did not complete.";
          setInjectionError(message);
          onEpicAutoDemoChange?.({ status: "error", error: message, result });
          return;
        }
      } catch (error) {
        if (cancelled) return;
      }

      timerId = window.setTimeout(poll, 700);
    };

    timerId = window.setTimeout(poll, 450);

    return () => {
      cancelled = true;
      if (timerId != null) window.clearTimeout(timerId);
    };
  }, [epicAutoDemo?.enabled, epicAutoDemo?.status]);


  useEffect(() => {
    if (!EVALUATION_ENABLED) {
      return undefined;
    }

    let active = true;
    setInjectionScenariosLoading(true);

    listEvaluationInjectionScenarios()
      .then((payload) => {
        if (!active) return;

        const scenarios = Array.isArray(payload?.scenarios)
          ? payload.scenarios.filter((item) => item?.available !== false)
          : [];

        setInjectionScenarios(scenarios);
        setInjectionScenarioId((current) => {
          if (scenarios.some((item) => item.scenarioId === current)) {
            return current;
          }

          return scenarios[0]?.scenarioId || "";
        });
      })
      .catch((error) => {
        if (!active) return;

        console.error(
          "[KGEN EVAL INJECTION SCENARIOS]",
          error
        );

        setInjectionScenarios([]);
        setInjectionScenarioId("");
        setInjectionError(
          error instanceof Error
            ? error.message
            : "Could not load evaluation scenarios."
        );
      })
      .finally(() => {
        if (active) {
          setInjectionScenariosLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  async function armApiRangeEvaluation() {
    if (
      waveformSource !==
      API_RANGE_EPISODE_SOURCE
    ) {
      setInjectionError(
        `Select ${EPISODE_SOURCE_LABEL} before starting the mapped episode.`
      );
      return;
    }

    setInjectionError("");

    try {
      const result =
        await armEvaluationInjection(
          waveformSessionIdRef.current,
          {
            scenarioId:
              injectionScenarioId,
            baselineSeconds: 10,
            preSeconds: 6,
            postSeconds: 6,
            runSlm: true,
          }
        );

      setInjectionStatus(
        result
      );

      console.info(
        "[KGEN EVAL INJECTION UI] armed",
        result
      );
    } catch (error) {
      console.error(
        "[KGEN EVAL INJECTION UI] arm failed",
        error
      );

      setInjectionError(
        error instanceof Error
          ? error.message
          : "Could not arm evaluation injection."
      );
    }
  }

  async function cancelApiRangeEvaluation() {
    try {
      const result =
        await cancelEvaluationInjection(
          waveformSessionIdRef.current
        );

      setInjectionStatus(
        result
      );

      setInjectionControlsOpen(
        false
      );
    } catch (error) {
      setInjectionError(
        error instanceof Error
          ? error.message
          : "Could not cancel evaluation injection."
      );
    }
  }

  function clearEvaluationState() {
    evaluationPlaybackRef.current?.stop();
    evaluationPlaybackRef.current = null;

   

 
    selectedEvaluationIdRef.current = "";

    setSelectedEvaluationId("");
    setEvaluationPlaying(false);
    setEvaluationLoading(false);
    setEvaluationError("");
    setEvaluationAnalysisStatus("idle");

    onEvaluationChange?.({
      active: false,
      episodeId: null,
      episode: null,
      run: null,
      capture: null,
    });
  }

  function changeWaveformSource(nextSource) {
    if (nextSource === waveformSource) {
      return;
    }

    if (nextSource === "evaluation") {
      if (!EVALUATION_ENABLED) {
        return;
      }

      setSourceBeforeEvaluation(
        waveformSource
      );

      evaluationPlaybackRef.current?.stop();
      evaluationPlaybackRef.current = null;

      setSelectedEvaluationId("");
      setEvaluationPlaying(false);
      setEvaluationError("");
      setStreamStatus("evaluation");
      setWaveFrame(
        createEmptyFrame(
          "cardinal-evaluation"
        )
      );
      setLeadWindows(
        createEmptyLeads()
      );
      setWaveformSource(
        "evaluation"
      );

      onEvaluationChange?.({
        active: true,
        episodeId: null,
        episode: null,
        run: null,
        capture: null,
      });

      return;
    }

    if (
      waveformSource === "evaluation"
    ) {
      clearEvaluationState();
    }

    setStreamStatus("connecting");
    setWaveFrame(
      createEmptyFrame(nextSource)
    );
    setLeadWindows(
      createEmptyLeads()
    );

    setLeadAutoGains(
      Object.fromEntries(
        LEADS.map((lead) => [
          lead.id,
          AUTO_GAIN_BOOTSTRAP,
        ])
      )
    );
    leadGainControllerRef.current =
      Object.fromEntries(
        LEADS.map((lead) => [
          lead.id,
          {
            initialized: false,
            holdUntil: 0,
            candidateGain: null,
            candidateSince: null,
            lastUpdatedAt: 0,
          },
        ])
      );

    setWaveformSource(nextSource);
  }

  function handleEvaluationFrame(
    frame
  ) {
    setWaveFrame(frame);
    setLeadWindows(
      frame.leadsMv || {}
    );
    setStreamStatus(
      "evaluation"
    );
  }

  async function resolveEvaluationAnalysis(
    episodeId,
    adaptedEpisode,
    evaluationCapture
  ) {
    if (!episodeId) {
      return null;
    }

    const existingRequest =
      evaluationRunRequestsRef.current.get(
        episodeId
      );

    if (existingRequest) {
      console.info(
        "[KGEN EVAL UI] reusing in-flight analysis",
        { episodeId }
      );
      return existingRequest;
    }

    const request = (async () => {
      const startedAt = performance.now();

      try {
        setEvaluationAnalysisStatus(
          "checking"
        );

        console.info(
          "[KGEN EVAL UI] checking saved result",
          { episodeId }
        );

        const health =
          await getEvaluationHealth();

        const configuredModel =
          health?.configuredModel ||
          null;

        console.info(
          "[KGEN EVAL UI] configured model resolved",
          {
            episodeId,
            configuredModel,
          }
        );

        let run =
          await getLatestCompletedEvaluationRun(
            episodeId,
            configuredModel
          ).catch(() => null);

        let source = "saved";

        if (!run) {
          source = "generated";
          setEvaluationAnalysisStatus(
            "running"
          );

          console.info(
            "[KGEN EVAL UI] no saved result; starting configured SLM",
            {
              episodeId,
              modelSelection:
                configuredModel ||
                "backend SLM_MODEL",
            }
          );

          // model is intentionally omitted. The backend uses
          // the model currently selected in SLM_MODEL.
          run = await runEvaluationSlm(
            episodeId,
            { temperature: 0 }
          );
        } else {
          console.info(
            "[KGEN EVAL UI] saved analysis loaded",
            {
              episodeId,
              runId: run.runId,
              model: run.model?.name,
              score: run.score?.total,
            }
          );
        }

        if (
          selectedEvaluationIdRef.current ===
          episodeId
        ) {
          onEvaluationChange?.({
            active: true,
            episodeId,
            episode: adaptedEpisode,
            run,
            capture: evaluationCapture,
          });

          setEvaluationAnalysisStatus(
            "ready"
          );
        }

        const elapsedMs = Math.round(
          performance.now() - startedAt
        );

        console.info(
          "[KGEN EVAL UI] analysis ready",
          {
            episodeId,
            source,
            runId: run?.runId,
            model: run?.model?.name,
            score: run?.score?.total,
            safetyPass: run?.score?.safetyPass,
            elapsedMs,
          }
        );

        onEvaluationAnalysisComplete?.({
          mode: "evaluation",
          status: "ready",
          title: "Evaluation analysis completed",
          message: [
            episodeId,
            run?.model?.name ||
              "configured model",
            run?.score?.total != null
              ? `score ${run.score.total}/100`
              : null,
            run?.score?.safetyPass != null
              ? `safety ${
                  run.score.safetyPass
                    ? "PASS"
                    : "FAIL"
                }`
              : null,
          ]
            .filter(Boolean)
            .join(" • "),
          episodeId,
          runId: run?.runId || null,
          source,
        });

        return run;
      } catch (error) {
        console.error(
          "[KGEN EVAL UI] analysis failed",
          { episodeId, error }
        );

        if (
          selectedEvaluationIdRef.current ===
          episodeId
        ) {
          setEvaluationAnalysisStatus(
            "error"
          );
          setEvaluationError(
            error instanceof Error
              ? error.message
              : "Evaluation analysis failed."
          );
        }

        return null;
      } finally {
        evaluationRunRequestsRef.current.delete(
          episodeId
        );
      }
    })();

    evaluationRunRequestsRef.current.set(
      episodeId,
      request
    );

    return request;
  }

  async function loadEvaluationEpisode(
    episodeId
  ) {
    setSelectedEvaluationId(
      episodeId
    );
    selectedEvaluationIdRef.current =
      episodeId;

   

    evaluationPlaybackRef.current?.stop();
    evaluationPlaybackRef.current = null;
    setEvaluationPlaying(false);

    if (!episodeId) {
      setWaveFrame(
        createEmptyFrame(
          "cardinal-evaluation"
        )
      );
      setLeadWindows(
        createEmptyLeads()
      );

      onEvaluationChange?.({
        active: true,
        episodeId: null,
        episode: null,
        run: null,
        capture: null,
      });

      return;
    }

    setEvaluationLoading(true);
    setEvaluationError("");
    setStreamStatus("connecting");

    try {
      console.info(
        "[KGEN EVAL UI] loading episode",
        { episodeId }
      );

      const record =
        await getEvaluationEpisode(
          episodeId
        );

      const adaptedEpisode =
        adaptEvaluationEpisode(
          record
        );
const evaluationDurationSeconds =
  Number(
    adaptedEpisode
      ?.ecg
      ?.durationSeconds
  ) || 8;

const evaluationCapture = {
  mode:
    "scenario_reference",

  referenceOnsetSeconds:
    0,

  referenceEndSeconds:
    evaluationDurationSeconds,

  triggerAnnotations:
    [],
};
      onEvaluationChange?.({
        active: true,
        episodeId,
        episode:
          adaptedEpisode,
        run: null,
        capture: evaluationCapture,
      });

      const playback =
  createEvaluationPlayback({
    episode:
      adaptedEpisode,

    cyclic: true,

    onFrame:
      handleEvaluationFrame,

    onStateChange: ({
      playing,
    }) => {
      setEvaluationPlaying(
        Boolean(playing)
      );

      setStreamStatus(
        "evaluation"
      );
    },

    onCycle: ({
      cycleCount,
    }) => {
      console.info(
        "[KGEN EVAL UI] cyclic waveform cycle completed",
        {
          episodeId,
          cycleCount,
        }
      );
    },
  });

      evaluationPlaybackRef.current =
        playback;

      playback.restart({
        autoPlay: true,
      });

      setEvaluationAnalysisStatus(
        "checking"
      );

      void resolveEvaluationAnalysis(
        episodeId,
        adaptedEpisode,
        evaluationCapture
      );
    } catch (error) {
      console.error(
        "[KGEN EVALUATION LOAD ERROR]",
        error
      );

      setEvaluationError(
        error instanceof Error
          ? error.message
          : "Could not load the evaluation episode."
      );

      setStreamStatus(
        "warning"
      );
    } finally {
      setEvaluationLoading(
        false
      );
    }
  }

 function toggleEvaluationPlayback() {
  if (
    !evaluationPlaybackRef.current ||
    !evaluationDemo?.episode
  ) {
    return;
  }

  if (evaluationPlaying) {
    evaluationPlaybackRef.current.pause();

    console.info(
      "[KGEN EVAL UI] cyclic playback paused",
      {
        episodeId:
          selectedEvaluationIdRef.current,
      }
    );

    return;
  }

  evaluationPlaybackRef.current.play();

  console.info(
    "[KGEN EVAL UI] cyclic playback resumed",
    {
      episodeId:
        selectedEvaluationIdRef.current,
    }
  );
}

  useEffect(() => {
    if (
      waveformSource !== "evaluation"
    ) {
      return undefined;
    }

    let active = true;

    setEvaluationLoading(true);
    setEvaluationError("");

    listEvaluationEpisodes()
      .then((manifest) => {
        if (!active) {
          return;
        }

        setEvaluationEpisodes(
          Array.isArray(
            manifest?.episodes
          )
            ? manifest.episodes
            : []
        );
      })
      .catch((error) => {
        if (!active) {
          return;
        }

        console.error(
          "[KGEN EVALUATION LIST ERROR]",
          error
        );

        setEvaluationError(
          error instanceof Error
            ? error.message
            : "Could not load evaluation episodes."
        );

        setStreamStatus(
          "warning"
        );
      })
      .finally(() => {
        if (active) {
          setEvaluationLoading(
            false
          );
        }
      });

    return () => {
      active = false;
    };
  }, [waveformSource]);

  useEffect(() => {
    return () => {
      evaluationPlaybackRef.current?.stop();

      
    };
  }, []);

useEffect(() => {
  let active = true;

  if (
    waveformSource === "evaluation"
  ) {
    return undefined;
  }

  // In Oracle/Epic automatic evaluation mode the only legal bedside stream
  // is API Range + Episode. If state is still transitioning, wait instead of
  // opening INCART/PhysioNet for a few milliseconds. This removes the source
  // race visible on Cloud Run.
  if (
    autoEvaluationEnabled &&
    waveformSource !== API_RANGE_EPISODE_SOURCE
  ) {
    setStreamStatus("connecting");
    return undefined;
  }

  const streamSource =
    backendWaveformSource(
      waveformSource
    );

  setStreamStatus("connecting");
  setWaveFrame(
    createEmptyFrame(
      streamSource
    )
  );
  setLeadWindows(createEmptyLeads());

  setLeadAutoGains(
    Object.fromEntries(
      LEADS.map((lead) => [
        lead.id,
        AUTO_GAIN_BOOTSTRAP,
      ])
    )
  );
  leadGainControllerRef.current =
    Object.fromEntries(
      LEADS.map((lead) => [
        lead.id,
        {
          initialized: false,
          holdUntil: 0,
          candidateGain: null,
          candidateSince: null,
          lastUpdatedAt: 0,
        },
      ])
    );

  const disconnectWaveforms = connectWaveformStream({
    source: streamSource,
    sessionId:
      waveformSessionIdRef.current,

    onFrame: (frame) => {
      if (!active) return;

      setWaveFrame(frame);

      if (
        frame.evaluationInjection
      ) {
        setInjectionStatus(
          frame.evaluationInjection
        );

        setInjectionError(
          frame.evaluationInjection
            .error ||
          ""
        );
      }

      setLeadWindows((previous) =>
        appendLeadWindows(previous, frame)
      );

      setStreamStatus(
        frame.status === "connected"
          ? "live"
          : "warning"
      );
    },

    onError: () => {
      if (active) {
        setStreamStatus("warning");
      }
    },
  });

  return () => {
    active = false;
    disconnectWaveforms?.();
  };
}, [
  patient?.id,
  waveformSource,
  autoEvaluationEnabled,
]);


useEffect(() => {
  let active = true;

  getLatestEpisode()
    .then((episode) => {
      if (active && episode?.id) {
        setLatestEpisodeId(episode.id);
      }
    })
    .catch(() => {});

  const disconnect = connectEpisodeEvents({
    onEvent: (event) => {
      if (
        active &&
        String(
          event.type || ""
        ).startsWith(
          "evaluation.injection."
        ) &&
        event.sessionId ===
          waveformSessionIdRef.current
      ) {
        setInjectionStatus(
          event
        );

        if (
          event.type ===
          "evaluation.injection.complete"
        ) {
          setLatestEpisodeId(
            event.episodeId ||
            null
          );
        }

        if (
          event.type ===
          "evaluation.injection.failed"
        ) {
          setInjectionError(
            event.message ||
            event.error ||
            "Evaluation injection failed."
          );
        }
      }

      if (
        active &&
        event.type === "episode.captured" &&
        event.episodeId
      ) {
        setLatestEpisodeId(
          event.episodeId
        );
      }
    },
    onError: () => {},
  });

  return () => {
    active = false;
    disconnect?.();
  };
}, []);


  useEffect(() => {
    const gridElement = gridRef.current;

    if (!gridElement) return undefined;

    function updateMeasurements() {
      const rect = gridElement.getBoundingClientRect();

      setGridSize({
        width: Math.max(1, rect.width),
        height: Math.max(1, rect.height),
      });
    }

    updateMeasurements();

    const observer = new ResizeObserver(updateMeasurements);
    observer.observe(gridElement);

    return () => observer.disconnect();
  }, []);

const visibleSeconds = DISPLAY_VISIBLE_SECONDS;

const sampleRate = waveFrame.sampleRate || 220;
const visiblePoints = Math.round(sampleRate * visibleSeconds);
const paperWidthMm = visibleSeconds * ECG_PAPER_SPEED_MM_PER_SEC;

const waveformGridWidth =
  gridSize.width > 0
    ? Math.max(1, gridSize.width - VITAL_RAIL_WIDTH_PX - GRID_GAP_PX * 2)
    : 720;

const tileWidthPx =
  gridSize.width > 0
    ? waveformGridWidth / WAVEFORM_COLUMN_COUNT
    : 360;

const tileHeightPx =
  gridSize.height > 0
    ? (gridSize.height - GRID_GAP_PX * 2) / WAVEFORM_ROW_COUNT
    : 160;

const pxPerMm = clamp(tileWidthPx / paperWidthMm, MIN_PX_PER_MM, MAX_PX_PER_MM);
const rowHeightPx = Math.max(1, tileHeightPx);

  useEffect(() => {
    if (!rowHeightPx || !pxPerMm) return;

    const nowMs =
      typeof performance !== "undefined"
        ? performance.now()
        : Date.now();

    setLeadAutoGains((previousGains) => {
      let changed = false;
      const nextGains = { ...previousGains };

      for (const lead of LEADS) {
        const viewport =
          leadViewportMetrics[lead.id] || {};

        const actualRowHeightPx =
          Number(viewport.heightPx) || rowHeightPx;

        const actualPxPerMm =
          Number(viewport.widthPx) > 0
            ? clamp(
                Number(viewport.widthPx) /
                  paperWidthMm,
                MIN_PX_PER_MM,
                MAX_PX_PER_MM
              )
            : pxPerMm;

        const currentGain =
          Number(previousGains[lead.id]) ||
          AUTO_GAIN_BOOTSTRAP;

        const controller =
          leadGainControllerRef.current[lead.id] ||
          (leadGainControllerRef.current[lead.id] = {
            initialized: false,
            holdUntil: 0,
            candidateGain: null,
            candidateSince: null,
            lastUpdatedAt: 0,
          });

        const nextGain = chooseLeadAutoGain({
          samples: leadWindows[lead.id] || [],
          currentGain,
          rowHeightPx: actualRowHeightPx,
          pxPerMm: actualPxPerMm,
          sampleRate,
          nowMs,
          controller,
        });

        nextGains[lead.id] = nextGain;

        if (Math.abs(nextGain - currentGain) >= 0.0001) {
          changed = true;
        }
      }

      return changed ? nextGains : previousGains;
    });
  }, [
    leadWindows,
    leadViewportMetrics,
    rowHeightPx,
    pxPerMm,
    paperWidthMm,
    sampleRate,
  ]);


  const leadTiles = useMemo(() => {
    return LEADS.map((lead) => {
      const samples = leadWindows[lead.id] || [];
      const stats = getLeadStats(
        samples,
        waveFrame.latestMv?.[lead.id]
      );

      const viewport =
        leadViewportMetrics[lead.id] || {};

      const actualRowHeightPx =
        Number(viewport.heightPx) || rowHeightPx;

      const actualPxPerMm =
        Number(viewport.widthPx) > 0
          ? clamp(
              Number(viewport.widthPx) /
                paperWidthMm,
              MIN_PX_PER_MM,
              MAX_PX_PER_MM
            )
          : pxPerMm;

      const autoGain =
        Number(leadAutoGains[lead.id]) ||
        AUTO_GAIN_BOOTSTRAP;

      // Render-time safety ceiling. React effects run after paint, so relying
      // only on the autoscale effect can expose one or more clipped frames
      // during an abrupt API Range -> episode transition. Recompute the hard
      // ceiling synchronously from the exact visible samples and exact canvas
      // geometry before drawing.
      const { hardSafeGain } =
        getHardSafeGain({
          samples,
          rowHeightPx: actualRowHeightPx,
          pxPerMm: actualPxPerMm,
        });

      const effectiveGain =
        floorSafeGain(
          Math.min(
            autoGain,
            hardSafeGain * AUTO_GAIN_SAFETY_PAD
          )
        );

      const scale = getDisplayScale({
        samples,
        gainMmPerMv: effectiveGain,
        rowHeightPx: actualRowHeightPx,
        pxPerMm: actualPxPerMm,
      });

      return {
        ...lead,
        latest: stats.latest,
        p2p: stats.p2p,
        gainMode: DEFAULT_GAIN_MODE,
        gainMmPerMv: effectiveGain,
        scale,
        displayPxPerMm: actualPxPerMm,
      };
    });
  }, [
    leadWindows,
    waveFrame.latestMv,
    leadAutoGains,
    leadViewportMetrics,
    rowHeightPx,
    pxPerMm,
    paperWidthMm,
  ]);

// const gainSummary = useMemo(() => {
//   return ECG_GAIN_OPTIONS.map((gain) => ({
//     gain,
//     count: leadTiles.filter((lead) => lead.gainMmPerMv === gain).length,
//   }));
// }, [leadTiles]);

  const episodePackPatient =
    injectionStatus?.episodePackPatient ||
    injectionStatus?.oracleDemo
      ?.episodePackPatient ||
    injectionStatus?.epicDemo
      ?.episodePackPatient ||
    oracleAutoDemo?.episodePack
      ?.patient ||
    epicAutoDemo?.episodePack
      ?.patient ||
    evaluationDemo?.episode
      ?.evaluationScenario
      ?.patient ||
    null;

  const displayedPatient =
    episodePackPatient ||
    (
      waveformSource ===
      "evaluation"
        ? evaluationDemo?.episode
            ?.patient
        : null
    ) ||
    patient;

const injectionWorkflowActive =
  waveformSource ===
    API_RANGE_EPISODE_SOURCE &&
  [
    "ARMED",
    "INJECTING",
    "POST_EVENT",
    "ANALYZING",
  ].includes(
    injectionStatus.state
  );

const completedInjectionEpisodeId =
  injectionStatus.state ===
    "COMPLETE"
    ? injectionStatus.episodeId ||
      null
    : null;


  return (
    <section className="wave7-page">
      <header className="wave7-header">
        <div className="wave7-patient-copy">
          <p className="wave7-eyebrow">
            Real-time telemetry
          </p>
          <h1>
            {displayedPatient?.display ||
              displayedPatient?.name ||
              "Selected patient"}
          </h1>

    
            {/* MRN{" "}
            {displayedPatient?.mrn ||
              displayedPatient?.id ||
              "--"}
            {" • "}
            Oracle SMART
            {" • "}
            physionet-incart */}
       
        </div>

        <div className="wave7-header-actions">
          <div
            className="wave7-source-toggle"
            aria-label="Waveform source"
          >
            {WAVEFORM_SOURCES.map(
              (source) => (
                <button
                  key={source.id}
                  type="button"
                  className={
                    waveformSource ===
                    source.id
                      ? "active"
                      : ""
                  }
                  disabled={
                    Boolean(
                      (oracleAutoDemo?.enabled && oracleAutoDemo?.status !== "error") ||
                      (epicAutoDemo?.enabled && epicAutoDemo?.status !== "error")
                    )
                  }
                  onClick={() =>
                    changeWaveformSource(
                      source.id
                    )
                  }
                >
                  {source.label}
                </button>
              )
            )}
          </div>

          {EVALUATION_ENABLED &&
          !(
            (oracleAutoDemo?.enabled && oracleAutoDemo?.status !== "error") ||
            (epicAutoDemo?.enabled && epicAutoDemo?.status !== "error")
          ) &&
          waveformSource ===
            API_RANGE_EPISODE_SOURCE && (
            <>
              <button
                type="button"
                className="wave7-action-btn"
                onClick={() =>
                  setInjectionControlsOpen(
                    (value) => !value
                  )
                }
              >
                {injectionStatus.state &&
                injectionStatus.state !==
                  "IDLE" &&
                ![
                  "COMPLETE",
                  "FAILED",
                  "CANCELLED",
                ].includes(
                  injectionStatus.state
                )
                  ? "Episode Armed"
                  : "Map Episode"}
              </button>

              {injectionControlsOpen && (
                <>
                  <select
                    className="wave7-action-btn"
                    aria-label="Injected evaluation scenario"
                    value={
                      injectionScenarioId
                    }
                    disabled={
                      injectionStatus.state &&
                      ![
                        "IDLE",
                        "COMPLETE",
                        "FAILED",
                        "CANCELLED",
                      ].includes(
                        injectionStatus.state
                      )
                    }
                    onChange={(event) =>
                      setInjectionScenarioId(
                        event.target.value
                      )
                    }
                  >
                    {injectionScenariosLoading && (
                      <option value="">
                        Loading scenarios...
                      </option>
                    )}

                    {!injectionScenariosLoading &&
                      injectionScenarios.map((scenario) => (
                        <option
                          key={scenario.scenarioId}
                          value={scenario.scenarioId}
                        >
                          {scenario.scenarioId}
                          {" — "}
                          {scenario.shortLabel || scenario.display}
                        </option>
                      ))}

                    {!injectionScenariosLoading &&
                      injectionScenarios.length === 0 && (
                        <option value="">
                          No scenarios available
                        </option>
                      )}
                  </select>

                  {injectionStatus.state ===
                  "ANALYZING" ? (
                    <button
                      type="button"
                      className="wave7-action-btn"
                      disabled
                    >
                      Analyzing...
                    </button>
                  ) : [
                      "ARMED",
                      "INJECTING",
                      "POST_EVENT",
                    ].includes(
                      injectionStatus.state
                    ) ? (
                    <button
                      type="button"
                      className="wave7-action-btn"
                      onClick={
                        cancelApiRangeEvaluation
                      }
                    >
                      Cancel Test
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="wave7-action-btn"
                      disabled={
                        injectionScenariosLoading ||
                        !injectionScenarioId
                      }
                      onClick={
                        armApiRangeEvaluation
                      }
                    >
                      Start Mapped Episode
                    </button>
                  )}
                </>
              )}
            </>
          )}

          {waveformSource ===
            "evaluation" && (
            <>
              <select
                className="wave7-action-btn"
                aria-label="Evaluation episode"
                value={
                  selectedEvaluationId
                }
                disabled={
                  evaluationLoading
                }
                onChange={(event) =>
                  loadEvaluationEpisode(
                    event.target.value
                  )
                }
              >
                <option value="">
                  {evaluationLoading
                    ? "Loading episodes..."
                    : "Choose episode"}
                </option>

                {evaluationEpisodes.map(
                  (item) => (
                    <option
                      key={
                        item.episodeId
                      }
                      value={
                        item.episodeId
                      }
                    >
                      {item.episodeId}
                      {" — "}
                      {item.display}
                    </option>
                  )
                )}
              </select>

              <button
                type="button"
                className="wave7-action-btn"
                disabled={
                  !evaluationDemo
                    ?.episode ||
                  evaluationLoading
                }
                onClick={
                  toggleEvaluationPlayback
                }
              >
                {evaluationPlaying
                  ? "Pause"
                  : "Play"}
              </button>
            </>
          )}

          <span
            className={`wave7-live-pill ${streamStatus}`}
            title={
              injectionError ||
              evaluationError ||
              undefined
            }
          >
            ●{" "}
            {waveformSource ===
              "evaluation"
              ? evaluationError
                ? "Evaluation Error"
                : evaluationLoading
                ? "Loading Case"
                : evaluationAnalysisStatus ===
                  "running"
                ? "Analysis Running"
                : evaluationAnalysisStatus ===
                  "checking"
                ? "Checking Analysis"
                : evaluationAnalysisStatus ===
                  "ready"
                ? "Analysis Ready"
                : evaluationDemo
                    ?.episode
                ? evaluationPlaying
                  ? "Evaluation Playing"
                  : "Evaluation Ready"
                : "Choose Episode"
              : injectionStatus.state ===
                "ARMED"
              ? `Armed • ${Number(
                  injectionStatus.remainingSeconds || 0
                ).toFixed(1)}s`
              : injectionStatus.state ===
                "INJECTING"
              ? `Injecting ${injectionStatus.scenarioDisplay || "evaluation waveform"}`
              : injectionStatus.state ===
                "POST_EVENT"
              ? `Post capture • ${Number(
                  injectionStatus.remainingSeconds || 0
                ).toFixed(1)}s`
              : injectionStatus.state ===
                "ANALYZING"
              ? "Analysis Running"
              : injectionStatus.state ===
                "COMPLETE"
              ? "Analysis Complete"
              : injectionStatus.state ===
                "FAILED"
              ? "Evaluation Failed"
              : streamStatus ===
                "live"
              ? "Live WebGL"
              : streamStatus ===
                "warning"
              ? "Waveform Warning"
              : "Connecting"}
          </span>

          <button
            type="button"
            className="wave7-action-btn"
            onClick={() => {
              if (
                waveformSource ===
                "evaluation"
              ) {
                onOpenAnalytics?.();
                return;
              }

              if (
                completedInjectionEpisodeId
              ) {
                onOpenAnalytics?.(
                  completedInjectionEpisodeId
                );
                return;
              }

              if (
                injectionWorkflowActive
              ) {
                return;
              }

              onOpenAnalytics?.(
                latestEpisodeId
              );
            }}
            disabled={
              (
                waveformSource ===
                  "evaluation" &&
                !evaluationDemo?.episode
              ) ||
              injectionWorkflowActive
            }
            title={
              injectionWorkflowActive
                ? (
                    "Evaluation capture or analysis is still running."
                  )
                : undefined
            }
          >
            {injectionWorkflowActive
              ? "Analytics Pending"
              : "Open Analytics"}
          </button>
        </div>
      </header>

      <main className="wave7-monitor-card">
        <div className="wave7-monitor-title">
          <div>
            {/* <p className="wave7-eyebrow">High precision</p> */}
            <h2>6 waveform ECG matrix</h2>
          </div>

          {/* <span>Calibrated auto gain</span> */}
        </div>

<section
  ref={gridRef}
  className="wave7-grid"
 style={{
  "--ecg-mm": `${pxPerMm}px`,
  "--ecg-major-box": `${pxPerMm * ECG_MINOR_BOXES_PER_MAJOR}px`,
  "--ecg-half-mm": `${pxPerMm / 2}px`,
  "--ecg-half-major-box": `${(pxPerMm * ECG_MINOR_BOXES_PER_MAJOR) / 2}px`,
  "--ecg-zero-y": "50%",
  "--wave7-grid-gap": `${GRID_GAP_PX}px`,
  "--wave7-vitals-width": `${VITAL_RAIL_WIDTH_PX}px`,
}}
>
 {leadTiles.map((lead) => (
  <WaveformTile
    key={`${waveformSource}-${lead.id}`}
    lead={lead}
    samples={leadWindows[lead.id] || []}
    visiblePoints={visiblePoints}
    pxPerMm={lead.displayPxPerMm || pxPerMm}
    onViewportMetrics={(metrics) =>
      updateLeadViewportMetrics(lead.id, metrics)
    }
  />
))}

  <BedsideVitalsPanel
    key={`${waveformSource}-vitals`}
    waveFrame={waveFrame}
    injectionStatus={injectionStatus}
    scenarioId={
      injectionStatus?.scenarioId ||
      oracleAutoDemo?.scenarioId ||
      epicAutoDemo?.scenarioId ||
      injectionScenarioId
    }
  />
</section>
      </main>
    </section>
  );
}

// function WaveformTile({
//   lead,
//   samples,
//   visiblePoints,
//   pxPerMm,
//   onGainModeChange,
// }) {
//   const { scale } = lead;

//   return (
//     <article
//   className={`wave7-wave-tile ${scale.clipRisk ? "clip-risk" : ""}`}
//   style={{ gridArea: lead.area }}
// >
//       <div className="wave7-calibrated-strip">
//         <YAxisScale scale={scale} />

//         <div className="wave7-zero-line" />

//         <WebGLWaveformCanvas
//           samples={samples}
//           points={visiblePoints}
//           color={lead.color}
//           mode="millivolts"
//           pxPerMm={pxPerMm}
//           voltageScaleMmPerMv={lead.gainMmPerMv}
//           centerMv={0}
//         />
//       </div>

//       <div className="wave7-tile-top">
//         <strong>{lead.label}</strong>
//         <span>
//           {lead.gainMode === "auto" ? "Auto" : "Manual"} {lead.gainMmPerMv}
//         </span>
//       </div>

//       <div className="wave7-lead-gain-toggle" aria-label={`${lead.label} gain selector`}>
//         <button
//           type="button"
//           className={lead.gainMode === "auto" ? "active" : ""}
//           onClick={() => onGainModeChange(lead.id, "auto")}
//         >
//           A
//         </button>

//         {ECG_GAIN_OPTIONS.map((gain) => (
//           <button
//             key={gain}
//             type="button"
//             className={lead.gainMode === gain ? "active" : ""}
//             onClick={() => onGainModeChange(lead.id, gain)}
//           >
//             {gain}
//           </button>
//         ))}
//       </div>

//       <div className="wave7-tile-bottom">
//         <strong>
//           {lead.latest}
//           <em>mV</em>
//         </strong>

//         <span>P-P {lead.p2p}</span>
//       </div>
//     </article>
//   );
// }

function WaveformTile({
  lead,
  samples,
  visiblePoints,
  pxPerMm,
  onViewportMetrics,
}) {
  const { scale } = lead;

  return (
    <article
      className={`wave7-wave-tile ${scale.clipRisk ? "clip-risk" : ""}`}
      style={{ gridArea: lead.area }}
    >
      <div className="wave7-calibrated-strip">
        <YAxisScale scale={scale} />

        <div className="wave7-zero-line" />

        <WebGLWaveformCanvas
          values={samples}
          points={visiblePoints}
          color={lead.color}
          mode="millivolts"
          pxPerMm={pxPerMm}
          voltageScaleMmPerMv={lead.gainMmPerMv}
          centerMv={0}
          rightAlignWindow
          onViewportMetrics={onViewportMetrics}
        />
      </div>

      <div className="wave7-tile-top">
        <strong>{lead.label}</strong>
        <span>Auto {formatGainValue(lead.gainMmPerMv)}</span>
      </div>

      <div className="wave7-tile-bottom">
        <strong>
          {lead.latest}
          <em>mV</em>
        </strong>

        <span>P-P {lead.p2p}</span>
      </div>
    </article>
  );
}

function YAxisScale({ scale }) {
  return (
    <div className={`wave7-y-scale ${scale.clipRisk ? "clip-risk" : ""}`}>
      <span>+{formatMvValue(scale.halfRangeMv)}</span>
      <span>0 mV</span>
      <span>-{formatMvValue(scale.halfRangeMv)}</span>
    </div>
  );
}

function formatVitalValue(value, decimals = 0) {
  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) return "--";

  return numericValue.toFixed(decimals);
}



function useRollingSeries(value, maxPoints = 28) {
  const [series, setSeries] = useState(() =>
    Array.from({ length: maxPoints }, () => Number(value) || 0)
  );

  useEffect(() => {
    const numericValue = Number(value);

    if (!Number.isFinite(numericValue)) return;

    setSeries((previous) => [
      ...previous.slice(-(maxPoints - 1)),
      numericValue,
    ]);
  }, [value, maxPoints]);

  return series;
}

function downsampleSeries(values, targetPoints = PPG_MINI_GRAPH_POINTS) {
  if (values.length <= targetPoints) return values;

  return Array.from({ length: targetPoints }, (_, index) => {
    const sourceIndex = Math.round(
      (index / (targetPoints - 1)) * (values.length - 1)
    );

    return values[sourceIndex];
  });
}



function smoothPpg(values, radius) {
  return values.map((_, index) => {
    const start = Math.max(0, index - radius);
    const end = Math.min(
      values.length,
      index + radius + 1
    );

    let total = 0;

    for (let current = start; current < end; current += 1) {
      total += values[current];
    }

    return total / Math.max(1, end - start);
  });
}

function getMovingPpgBeat(
  values,
  sampleRate = 220,
  heartRate = 72,
  source = ""
) {
  const cleanValues = values
    .map(Number)
    .filter(Number.isFinite);

  if (cleanValues.length < 8) {
    return cleanValues.length
      ? cleanValues
      : [0.5];
  }

  const safeSampleRate = Math.max(
    50,
    Number(sampleRate) || 220
  );

  const safeHeartRate = clamp(
    Number(heartRate) || 72,
    40,
    180
  );

  const beatLength = clamp(
    Math.round(
      safeSampleRate * (60 / safeHeartRate)
    ),
    Math.round(safeSampleRate * 0.45),
    Math.round(safeSampleRate * 1.4)
  );

  const isApiRange =
    source === "api-range" ||
    source === "api_range";

  if (!isApiRange) {
    const beat = cleanValues.slice(-beatLength);

    return downsampleSeries(
      smoothPpg(beat, 2),
      PPG_MINI_GRAPH_POINTS
    );
  }

  const analysisLength = Math.min(
    cleanValues.length,
    beatLength * 3
  );

  const recent = cleanValues.slice(-analysisLength);

  const firstPass = smoothPpg(
    recent,
    Math.max(
      4,
      Math.round(safeSampleRate * 0.045)
    )
  );

  const smoothed = smoothPpg(
    firstPass,
    Math.max(
      3,
      Math.round(safeSampleRate * 0.03)
    )
  );

  const edgeSpace = Math.max(
    4,
    Math.round(beatLength * 0.18)
  );

  let peakIndex = edgeSpace;

  for (
    let index = edgeSpace;
    index < smoothed.length - edgeSpace;
    index += 1
  ) {
    if (smoothed[index] > smoothed[peakIndex]) {
      peakIndex = index;
    }
  }

  const beforePeak = Math.round(
    beatLength * 0.28
  );

  const afterPeak = beatLength - beforePeak;

  let start = peakIndex - beforePeak;
  let end = peakIndex + afterPeak;

  if (start < 0) {
    end += Math.abs(start);
    start = 0;
  }

  if (end > smoothed.length) {
    start = Math.max(
      0,
      start - (end - smoothed.length)
    );

    end = smoothed.length;
  }

  const oneBeat = smoothed.slice(start, end);

  return downsampleSeries(
    oneBeat,
    PPG_MINI_GRAPH_POINTS
  );
}

const CONTINUOUS_PPG_TEMPLATE_POINTS = 96;
const CONTINUOUS_PPG_DRAW_POINTS = 150;
const CONTINUOUS_PPG_BEATS_VISIBLE = 2.15;

const PPG_TEMPLATE_MORPH_SECONDS = 0.72;
const PPG_RATE_MORPH_SECONDS = 0.65;
const PPG_MIN_HEART_RATE = 40;
const PPG_MAX_HEART_RATE = 190;

const DEFAULT_CONTINUOUS_PPG_TEMPLATE =
  Array.from(
    {
      length:
        CONTINUOUS_PPG_TEMPLATE_POINTS,
    },
    (_, index) => {
      const x =
        index /
        Math.max(
          1,
          CONTINUOUS_PPG_TEMPLATE_POINTS -
            1
        );

      const systolic =
        Math.exp(
          -Math.pow(
            (x - 0.24) / 0.075,
            2
          )
        );

      const shoulder =
        0.18 *
        Math.exp(
          -Math.pow(
            (x - 0.39) / 0.09,
            2
          )
        );

      const notch =
        0.12 *
        Math.exp(
          -Math.pow(
            (x - 0.50) / 0.048,
            2
          )
        );

      const dicrotic =
        0.31 *
        Math.exp(
          -Math.pow(
            (x - 0.64) / 0.11,
            2
          )
        );

      return clamp(
        systolic +
        shoulder +
        dicrotic -
        notch,
        0,
        1
      );
    }
  );

function cleanContinuousPpgSeries(
  values
) {
  return (
    Array.isArray(values)
      ? values
      : []
  )
    .map(Number)
    .filter(Number.isFinite);
}

function resampleContinuousPpg(
  values,
  pointCount =
    CONTINUOUS_PPG_TEMPLATE_POINTS
) {
  const clean =
    cleanContinuousPpgSeries(
      values
    );

  if (!clean.length) {
    return [
      ...DEFAULT_CONTINUOUS_PPG_TEMPLATE,
    ];
  }

  if (clean.length === 1) {
    return Array.from(
      {
        length: pointCount,
      },
      () => clean[0]
    );
  }

  return Array.from(
    {
      length: pointCount,
    },
    (_, targetIndex) => {
      const position =
        (
          targetIndex /
          Math.max(
            1,
            pointCount - 1
          )
        ) *
        (clean.length - 1);

      const lowerIndex =
        Math.floor(
          position
        );

      const upperIndex =
        Math.min(
          clean.length - 1,
          lowerIndex + 1
        );

      const fraction =
        position -
        lowerIndex;

      return (
        clean[lowerIndex] *
          (1 - fraction) +
        clean[upperIndex] *
          fraction
      );
    }
  );
}

function normalizeContinuousPpgTemplate(
  values
) {
  const resampled =
    resampleContinuousPpg(
      smoothPpg(
        cleanContinuousPpgSeries(
          values
        ),
        1
      )
    );

  const lower =
    percentile(
      resampled,
      4
    );

  const upper =
    percentile(
      resampled,
      96
    );

  const range =
    upper - lower;

  if (
    !Number.isFinite(range) ||
    range < 0.00001
  ) {
    return [
      ...DEFAULT_CONTINUOUS_PPG_TEMPLATE,
    ];
  }

  return resampled.map(
    (value) =>
      clamp(
        (value - lower) /
          range,
        0,
        1
      )
  );
}

function rotateContinuousPpgTemplate(
  values,
  shift
) {
  const length =
    values.length;

  if (!length) {
    return [];
  }

  return values.map(
    (_, index) => {
      const sourceIndex =
        (
          index -
          shift +
          length
        ) %
        length;

      return values[
        sourceIndex
      ];
    }
  );
}

function continuousPpgTemplateDistance(
  left,
  right
) {
  const length =
    Math.min(
      left.length,
      right.length
    );

  if (!length) {
    return Infinity;
  }

  let total = 0;

  for (
    let index = 0;
    index < length;
    index += 1
  ) {
    const difference =
      left[index] -
      right[index];

    total +=
      difference *
      difference;
  }

  return total / length;
}

function alignContinuousPpgTemplate(
  previous,
  candidate
) {
  if (
    !previous?.length ||
    previous.length !==
      candidate.length
  ) {
    return candidate;
  }

  const maximumShift =
    Math.max(
      4,
      Math.round(
        candidate.length *
          0.18
      )
    );

  let best =
    candidate;

  let bestDistance =
    continuousPpgTemplateDistance(
      previous,
      candidate
    );

  for (
    let shift =
      -maximumShift;
    shift <= maximumShift;
    shift += 1
  ) {
    if (shift === 0) {
      continue;
    }

    const shifted =
      rotateContinuousPpgTemplate(
        candidate,
        shift
      );

    const distance =
      continuousPpgTemplateDistance(
        previous,
        shifted
      );

    if (
      distance <
      bestDistance
    ) {
      best =
        shifted;

      bestDistance =
        distance;
    }
  }

  return best;
}

function sampleContinuousPpgTemplate(
  template,
  phase
) {
  const length =
    template.length;

  if (!length) {
    return 0;
  }

  const wrapped =
    (
      phase % 1 +
      1
    ) % 1;

  const position =
    wrapped *
    length;

  const lowerIndex =
    Math.floor(
      position
    ) % length;

  const upperIndex =
    (
      lowerIndex + 1
    ) % length;

  const fraction =
    position -
    Math.floor(
      position
    );

  return (
    template[lowerIndex] *
      (1 - fraction) +
    template[upperIndex] *
      fraction
  );
}

function validContinuousPpgHeartRate(
  value
) {
  const numeric =
    Number(value);

  if (
    !Number.isFinite(numeric) ||
    numeric <= 0
  ) {
    return null;
  }

  return clamp(
    numeric,
    PPG_MIN_HEART_RATE,
    PPG_MAX_HEART_RATE
  );
}

function MiniPpgWaveform({
  series,
  sampleRate = 220,
  heartRate = 72,
  source = "",
}) {
  const canvasRef =
    useRef(null);

  const sizeRef =
    useRef({
      width: 86,
      height: 42,
      pixelRatio: 1,
    });

  const displayedTemplateRef =
    useRef([
      ...DEFAULT_CONTINUOUS_PPG_TEMPLATE,
    ]);

  const targetTemplateRef =
    useRef([
      ...DEFAULT_CONTINUOUS_PPG_TEMPLATE,
    ]);

  const phaseRef =
    useRef(0);

  const smoothedHeartRateRef =
    useRef(
      validContinuousPpgHeartRate(
        heartRate
      ) || 72
    );

  const requestedHeartRateRef =
    useRef(
      validContinuousPpgHeartRate(
        heartRate
      )
    );

  const latestValidSeriesRef =
    useRef([]);

  const candidateTemplate =
    useMemo(() => {
      const clean =
        cleanContinuousPpgSeries(
          series
        );

      /*
       * A missing trace is not a reset signal.
       * During controlled injection we keep the
       * previous real pulse moving until a new
       * post-capture trace arrives.
       */
      if (clean.length < 8) {
        return null;
      }

      latestValidSeriesRef.current =
        clean;

      const extracted =
        getMovingPpgBeat(
          clean,
          sampleRate,
          heartRate,
          source
        );

      return normalizeContinuousPpgTemplate(
        extracted
      );
    }, [
      series,
      sampleRate,
      heartRate,
      source,
    ]);

  useEffect(() => {
    const requested =
      validContinuousPpgHeartRate(
        heartRate
      );

    if (requested !== null) {
      requestedHeartRateRef.current =
        requested;
    }
  }, [heartRate]);

  useEffect(() => {
    if (!candidateTemplate) {
      return;
    }

    const aligned =
      alignContinuousPpgTemplate(
        displayedTemplateRef.current,
        candidateTemplate
      );

    /*
     * Do not replace the target in one frame.
     * This first dampening layer prevents a new
     * API-range pulse selection from looking like
     * a sudden morphology jump.
     */
    const previousTarget =
      targetTemplateRef.current;

    targetTemplateRef.current =
      aligned.map(
        (value, index) => {
          const previous =
            previousTarget[
              index
            ] ??
            value;

          return (
            previous *
              0.62 +
            value *
              0.38
          );
        }
      );
  }, [candidateTemplate]);

  useEffect(() => {
    const canvas =
      canvasRef.current;

    if (!canvas) {
      return undefined;
    }

    const context =
      canvas.getContext(
        "2d",
        {
          alpha: true,
          desynchronized: true,
        }
      );

    if (!context) {
      return undefined;
    }

    let mounted = true;
    let animationFrame = 0;
    let previousTimestamp = 0;

    function resizeCanvas() {
      const rect =
        canvas.getBoundingClientRect();

      const width =
        Math.max(
          1,
          rect.width || 86
        );

      const height =
        Math.max(
          1,
          rect.height || 42
        );

      const pixelRatio =
        Math.min(
          2,
          Math.max(
            1,
            window.devicePixelRatio ||
              1
          )
        );

      const canvasWidth =
        Math.round(
          width *
          pixelRatio
        );

      const canvasHeight =
        Math.round(
          height *
          pixelRatio
        );

      if (
        canvas.width !==
          canvasWidth ||
        canvas.height !==
          canvasHeight
      ) {
        canvas.width =
          canvasWidth;

        canvas.height =
          canvasHeight;
      }

      sizeRef.current = {
        width,
        height,
        pixelRatio,
      };
    }

    resizeCanvas();

    const resizeObserver =
      typeof ResizeObserver !==
      "undefined"
        ? new ResizeObserver(
            resizeCanvas
          )
        : null;

    resizeObserver?.observe(
      canvas
    );

    function draw(timestamp) {
      if (!mounted) {
        return;
      }

      if (!previousTimestamp) {
        previousTimestamp =
          timestamp;
      }

      const elapsedSeconds =
        Math.min(
          0.05,
          Math.max(
            0,
            (
              timestamp -
              previousTimestamp
            ) /
              1000
          )
        );

      previousTimestamp =
        timestamp;

      const requestedRate =
        requestedHeartRateRef.current ||
        smoothedHeartRateRef.current ||
        72;

      const rateAlpha =
        1 -
        Math.exp(
          -elapsedSeconds /
            PPG_RATE_MORPH_SECONDS
        );

      smoothedHeartRateRef.current +=
        (
          requestedRate -
          smoothedHeartRateRef.current
        ) *
        rateAlpha;

      phaseRef.current =
        (
          phaseRef.current +
          elapsedSeconds *
            (
              smoothedHeartRateRef.current /
              60
            )
        ) % 1;

      const templateAlpha =
        1 -
        Math.exp(
          -elapsedSeconds /
            PPG_TEMPLATE_MORPH_SECONDS
        );

      displayedTemplateRef.current =
        displayedTemplateRef.current.map(
          (value, index) => {
            const target =
              targetTemplateRef.current[
                index
              ] ??
              value;

            return (
              value +
              (
                target -
                value
              ) *
                templateAlpha
            );
          }
        );

      const {
        width,
        height,
        pixelRatio,
      } = sizeRef.current;

      context.setTransform(
        pixelRatio,
        0,
        0,
        pixelRatio,
        0,
        0
      );

      context.clearRect(
        0,
        0,
        width,
        height
      );

      const horizontalPadding = 2;
      const verticalPadding = 4;
      const drawableWidth =
        Math.max(
          1,
          width -
          horizontalPadding *
            2
        );

      const drawableHeight =
        Math.max(
          1,
          height -
          verticalPadding *
            2
        );

      const points = [];

      for (
        let index = 0;
        index <
        CONTINUOUS_PPG_DRAW_POINTS;
        index += 1
      ) {
        const fraction =
          index /
          Math.max(
            1,
            CONTINUOUS_PPG_DRAW_POINTS -
              1
          );

        const x =
          horizontalPadding +
          fraction *
            drawableWidth;

        /*
         * The animation clock is never reset by
         * pre/event/post state changes. Increasing
         * phase makes the trace continuously scroll
         * from right to left.
         */
        const templatePhase =
          phaseRef.current +
          fraction *
            CONTINUOUS_PPG_BEATS_VISIBLE;

        const value =
          sampleContinuousPpgTemplate(
            displayedTemplateRef.current,
            templatePhase
          );

        const y =
          verticalPadding +
          (
            1 -
            clamp(
              value,
              0,
              1
            )
          ) *
            drawableHeight;

        points.push({
          x,
          y,
        });
      }

      const gradient =
        context.createLinearGradient(
          0,
          0,
          width,
          0
        );

      gradient.addColorStop(
        0,
        "rgba(56, 189, 248, 0.38)"
      );

      gradient.addColorStop(
        0.72,
        "rgba(125, 211, 252, 0.88)"
      );

      gradient.addColorStop(
        1,
        "rgba(224, 247, 255, 1)"
      );

      context.save();

      context.beginPath();

      points.forEach(
        (point, index) => {
          if (index === 0) {
            context.moveTo(
              point.x,
              point.y
            );
          } else {
            context.lineTo(
              point.x,
              point.y
            );
          }
        }
      );

      context.lineWidth = 4;
      context.strokeStyle =
        "rgba(56, 189, 248, 0.18)";

      context.shadowColor =
        "rgba(56, 189, 248, 0.42)";

      context.shadowBlur = 5;
      context.stroke();
      context.restore();

      context.beginPath();

      points.forEach(
        (point, index) => {
          if (index === 0) {
            context.moveTo(
              point.x,
              point.y
            );
          } else {
            context.lineTo(
              point.x,
              point.y
            );
          }
        }
      );

      context.lineWidth = 1.8;
      context.lineCap = "round";
      context.lineJoin = "round";
      context.strokeStyle =
        gradient;

      context.stroke();

      /*
       * A short bright tail moves with the actual
       * waveform. It is not a static CSS dash and
       * does not jump to a newly selected endpoint.
       */
      const tailLength =
        Math.max(
          10,
          Math.round(
            CONTINUOUS_PPG_DRAW_POINTS *
              0.16
          )
        );

      const tailStart =
        Math.max(
          0,
          points.length -
          tailLength
        );

      context.beginPath();

      for (
        let index = tailStart;
        index < points.length;
        index += 1
      ) {
        const point =
          points[index];

        if (index === tailStart) {
          context.moveTo(
            point.x,
            point.y
          );
        } else {
          context.lineTo(
            point.x,
            point.y
          );
        }
      }

      context.lineWidth = 2.35;
      context.strokeStyle =
        "rgba(224, 247, 255, 0.96)";

      context.shadowColor =
        "rgba(125, 211, 252, 0.74)";

      context.shadowBlur = 4;
      context.stroke();

      context.shadowBlur = 0;

      animationFrame =
        requestAnimationFrame(
          draw
        );
    }

    animationFrame =
      requestAnimationFrame(
        draw
      );

    return () => {
      mounted = false;

      resizeObserver?.disconnect();

      cancelAnimationFrame(
        animationFrame
      );
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="wave7-spo2-reference-graph wave7-spo2-continuous-canvas"
      aria-hidden="true"
    />
  );
}


function useStablePresentationSpo2(
  value
) {
  const stableStateRef =
    useRef(null);

  const [displayed, setDisplayed] =
    useState(() => {
      const initial =
        nextStableSpo2State(
          null,
          value,
          Date.now()
        );

      stableStateRef.current =
        initial;

      return initial.displayed;
    });

  useEffect(() => {
    const next =
      nextStableSpo2State(
        stableStateRef.current,
        value,
        Date.now()
      );

    stableStateRef.current =
      next;

    setDisplayed((previous) =>
      previous === next.displayed
        ? previous
        : next.displayed
    );
  }, [value]);

  return displayed;
}


function finiteBedsideValue(
  value
) {
  const parsed =
    Number(value);

  return Number.isFinite(parsed)
    ? parsed
    : null;
}

function formatResolvedVital(
  value,
  decimals = 0,
  invalidWhenNonPositive = false
) {
  const parsed =
    finiteBedsideValue(
      value
    );

  if (
    parsed === null ||
    (
      invalidWhenNonPositive &&
      parsed <= 0
    )
  ) {
    return "--";
  }

  return parsed.toFixed(
    decimals
  );
}

function BedsideVitalsPanel({
  waveFrame,
  injectionStatus,
  scenarioId,
}) {
  const rawVitals =
    waveFrame?.vitals || {};

  const resolved =
    resolveBedsideWidgetValues({
      enabled:
        Boolean(scenarioId),
      scenarioId,
      injectionState:
        injectionStatus?.state,
      vitals: rawVitals,
    });

  const stableSpo2 =
    useStablePresentationSpo2(
      resolved.spo2
    );

  /*
   * Do not turn the numeric SpO₂ history into a
   * waveform. The canvas keeps the last learned
   * real PPG pulse moving through short or long
   * capture-boundary gaps.
   */
  const ppgSeries =
    Array.isArray(
      rawVitals.ppgTrace
    )
      ? rawVitals.ppgTrace
      : [];

  const systolic =
    finiteBedsideValue(
      resolved.systolic
    );

  const diastolic =
    finiteBedsideValue(
      resolved.diastolic
    );

  const bloodPressure =
    systolic !== null &&
    diastolic !== null &&
    systolic > 0 &&
    diastolic > 0
      ? `${Math.round(
          systolic
        )}/${Math.round(
          diastolic
        )}`
      : "--/--";

  const signalLabel =
    waveFrame?.status ===
    "connected"
      ? "Live signal"
      : scenarioId
      ? "Scenario signal"
      : "Waiting";

  return (
    <aside className="wave7-vitals-panel wave7-reference-vitals">
      <div className="wave7-vitals-header">
        <p className="wave7-eyebrow">
          Bedside widgets
        </p>

        <span>
          {signalLabel}
        </span>
      </div>

      <ReferenceVitalCard
        className="hr"
        label="HR"
        value={formatResolvedVital(
          resolved.heartRate,
          0,
          true
        )}
        unit=""
      />

      <ReferenceVitalCard
        className="spo2"
        label="SpO₂"
        value={formatResolvedVital(
          stableSpo2,
          0,
          true
        )}
        unit=""
        graph={
          <MiniPpgWaveform
            series={ppgSeries}
            sampleRate={
              waveFrame?.sampleRate ||
              220
            }
            heartRate={
              resolved.heartRate ||
              72
            }
            source={
              waveFrame?.source ||
              ""
            }
          />
        }
      />

      <ReferenceVitalCard
        className="bp"
        label="NIBP"
        value={bloodPressure}
        unit=""
      />

      <ReferenceVitalCard
        className="rr"
        label="RR"
        value={formatResolvedVital(
          resolved.respiratoryRate,
          0,
          true
        )}
        unit=""
      />

      <ReferenceVitalCard
        className="temp"
        label="Temp"
        value={formatResolvedVital(
          resolved.temperature,
          1
        )}
        unit=""
      />
    </aside>
  );
}

function ReferenceVitalCard({ label, value, unit, graph, className = "" }) {
  return (
    <article className={`wave7-reference-vital-card ${className}`}>
      <div className="wave7-reference-vital-title">
        <span>{label}</span>
      </div>

      <div className="wave7-reference-vital-main">
        <div className="wave7-reference-vital-value">
          <strong>{value}</strong>
          <em>{unit}</em>
        </div>

        {graph && (
          <div className="wave7-reference-graph-box">
            {graph}
          </div>
        )}
      </div>
    </article>
  );
}

function CompactVitalCard({ label, value, unit, graph, className = "" }) {
  return (
    <div className={`wave7-vital-card wave7-vital-compact ${className}`}>
      <div className="wave7-compact-vital-top">
        <span>{label}</span>
      </div>

      <div className="wave7-compact-vital-body">
        <div className="wave7-compact-vital-value">
          <strong>{value}</strong>
          <em>{unit}</em>
        </div>

        {graph && (
          <div className="wave7-compact-vital-graph">
            {graph}
          </div>
        )}
      </div>
    </div>
  );
}