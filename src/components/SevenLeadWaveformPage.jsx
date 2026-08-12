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
} from "../evaluation/oracleEvaluationDemo";
import {
  startEpicEvaluationDemo,
} from "../evaluation/epicEvaluationDemo";
import {
  nextStableSpo2State,
  resolveBedsideWidgetValues,
} from "../presentation/episodeWidgetFallbacks";

const ECG_PAPER_SPEED_MM_PER_SEC = 25;
const DEFAULT_VISIBLE_SECONDS = 3;
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
  Calibrated display gain ladder.

  This does not alter ECG data or morphology.
  It only changes how many millimeters represent 1 mV.
  The active gain is always shown as Auto 2.5 / 5 / 10 / 20 / 40.
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

const AUTO_HEADROOM = 0.9;
const AUTO_TARGET_P2P_FILL = 0.46;
const AUTO_KEEP_MIN_P2P_FILL = 0.34;
const AUTO_KEEP_MAX_P2P_FILL = 0.64;

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
  const visibleSeconds =
    Number(frame.xAxis?.secondsVisible) || DEFAULT_VISIBLE_SECONDS;

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
      hardAbsMv: 0,
    };
  }

  const minMv = Math.min(...values);
  const maxMv = Math.max(...values);
  const p2pMv = maxMv - minMv;

  const p05 = percentile(values, 5);
  const p95 = percentile(values, 95);
  const robustP2pMv = Math.max(0, p95 - p05);

  const absFromZero = values.map((value) => Math.abs(value - ECG_ZERO_MV));

  /*
    displayP2pMv is used only for gain choice.
    It avoids one noisy sample dominating the visual gain decision,
    but it still respects the real peak-to-peak signal.
  */
  const displayP2pMv = Math.max(robustP2pMv, p2pMv * 0.65);

  return {
    minMv,
    maxMv,
    p2pMv,
    robustP2pMv,
    displayP2pMv,
    robustAbsMv: percentile(absFromZero, 95),
    hardAbsMv: Math.max(...absFromZero),
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

function chooseAutoGain({ samples, currentGain, rowHeightPx, pxPerMm }) {
  const values = samples.map(Number).filter(Number.isFinite);

  if (!values.length || !rowHeightPx || !pxPerMm) {
    return currentGain || DEFAULT_GAIN_MM_PER_MV;
  }

  const amplitude = getLeadAmplitude(values);
  const rowHeightMm = rowHeightPx / pxPerMm;
  const current = currentGain || DEFAULT_GAIN_MM_PER_MV;

  if (!rowHeightMm || amplitude.hardAbsMv < 0.005) {
    return ECG_GAIN_OPTIONS[ECG_GAIN_OPTIONS.length - 1];
  }

  const viableGains = ECG_GAIN_OPTIONS.filter((gain) =>
    peakFitsAtGain({
      hardAbsMv: amplitude.hardAbsMv,
      gainMmPerMv: gain,
      rowHeightPx,
      pxPerMm,
    })
  );

  if (!viableGains.length) {
    return ECG_GAIN_OPTIONS[0];
  }

  const currentStillFits = viableGains.includes(current);
  const currentP2pFill = getP2pFillRatio({
    p2pMv: amplitude.displayP2pMv,
    gainMmPerMv: current,
    rowHeightPx,
    pxPerMm,
  });

  /*
    Hysteresis:
    Keep the current gain if it is safe and already visually acceptable.
    This prevents rapid gain flicker.
  */
  if (
    currentStillFits &&
    currentP2pFill >= AUTO_KEEP_MIN_P2P_FILL &&
    currentP2pFill <= AUTO_KEEP_MAX_P2P_FILL
  ) {
    return current;
  }

  const idealGain =
    (AUTO_TARGET_P2P_FILL * rowHeightMm) /
    Math.max(amplitude.displayP2pMv, 0.01);

  return getClosestGain(viableGains, idealGain);
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
    Object.fromEntries(LEADS.map((lead) => [lead.id, DEFAULT_GAIN_MM_PER_MV]))
  );

  const [gridSize, setGridSize] = useState({
    width: 0,
    height: 0,
  });

  const gridRef = useRef(null);

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
          DEFAULT_GAIN_MM_PER_MV,
        ])
      )
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
        DEFAULT_GAIN_MM_PER_MV,
      ])
    )
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

const visibleSeconds =
  Number(waveFrame.xAxis?.secondsVisible) || DEFAULT_VISIBLE_SECONDS;

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

    setLeadAutoGains((previousGains) => {
      const nextGains = { ...previousGains };
      let changed = false;

      for (const lead of LEADS) {
        const nextGain = chooseAutoGain({
          samples: leadWindows[lead.id] || [],
          currentGain: previousGains[lead.id] || DEFAULT_GAIN_MM_PER_MV,
          rowHeightPx,
          pxPerMm,
        });

        if (nextGains[lead.id] !== nextGain) {
          nextGains[lead.id] = nextGain;
          changed = true;
        }
      }

      return changed ? nextGains : previousGains;
    });
  }, [leadWindows, rowHeightPx, pxPerMm]);

  const leadTiles = useMemo(() => {
    return LEADS.map((lead) => {
      const samples = leadWindows[lead.id] || [];
      const stats = getLeadStats(samples, waveFrame.latestMv?.[lead.id]);

      // const leadGainMode = leadGainModes[lead.id] || DEFAULT_GAIN_MODE;
      // const autoGain = leadAutoGains[lead.id] || DEFAULT_GAIN_MM_PER_MV;

      // const effectiveGain =
      //   leadGainMode === "auto"
      //     ? autoGain
      //     : Number(leadGainMode) || DEFAULT_GAIN_MM_PER_MV;

      const autoGain = leadAutoGains[lead.id] || DEFAULT_GAIN_MM_PER_MV;
const effectiveGain = autoGain;

      const scale = getDisplayScale({
        samples,
        gainMmPerMv: effectiveGain,
        rowHeightPx,
        pxPerMm,
      });

      return {
        ...lead,
        latest: stats.latest,
        p2p: stats.p2p,
        // gainMode: leadGainMode,
        // gainMmPerMv: effectiveGain,
        gainMode: DEFAULT_GAIN_MODE,
gainMmPerMv: effectiveGain,
        scale,
      };
    });
  }, [
    leadWindows,
    waveFrame.latestMv,
    // leadGainModes,
    leadAutoGains,
    rowHeightPx,
    pxPerMm,
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
    samples={waveFrame.leadsMv?.[lead.id] || []}
    visiblePoints={visiblePoints}
    pxPerMm={pxPerMm}
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
          samples={samples}
          points={visiblePoints}
          color={lead.color}
          mode="millivolts"
          pxPerMm={pxPerMm}
          voltageScaleMmPerMv={lead.gainMmPerMv}
          centerMv={0}
        />
      </div>

      <div className="wave7-tile-top">
        <strong>{lead.label}</strong>
        <span>Auto {lead.gainMmPerMv}</span>
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