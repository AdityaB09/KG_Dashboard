import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { patients as mockPatients, medications as mockMedications, labs as mockLabs, documents as mockDocuments, recentSearches } from "./data/mockData";
import { fetchFirelyPatientClinicalData, fetchFirelyPatients, testFirelyConnection } from "./services/fhirService";

import Navbar from "./components/Navbar";
import PatientSidebar from "./components/PatientSidebar";
import PatientSummary from "./components/PatientSummary";
import ECGPanel from "./components/ECGPanel";
import WidgetCard from "./components/WidgetCard";
import OverlayModal from "./components/OverlayModal";
import SearchOverlay from "./components/SearchOverlay";
import AlertPanel from "./components/AlertPanel";
import TimelineFeed from "./components/TimelineFeed";
import MultiPatientMonitor from "./components/MultiPatientMonitor";
import ClinicalPhysiologyPage from "./components/ClinicalPhysiologyPage";
import SevenLeadWaveformPage from "./components/SevenLeadWaveformPage";
import AnalyticsIncidentNotice from "./components/AnalyticsIncidentNotice";
import {
  connectEpisodeEvents,
} from "./services/episodeService";
import {
  getOracleEvaluationBootstrap,
} from "./evaluation/oracleEvaluationDemo";
import {
  getEpicEvaluationBootstrap,
} from "./evaluation/epicEvaluationDemo";
import "./index.css";

const SMART_ANALYTICS_NOTICE_DELAY_AFTER_POST_MS = Math.max(
  0,
  Number(
    import.meta.env
      .VITE_SMART_ANALYTICS_NOTICE_DELAY_AFTER_POST_MS
  ) || 3000
);
const SMART_ANALYTICS_NOTICE_VISIBLE_MS = Math.max(
  800,
  Number(
    import.meta.env
      .VITE_SMART_ANALYTICS_NOTICE_VISIBLE_MS
  ) || 2000
);

import { createInitialTelemetry, nextTelemetryFrame } from "./services/telemetryService";
import { formatCurrentTime, getVitalAlerts } from "./utils/clinicalEvents";

function normalizeDashboardPatient(
  value,
  fallback = {}
) {
  const patient =
    value && typeof value === "object"
      ? value
      : {};

  const backup =
    fallback &&
    typeof fallback === "object"
      ? fallback
      : {};

  const name = String(
    patient.name ||
      patient.displayName ||
      backup.name ||
      backup.displayName ||
      "Evaluation Patient"
  ).trim();

  const generatedId =
    `evaluation-${name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") ||
      "patient"}`;

  const id = String(
    patient.id ||
      patient.patientId ||
      patient.mrn ||
      backup.id ||
      backup.patientId ||
      backup.mrn ||
      generatedId
  );

  const sex = String(
    patient.sex ||
      patient.gender ||
      backup.sex ||
      backup.gender ||
      "Unknown"
  )
    .trim()
    .toUpperCase();

  const initials =
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) =>
        part.charAt(0).toUpperCase()
      )
      .join("") || "EP";

  return {
    ...backup,
    ...patient,
    id,
    name,
    mrn: String(
      patient.mrn ||
        patient.medicalRecordNumber ||
        backup.mrn ||
        backup.medicalRecordNumber ||
        id
    ),
    unit: String(
      patient.unit ||
        patient.location ||
        backup.unit ||
        backup.location ||
        "Evaluation"
    ),
    age:
      patient.age ??
      backup.age ??
      "--",
    sex,
    avatar:
      patient.avatar ||
      backup.avatar ||
      initials,
    lastSeen:
      patient.lastSeen ||
      backup.lastSeen ||
      "Evaluation episode",
    risk:
      patient.risk ||
      backup.risk ||
      "high",
  };
}

export default function App() {
  const [patients, setPatients] = useState(mockPatients);
  const [medications, setMedications] = useState(mockMedications);
  const [labs, setLabs] = useState(mockLabs);
  const [documents, setDocuments] = useState(mockDocuments);
  const [fhirEnabled, setFhirEnabled] = useState(false);
  const [fhirStatus, setFhirStatus] = useState("Using mock data");
  const [fhirLoading, setFhirLoading] = useState(false);
  const [oracleAutoDemo, setOracleAutoDemo] = useState({
    enabled: false,
    status: "idle",
    patient: null,
    scenario: null,
    episodePack: null,
    contextMode: "episode_pack_only",
    encounterId: null,
    existingRun: null,
    error: null,
  });
  const [epicAutoDemo, setEpicAutoDemo] = useState({
    enabled: false,
    status: "idle",
    patient: null,
    scenario: null,
    episodePack: null,
    contextMode: "episode_pack_only",
    encounterId: null,
    existingRun: null,
    error: null,
  });
const [
  evaluationDemo,
  setEvaluationDemo,
] = useState({
  active: false,
  episodeId: null,
  episode: null,
  run: null,
  capture: null,
});
  const [selectedPatientId, setSelectedPatientId] = useState(mockPatients[0].id);
  const [modal, setModal] = useState(null);
  const [globalSearchOpen, setGlobalSearchOpen] = useState(false);
  const [compactMode, setCompactMode] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true);
  const [globalSearchQuery, setGlobalSearchQuery] = useState("");
  const [darkMode, setDarkMode] = useState(false);
  const [timelineEvents, setTimelineEvents] = useState([]);

  const searchRef = useRef(null);
  const lastAlertSignatureRef = useRef("");
  const [multiMonitorOpen, setMultiMonitorOpen] = useState(false);
  
  const [activePage, setActivePage] = useState("main"); // "dashboard", "monitor", "physiology" 
  const [
  selectedEpisodeId,
  setSelectedEpisodeId,
] = useState(null);
const [
  selectedIncidentId,
  setSelectedIncidentId,
] = useState(null);

const [
  analyticsNotice,
  setAnalyticsNotice,
] = useState(null);

const postCaptureCompletedAtRef = useRef(0);
const pendingSmartAnalyticsTimerRef = useRef(null);
const pendingSmartAnalyticsKeyRef = useRef("");

const clearPendingSmartAnalyticsNotice = useCallback(() => {
  if (pendingSmartAnalyticsTimerRef.current != null) {
    window.clearTimeout(pendingSmartAnalyticsTimerRef.current);
    pendingSmartAnalyticsTimerRef.current = null;
  }
  pendingSmartAnalyticsKeyRef.current = "";
}, []);

const queueSmartAnalyticsNotice = useCallback((notice) => {
  if (!notice) return;

  const noticeKey = [
    notice?.episodeId || "",
    notice?.incidentId || "",
    notice?.scenarioId || "",
  ].join("|");

  if (
    noticeKey &&
    pendingSmartAnalyticsKeyRef.current === noticeKey
  ) {
    return;
  }

  if (pendingSmartAnalyticsTimerRef.current != null) {
    window.clearTimeout(pendingSmartAnalyticsTimerRef.current);
  }

  pendingSmartAnalyticsKeyRef.current = noticeKey;

  // evaluation.injection.captured is emitted immediately after the 6-second
  // post-capture window has been persisted. The ready notification is held
  // until three seconds after that moment by default. If the captured SSE event was
  // missed, fall back to the same delay from COMPLETE/status recovery.
  const postCaptureAt =
    postCaptureCompletedAtRef.current > 0
      ? postCaptureCompletedAtRef.current
      : Date.now();

  const elapsedSincePostCapture = Math.max(
    0,
    Date.now() - postCaptureAt
  );

  const delayMs = Math.max(
    0,
    SMART_ANALYTICS_NOTICE_DELAY_AFTER_POST_MS -
      elapsedSincePostCapture
  );

  pendingSmartAnalyticsTimerRef.current =
    window.setTimeout(() => {
      pendingSmartAnalyticsTimerRef.current = null;
      setAnalyticsNotice(notice);
    }, delayMs);
}, []);

useEffect(() => {
  return () => clearPendingSmartAnalyticsNotice();
}, [clearPendingSmartAnalyticsNotice]);

const evaluationInjectionActiveRef =
  useRef(false);

const evaluationInjectionIncidentRef =
  useRef(null);
  const [monitorPatientIds, setMonitorPatientIds] = useState([]);
  
  const [monitorSlots, setMonitorSlots] = useState([null, null, null, null]);

  useEffect(() => {
    const mode = new URLSearchParams(window.location.search).get("mode");
    const provider =
      mode === "oracle-evaluation-auto" ? "oracle" :
      mode === "epic-evaluation-auto" ? "epic" : null;
    if (!provider) return undefined;

    const setAutoDemo = provider === "epic" ? setEpicAutoDemo : setOracleAutoDemo;
    const bootstrap = provider === "epic" ? getEpicEvaluationBootstrap : getOracleEvaluationBootstrap;
    const providerLabel = provider === "epic" ? "Epic" : "Oracle";
    let active = true;
    setAutoDemo((previous) => ({...previous,enabled:true,status:"bootstrapping",error:null}));

    bootstrap()
      .then((payload) => {
        if (!active) return;
        if (!payload?.ready) throw new Error(payload?.reason || `${providerLabel} automatic evaluation is not ready.`);
        const launchPatient=payload.patient||{};
        const episodePackPatient=payload?.episodePack?.patient||payload?.scenario?.patient||null;
        if(!episodePackPatient) throw new Error("The selected scenario did not return a complete episode-package patient.");
        const dashboardPatient=normalizeDashboardPatient(episodePackPatient,{});
        setPatients((previous)=>[dashboardPatient,...previous.filter((item)=>item.id!==dashboardPatient.id)]);
        setSelectedPatientId(dashboardPatient.id);
        setFhirEnabled(false);
        setFhirStatus(`Episode package selected via ${providerLabel}: ${dashboardPatient.name}`);
        const existingRun=payload?.existingRun||null;
        const existingState=String(existingRun?.state||"").toUpperCase();
        const existingIsComplete=existingState==="COMPLETE"&&existingRun?.episodeId&&existingRun?.incidentId;
        const existingIsActive=["ARMED","INJECTING","POST_EVENT","ANALYZING"].includes(existingState);
        setAutoDemo({enabled:true,status:existingIsComplete?"complete":existingIsActive?"running":"ready",patient:launchPatient,scenario:payload.scenario,episodePack:payload.episodePack||null,contextMode:payload.clinicalContextMode||"episode_pack_only",encounterId:payload.encounterId||null,existingRun,error:null});
        if(existingIsComplete){ setSelectedEpisodeId(existingRun.episodeId); setSelectedIncidentId(existingRun.incidentId); setActivePage("physiology"); }
        else setActivePage("main");
      })
      .catch((error)=>{ if(!active)return; setAutoDemo({enabled:true,status:"error",patient:null,scenario:null,episodePack:null,contextMode:"episode_pack_only",encounterId:null,existingRun:null,error:error instanceof Error?error.message:`${providerLabel} automatic evaluation bootstrap failed.`}); });
    return ()=>{ active=false; };
  }, []);

  const [telemetryMap, setTelemetryMap] = useState(() =>
    Object.fromEntries(
      mockPatients.map((patient) => [
        patient.id,
        createInitialTelemetry(patient.id, patient.risk)
      ])
    )
  );

  useEffect(() => {
    setTelemetryMap((prev) => {
      const next = { ...prev };

      for (const patient of patients) {
        if (!next[patient.id]) {
          next[patient.id] = createInitialTelemetry(patient.id, patient.risk);
        }
      }

      return next;
    });
  }, [patients]);

  useEffect(() => {
    const interval = setInterval(() => {
      setTelemetryMap((prev) => {
        const updated = {};

        for (const patient of patients) {
          updated[patient.id] = nextTelemetryFrame(prev[patient.id] ?? createInitialTelemetry(patient.id, patient.risk));
        }

        return updated;
      });
    }, 90);

    return () => clearInterval(interval);
  }, [patients]);
useEffect(() => {
  const disconnect =
    connectEpisodeEvents({
      onEvent: (event) => {
        const eventType =
          String(
            event?.type || ""
          );

        if (
          [
            "evaluation.injection.armed",
            "evaluation.injection.started",
            "evaluation.injection.detected",
            "evaluation.injection.event_complete",
            "evaluation.injection.captured",
          ].includes(eventType)
        ) {
          evaluationInjectionActiveRef.current =
            true;

          if (eventType === "evaluation.injection.armed") {
            postCaptureCompletedAtRef.current = 0;
            clearPendingSmartAnalyticsNotice();
          }

          // This event is emitted after the configured post-capture samples
          // have been persisted, so it is the timing anchor for the 7-second
          // analytics-ready delay.
          if (eventType === "evaluation.injection.captured") {
            postCaptureCompletedAtRef.current = Date.now();
          }

          if (event.incidentId) {
            evaluationInjectionIncidentRef.current =
              event.incidentId;
          }
        }

        if (
          eventType ===
          "evaluation.injection.complete"
        ) {
          evaluationInjectionActiveRef.current =
            false;

          evaluationInjectionIncidentRef.current =
            event.incidentId ||
            null;

          setSelectedEpisodeId(
            event.episodeId || null
          );

          setSelectedIncidentId(
            event.incidentId || null
          );

          queueSmartAnalyticsNotice({
            mode:
              "evaluation_injection",
            status: "ready",
            title:
              event.title ||
              "Evaluation analysis completed",
            message:
              event.message ||
              "The captured evaluation episode is ready in Analytics.",
            episodeId:
              event.episodeId || null,
            incidentId:
              event.incidentId || null,
            scenarioId:
              event.scenarioId || null,
            score:
              event.score || null,
            oracleDemo:
              event.oracleDemo || null,
            epicDemo:
              event.epicDemo || null,
          });

          if (event.oracleDemo) {
            setOracleAutoDemo((previous) => ({
              ...previous,
              enabled: true,
              status: "complete",
              patient:
                event.oracleDemo.patient ||
                previous.patient,
              scenario: {
                ...(previous.scenario || {}),
                scenarioId:
                  event.scenarioId ||
                  event.oracleDemo.scenarioId,
              },
            }));
          }

          if (event.epicDemo) {
            setEpicAutoDemo((previous) => ({
              ...previous,
              enabled: true,
              status: "complete",
              patient: event.epicDemo.patient || previous.patient,
              scenario: {
                ...(previous.scenario || {}),
                scenarioId: event.scenarioId || event.epicDemo.scenarioId,
              },
            }));
          }

          console.info(
            "[KGEN EVAL INJECTION APP] complete",
            event
          );

          return;
        }

        if (
          eventType ===
            "evaluation.injection.failed" ||
          eventType ===
            "evaluation.injection.cancelled"
        ) {
          evaluationInjectionActiveRef.current =
            false;

          evaluationInjectionIncidentRef.current =
            null;

          postCaptureCompletedAtRef.current = 0;
          clearPendingSmartAnalyticsNotice();

          return;
        }

        if (
          eventType ===
          "episode.captured"
        ) {
          if (
            evaluationInjectionActiveRef.current
          ) {
            console.info(
              "[KGEN EVAL INJECTION APP] ignored normal episode during evaluation workflow",
              {
                episodeId:
                  event.episodeId,
                incidentId:
                  event.incidentId,
              }
            );

            return;
          }

          setSelectedEpisodeId(
            event.episodeId || null
          );

          setSelectedIncidentId(
            event.incidentId || null
          );

          setAnalyticsNotice({
            status: "captured",
            title:
              "ECG episode captured",
            message:
              "An episode was stored and the incident analysis is running.",
            episodeId:
              event.episodeId || null,
            incidentId:
              event.incidentId || null,
          });

          return;
        }

        if (
          eventType ===
          "phase7.ready"
        ) {
          const isInjectionIncident =
            Boolean(
              event.incidentId &&
              event.incidentId ===
                evaluationInjectionIncidentRef
                  .current
            );

          if (
            evaluationInjectionActiveRef.current ||
            isInjectionIncident ||
            event.mode ===
              "evaluation_injection"
          ) {
            console.info(
              "[KGEN EVAL INJECTION APP] ignored intermediate Phase 7 notice",
              {
                incidentId:
                  event.incidentId,
              }
            );

            return;
          }

          setSelectedEpisodeId(
            event.episodeId ||
              event.primaryEpisodeId ||
              null
          );

          setSelectedIncidentId(
            event.incidentId || null
          );

          setAnalyticsNotice({
            status: "ready",
            title:
              "Incident ready in Analytics",
            message:
              "Episode analysis and the interpretation package are available.",
            episodeId:
              event.episodeId ||
              event.primaryEpisodeId ||
              null,
            incidentId:
              event.incidentId || null,
          });
        }
      },

      onError: () => {},
    });

  return disconnect;
}, [
  clearPendingSmartAnalyticsNotice,
  queueSmartAnalyticsNotice,
]);
  useEffect(() => {
    if (
      analyticsNotice?.mode !== "evaluation_injection" ||
      analyticsNotice?.status !== "ready" ||
      !(analyticsNotice?.oracleDemo || analyticsNotice?.epicDemo)
    ) {
      return undefined;
    }

    const timer = window.setTimeout(() => {
      setSelectedEpisodeId(
        analyticsNotice.episodeId || null
      );
      setSelectedIncidentId(
        analyticsNotice.incidentId || null
      );
      setEvaluationDemo({
        active: false,
        episodeId: null,
        episode: null,
        run: null,
        capture: null,
      });
      setActivePage("physiology");
      if (analyticsNotice?.oracleDemo) {
        setOracleAutoDemo((previous) => ({
          ...previous,
          status: "analytics",
        }));
      }
      if (analyticsNotice?.epicDemo) {
        setEpicAutoDemo((previous) => ({
          ...previous,
          status: "analytics",
        }));
      }
      setAnalyticsNotice(null);
    }, SMART_ANALYTICS_NOTICE_VISIBLE_MS);

    return () => window.clearTimeout(timer);
  }, [analyticsNotice]);

  const selectedPatient = useMemo(
    () => patients.find((patient) => patient.id === selectedPatientId) ?? patients[0],
    [selectedPatientId]
  );

  const selectedTelemetry = telemetryMap[selectedPatientId];

  const activeAlerts = useMemo(
    () => getVitalAlerts(selectedTelemetry),
    [selectedTelemetry]
  );

  useEffect(() => {
    if (!selectedTelemetry) return;

    const signature = activeAlerts.map((alert) => alert.id).join("|");

    if (!signature || signature === lastAlertSignatureRef.current) return;

    lastAlertSignatureRef.current = signature;

    const newEvents = activeAlerts.map((alert) => ({
      id: `${Date.now()}-${alert.id}`,
      patientId: selectedPatientId,
      time: formatCurrentTime(),
      type: alert.level === "critical" ? "Critical Alert" : "Alert",
      level: alert.level,
      title: alert.title,
      message: alert.message
    }));

    setTimelineEvents((prev) => [...newEvents, ...prev].slice(0, 20));
  }, [activeAlerts, selectedTelemetry, selectedPatientId]);

  useEffect(() => {
    setTimelineEvents([
      {
        id: `selected-${Date.now()}`,
        patientId: selectedPatientId,
        time: formatCurrentTime(),
        type: "Patient Selected",
        level: "normal",
        title: `${selectedPatient.name} opened`,
        message: `Live vitals monitoring started for MRN ${selectedPatient.mrn}.`
      }
    ]);

    lastAlertSignatureRef.current = "";
  }, [selectedPatientId]);

  useEffect(() => {
    function handleOutsideClick(event) {
      if (searchRef.current && !searchRef.current.contains(event.target)) {
        setGlobalSearchOpen(false);
      }
    }

    document.addEventListener("mousedown", handleOutsideClick);

    return () => {
      document.removeEventListener("mousedown", handleOutsideClick);
    };
  }, []);

  useEffect(() => {
    document.body.classList.toggle("dark-mode", darkMode);
  }, [darkMode]);

const handleSelectPatient = (id) => {
  setSelectedPatientId(id);
  setModal(null);
  setGlobalSearchOpen(false);
  setGlobalSearchQuery("");

  setSelectedEpisodeId(null);
  setSelectedIncidentId(null);

  setEvaluationDemo({
  active: false,
  episodeId: null,
  episode: null,
  run: null,
  capture: null,
});
};


  async function loadFirelySandbox() {
    setFhirLoading(true);
    setFhirStatus("Connecting to Firely sandbox...");

    try {
      const metadata = await testFirelyConnection();
      const firelyPatients = await fetchFirelyPatients(8);

      if (!firelyPatients.length) {
        throw new Error("No Patient resources returned from Firely sandbox.");
      }

      setPatients(firelyPatients);
      setSelectedPatientId(firelyPatients[0].id);
      setFhirEnabled(true);
      setFhirStatus(`Connected: ${metadata.software} ${metadata.version}`);
    } catch (error) {
      console.error(error);
      setFhirStatus("Firely sandbox could not be loaded. Mock data is still active.");
      setFhirEnabled(false);
    } finally {
      setFhirLoading(false);
    }
  }

  function useMockData() {
    setPatients(mockPatients);
    setMedications(mockMedications);
    setLabs(mockLabs);
    setDocuments(mockDocuments);
    setSelectedPatientId(mockPatients[0].id);
    setFhirEnabled(false);
    setFhirStatus("Using mock data");
  }

  useEffect(() => {
    let ignore = false;

    async function loadClinicalData() {
      if (!fhirEnabled) {
        setMedications(mockMedications);
        setLabs(mockLabs);
        setDocuments(mockDocuments);
        return;
      }

      try {
        const data = await fetchFirelyPatientClinicalData(selectedPatientId);
        if (ignore) return;

        setMedications(data.medications.length ? data.medications : mockMedications);
        setLabs(data.observations.length ? data.observations : mockLabs);
        setDocuments(data.documents.length ? data.documents : mockDocuments);
      } catch (error) {
        console.error(error);
        if (!ignore) {
          setMedications(mockMedications);
          setLabs(mockLabs);
          setDocuments(mockDocuments);
        }
      }
    }

    loadClinicalData();

    return () => {
      ignore = true;
    };
  }, [fhirEnabled, selectedPatientId]);

  const openModal = (type) => setModal(type);
  const closeModal = () => setModal(null);

  function renderDashboardPage() {
  return (
    <>
      <div className="main-toolbar">
        <div>
          <p className="eyebrow">Clinical dashboard</p>
          <h1>{selectedPatient.name}</h1>
        </div>

        <div className="toolbar-actions">
          <button className="ghost-btn" onClick={() => setDarkMode((value) => !value)}>
            {darkMode ? "Light mode" : "Dark mode"}
          </button>

          <button className="ghost-btn" onClick={() => setCompactMode((value) => !value)}>
            {compactMode ? "Comfort view" : "Compact view"}
          </button>

          <button className="primary-btn" onClick={() => openModal("note")}>
            + Add note
          </button>

          <button className="ghost-btn" onClick={loadFirelySandbox} disabled={fhirLoading}>
            {fhirLoading ? "Loading Firely..." : "Use Firely"}
          </button>

          <button className="ghost-btn" onClick={useMockData}>
            Use mock
          </button>
        </div>
      </div>

      <section className="fhir-status-card">
        <strong>{fhirEnabled ? "Firely sandbox mode" : "Mock testing mode"}</strong>
        <p>
          {fhirStatus}. Live ECG and vitals are still simulated because public FHIR
          sandboxes do not stream bedside telemetry.
        </p>
      </section>

      <AlertPanel alerts={activeAlerts} />

      <section className="dashboard-grid priority-two-grid">
        <div className="patient-column">
          <PatientSummary patient={selectedPatient} />
          <ECGPanel telemetry={selectedTelemetry} />
        </div>

        <TimelineFeed events={timelineEvents} />
      </section>

      <section className={`widgets-grid ${compactMode ? "compact" : ""}`}>
        <WidgetCard
          title="Medication Log"
          items={medications}
          kind="medications"
          onAdd={() => openModal("medications")}
          onSeeAll={() => openModal("medications")}
          defaultExpanded={false}
          resetKey={selectedPatientId}
        />

        <WidgetCard
          title="Lab Results"
          items={labs}
          kind="labs"
          onAdd={() => openModal("labs")}
          onSeeAll={() => openModal("labs")}
          defaultExpanded={false}
          resetKey={selectedPatientId}
        />
      </section>

      <WidgetCard
        title="Documents"
        items={documents}
        kind="documents"
        onAdd={() => openModal("documents")}
        onSeeAll={() => openModal("documents")}
        variant="toggled"
        fullWidth
        defaultExpanded={false}
        resetKey={selectedPatientId}
      />
    </>
  );
}

function renderMainPage() {
  return (
    <SevenLeadWaveformPage
      patient={selectedPatient}
      oracleAutoDemo={oracleAutoDemo}
      onOracleAutoDemoChange={(update) =>
        setOracleAutoDemo((previous) => ({...previous,...update}))
      }
      epicAutoDemo={epicAutoDemo}
      onEpicAutoDemoChange={(update) =>
        setEpicAutoDemo((previous) => ({...previous,...update}))
      }

      evaluationDemo={
        evaluationDemo
      }
      onEvaluationAnalysisComplete={(notice) => {
  console.info(
    "[KGEN EVAL APP] analysis notice received",
    notice
  );

  queueSmartAnalyticsNotice(notice);
}}

      onEvaluationChange={
        setEvaluationDemo
      }

      onOpenAnalytics={(episodeId) => {
  if (episodeId) {
    setSelectedEpisodeId(episodeId);
  }

  setActivePage("physiology");
}}
    />
  );
}

function renderMonitorPage() {
  return (
    <MultiPatientMonitor
      patients={patients}
      telemetryMap={telemetryMap}
      monitorSlots={monitorSlots}
      onDropPatient={(slotIndex, patientId) => {
        setMonitorSlots((prev) => {
          const next = [...prev];

          const existingIndex = next.indexOf(patientId);
          if (existingIndex !== -1) {
            next[existingIndex] = null;
          }

          next[slotIndex] = patientId;
          return next;
        });
      }}
      onRemovePatient={(slotIndex) => {
        setMonitorSlots((prev) => {
          const next = [...prev];
          next[slotIndex] = null;
          return next;
        });
      }}
    />
  );
}

function renderPhysiologyPage() {
  return (
    <ClinicalPhysiologyPage
      patient={selectedPatient}

      episodeId={
        selectedEpisodeId
      }

      incidentId={
        selectedIncidentId
      }

      evaluationDemo={
        evaluationDemo
      }

      onExitEvaluation={() => {
  setEvaluationDemo({
  active: false,
  episodeId: null,
  episode: null,
  run: null,
  capture: null,
});

  setActivePage("main");
}}

      onOpenLabs={() =>
        openModal("labs")
      }
    />
  );
}

function renderActivePage() {
  if (activePage === "main") return renderMainPage();
  if (activePage === "dashboard") return renderDashboardPage();
  if (activePage === "monitor") return renderMonitorPage();
  if (activePage === "physiology") return renderPhysiologyPage();

  return renderMainPage();
}
  return (
    <div className={`app-shell ${compactMode ? "compact-mode" : "comfort-mode"}`}>
      <div ref={searchRef}>
        <Navbar
  searchValue={globalSearchQuery}
  alertCount={activeAlerts.length}
  activePage={activePage}
  onPageChange={setActivePage}
  onSearchChange={(value) => {
    setGlobalSearchQuery(value);
    setGlobalSearchOpen(true);
  }}
  onSearchFocus={() => setGlobalSearchOpen(true)}
  onToggleSidebar={() => setSidebarCollapsed((value) => !value)}
/>
<AnalyticsIncidentNotice
  notice={analyticsNotice}
  onOpen={() => {
  if (
    analyticsNotice?.mode ===
    "evaluation_injection"
  ) {
    console.info(
      "[KGEN EVAL INJECTION APP] opening captured analysis",
      {
        episodeId:
          analyticsNotice?.episodeId,
        incidentId:
          analyticsNotice?.incidentId,
      }
    );

    setSelectedEpisodeId(
      analyticsNotice?.episodeId ||
      null
    );

    setSelectedIncidentId(
      analyticsNotice?.incidentId ||
      null
    );

    setEvaluationDemo({
      active: false,
      episodeId: null,
      episode: null,
      run: null,
      capture: null,
    });

    setActivePage("physiology");
    setAnalyticsNotice(null);
    return;
  }

  if (
    analyticsNotice?.mode ===
    "evaluation"
  ) {
    console.info(
      "[KGEN EVAL APP] opening completed evaluation analysis",
      {
        episodeId:
          analyticsNotice?.episodeId,
        runId:
          analyticsNotice?.runId,
      }
    );

    setActivePage(
      "physiology"
    );

    setAnalyticsNotice(null);
    return;
  }

  setSelectedEpisodeId(
    analyticsNotice?.episodeId ||
    null
  );

  setSelectedIncidentId(
    analyticsNotice?.incidentId ||
    null
  );

  setActivePage("physiology");
  setAnalyticsNotice(null);
}}
  onDismiss={() =>
    setAnalyticsNotice(null)
  }
/>

        {globalSearchOpen && (
          <SearchOverlay
            patients={patients}
            recentSearches={recentSearches}
            query={globalSearchQuery}
            onClose={() => setGlobalSearchOpen(false)}
            onSelectPatient={handleSelectPatient}
          />
        )}
      </div>

      <div className={`workspace ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
        <PatientSidebar
          patients={patients}
          selectedPatientId={selectedPatientId}
          onSelectPatient={handleSelectPatient}
          onAddPatient={() => openModal("patient")}
          collapsed={sidebarCollapsed}
        />

        <main className="dashboard-main" aria-label="Patient dashboard">
          {/* <div className="main-toolbar">
            <div>
              <p className="eyebrow">Clinical dashboard</p>
              <h1>{selectedPatient.name}</h1>
            </div>

            <div className="toolbar-actions">
              
              <button className="ghost-btn" onClick={() => setDarkMode((value) => !value)}>
                {darkMode ? "Light mode" : "Dark mode"}
              </button>

              <button className="ghost-btn" onClick={() => setCompactMode((value) => !value)}>
                {compactMode ? "Comfort view" : "Compact view"}
              </button>

              <button className="primary-btn" onClick={() => openModal("note")}>
                + Add note
              </button>
            </div>
          </div>

          <AlertPanel alerts={activeAlerts} />


          <section className="dashboard-grid priority-two-grid">
            <div className="patient-column">
              
              <PatientSummary patient={selectedPatient} />
              <ECGPanel telemetry={selectedTelemetry} />
            </div>

            <TimelineFeed events={timelineEvents} />
          </section> */}
          {renderActivePage()}
        </main>
      </div>

      {modal && (
        <OverlayModal
          type={modal}
          patient={selectedPatient}
          medications={medications}
          labs={labs}
          documents={documents}
          onClose={closeModal}
        />
      )}
    </div>
  );
}