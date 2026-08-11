import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import "./CloudDemoAnalyticsAdditions.css";

const PRIMARY_METRIC_KEYS = [
  "evaluation-score",
  "evaluation-safety",
  "ecg-confidence",
  "morphology-difference",
  "episodeCount",
  "durationSeconds",
  "referenceVCount",
  "medianHeartRateBpm",
];

function asArray(value) {
  return Array.isArray(value)
    ? value
    : [];
}

const TECHNICAL_UNCERTAINTY_PATTERNS = [
  /not an independent diagnosis/i,
  /\bindependent diagnosis\b/i,
  /\bincart\b/i,
  /reference annotation/i,
  /reference marker/i,
  /dataset reference/i,
  /controlled evaluation/i,
  /diagnostic ownership/i,
  /diagnosis owned by/i,
  /upstream diagnosis/i,
  /slm may reclassify/i,
];

function compactText(value) {
  return typeof value === "string"
    ? value.replace(/\s+/g, " ").trim()
    : "";
}

function isTechnicalUncertainty(value) {
  const text = compactText(
    typeof value === "string"
      ? value
      : value?.text ||
        value?.detail ||
        value?.summary ||
        value?.reason
  );

  return (
    !text ||
    TECHNICAL_UNCERTAINTY_PATTERNS.some(
      (pattern) => pattern.test(text)
    )
  );
}

function clinicalUncertaintyList(widget) {
  const source = Array.isArray(
    widget?.materialEtiologicUncertainty
  )
    ? widget.materialEtiologicUncertainty
    : asArray(widget?.importantLimitations);

  return source
    .map((item) =>
      typeof item === "string"
        ? compactText(item)
        : compactText(
            item?.text ||
            item?.detail ||
            item?.summary ||
            item?.reason
          )
    )
    .filter(
      (item, index, values) =>
        item &&
        !isTechnicalUncertainty(item) &&
        values.indexOf(item) === index
    )
    .slice(0, 2);
}

function clinicalContributorList(widget) {
  const source = Array.isArray(
    widget?.possibleContributors
  )
    ? widget.possibleContributors
    : asArray(widget?.importantFindings);

  return source
    .filter(Boolean)
    .filter((item, index, values) => {
      const title = compactText(
        typeof item === "string"
          ? item
          : item?.title ||
            item?.name ||
            item?.label
      );

      if (!title) return false;

      return (
        values.findIndex((candidate) =>
          compactText(
            typeof candidate === "string"
              ? candidate
              : candidate?.title ||
                candidate?.name ||
                candidate?.label
          ) === title
        ) === index
      );
    })
    .slice(0, 5);
}

function displayValue(value, fallback = "--") {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return fallback;
  }
  return String(value);
}

function buildWidget(result) {
  if (result?.widgetInterpretation) {
    return result.widgetInterpretation;
  }

  if (!result?.narrative) {
    return null;
  }

  const narrative = result.narrative;
  const benchmark = result.benchmark || {};
  const grounding = result.grounding || {};
  const statistics =
    result.evaluationStatistics || {};

  return {
    severity: "warning",
    statusLabel:
      result.validationStatus === "accepted"
        ? "Grounded response accepted"
        : "Clinical interpretation",
    headline:
      result.headline ||
      result.scenarioId ||
      "Evaluation episode",
    episodeNarrative:
      narrative.episodeSummary || "",
    etiologyContextNarrative:
      narrative.mostLikelyEtiologyAndClinicalContext ||
      narrative.mostLikelyEtiology ||
      narrative.clinicalContext ||
      "",
    rootCauseNarrative:
      narrative.mostLikelyEtiologyAndClinicalContext ||
      narrative.mostLikelyEtiology ||
      narrative.clinicalContext ||
      "",
    arrhythmiaNarrative: "",
    morphologyNarrative: "",
    currentSituation: {
      narrative: "",
    },
    possibleContributors:
      asArray(narrative.contributingFactors)
        .map((title) => ({
          title,
          confidenceLabel: "model-generated",
          temporalFit: "episode evidence",
          evidenceAgainst: [],
        })),
    importantLimitations:
      asArray(
        narrative.materialEtiologicUncertainty ||
        narrative.uncertaintyAndMissingData
      ),
    materialEtiologicUncertainty:
      asArray(
        narrative.materialEtiologicUncertainty ||
        narrative.uncertaintyAndMissingData
      ),
    keyMetrics: [
      benchmark.score != null
        ? {
            key: "evaluation-score",
            label: "Evaluator score",
            value: benchmark.score,
            unit: "/100",
          }
        : null,
      statistics.safetyPass != null
        ? {
            key: "evaluation-safety",
            label: "Safety gate",
            value: statistics.safetyPass
              ? "PASS"
              : "FAIL",
            unit: "",
          }
        : null,
    ].filter(Boolean),
    validationSummary: {
      status:
        grounding.status ||
        result.validationStatus,
      strictlyAccepted:
        grounding.accepted ||
        grounding.pass,
      hardErrorCount:
        grounding.hardErrorCount || 0,
      qualityErrorCount:
        grounding.qualityErrorCount || 0,
      contradictionCount: 0,
      unsupportedFactCount: 0,
    },
    evaluationStatistics: statistics,
  };
}

function MetricValue({ metric }) {
  return (
    <>
      {displayValue(metric.value)}
      {metric.unit
        ? ` ${metric.unit}`
        : ""}
    </>
  );
}

function MetricCard({ metric }) {
  return (
    <div className="kgen-cloud-metric-card">
      <span>{metric.label}</span>
      <strong>
        <MetricValue metric={metric} />
      </strong>
    </div>
  );
}

function DetailSection({ title, children }) {
  if (!children) return null;

  return (
    <section className="kgen-slm-modal-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function InterpretationModal({
  result,
  widget,
  metrics,
  onClose,
}) {
  const closeButtonRef = useRef(null);
  const validation =
    widget.validationSummary ||
    result?.validationSummary ||
    {};
  const statistics =
    widget.evaluationStatistics ||
    result?.evaluationStatistics ||
    {};
  const benchmark =
    result?.benchmark ||
    result?.score?.benchmark ||
    {};
  const provenance =
    result?.precomputedResponse ||
    result?.content?.precomputedResponse ||
    result?.cardinalEvaluation
      ?.precomputedResponse ||
    null;
  const modelName =
    result?.modelState?.modelAlias ||
    result?.model?.name ||
    provenance?.model ||
    "Configured model";

  useEffect(() => {
    const previousOverflow =
      document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.body.classList.add(
      "kgen-slm-modal-open"
    );

    closeButtonRef.current?.focus();

    function onKeyDown(event) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener(
      "keydown",
      onKeyDown
    );

    return () => {
      document.body.style.overflow =
        previousOverflow;
      document.body.classList.remove(
        "kgen-slm-modal-open"
      );
      window.removeEventListener(
        "keydown",
        onKeyDown
      );
    };
  }, [onClose]);

  const contributors =
    clinicalContributorList(widget);
  const limitations =
    clinicalUncertaintyList(widget);

  const modal = (
    <div
      className="kgen-slm-modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        className="kgen-slm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="kgen-slm-modal-title"
      >
        <header className="kgen-slm-modal-header">
          <div>
            <span className="kgen-slm-modal-eyebrow">
              {widget.statusLabel ||
                "Clinical interpretation"}
            </span>
            <h2 id="kgen-slm-modal-title">
              {widget.headline}
            </h2>
            <div className="kgen-slm-modal-badges">
              <span>{modelName}</span>
              <span>
                {result?.responseProvenanceLabel ||
                  (provenance
                    ? "Pre-evaluated Lightning response"
                    : "Configured response")}
              </span>
              <span>
                Live inference: {provenance
                  ? "No"
                  : result?.liveInference === false
                  ? "No"
                  : "Configured"}
              </span>
            </div>
          </div>

          <button
            ref={closeButtonRef}
            type="button"
            className="kgen-slm-modal-close"
            onClick={onClose}
            aria-label="Close full interpretation"
          >
            ×
          </button>
        </header>

        <div className="kgen-slm-modal-body">
          <div className="kgen-slm-modal-main">
            <DetailSection title="Episode Summary">
              <p>{widget.episodeNarrative}</p>
            </DetailSection>

            <DetailSection title="Most Likely Etiology / Clinical Context">
              <p>
                {widget.etiologyContextNarrative ||
                  widget.rootCauseNarrative}
              </p>
            </DetailSection>

            {!!contributors.length && (
              <DetailSection title="Contributing Factors">
                <ol className="kgen-slm-modal-list">
                  {contributors.map((item, index) => {
                    const title =
                      typeof item === "string"
                        ? item
                        : item.title;
                    const confidenceLabel =
                      typeof item === "string"
                        ? ""
                        : item.confidenceLabel;
                    const temporalFit =
                      typeof item === "string"
                        ? ""
                        : item.temporalFit;

                    return (
                      <li key={`${title}-${index}`}>
                        <strong>{title}</strong>
                        {(confidenceLabel || temporalFit) && (
                          <small>
                            {[confidenceLabel, temporalFit]
                              .filter(Boolean)
                              .join(" • ")}
                          </small>
                        )}
                      </li>
                    );
                  })}
                </ol>
              </DetailSection>
            )}

            {!!limitations.length && (
              <DetailSection title="Material Etiologic Uncertainty">
                <ul className="kgen-slm-modal-list">
                  {limitations.map((item, index) => (
                    <li key={`${item}-${index}`}>
                      {item}
                    </li>
                  ))}
                </ul>
              </DetailSection>
            )}
          </div>

          <aside className="kgen-slm-modal-aside">
            {!!metrics.length && (
              <section className="kgen-slm-modal-stat-section">
                <h3>Episode and Evaluation Metrics</h3>
                <div className="kgen-slm-modal-metrics">
                  {metrics.map((metric) => (
                    <MetricCard
                      key={metric.key}
                      metric={metric}
                    />
                  ))}
                </div>
              </section>
            )}

            <section className="kgen-slm-modal-stat-section">
              <h3>Grounding Validation</h3>
              <dl className="kgen-slm-modal-facts">
                <div>
                  <dt>Status</dt>
                  <dd>{displayValue(validation.status)}</dd>
                </div>
                <div>
                  <dt>Hard errors</dt>
                  <dd>{displayValue(validation.hardErrorCount, "0")}</dd>
                </div>
                <div>
                  <dt>Quality errors</dt>
                  <dd>{displayValue(validation.qualityErrorCount, "0")}</dd>
                </div>
                <div>
                  <dt>Contradictions</dt>
                  <dd>{displayValue(validation.contradictionCount, "0")}</dd>
                </div>
                <div>
                  <dt>Unsupported facts</dt>
                  <dd>{displayValue(validation.unsupportedFactCount, "0")}</dd>
                </div>
              </dl>
            </section>

            <section className="kgen-slm-modal-stat-section">
              <h3>Benchmark and Safety</h3>
              <dl className="kgen-slm-modal-facts">
                <div>
                  <dt>Evaluator score</dt>
                  <dd>
                    {displayValue(
                      statistics.scenarioScore ??
                      benchmark.score ??
                      result?.score?.total
                    )}
                    {(statistics.scenarioScore ??
                      benchmark.score ??
                      result?.score?.total) != null
                      ? "/100"
                      : ""}
                  </dd>
                </div>
                <div>
                  <dt>Safety gate</dt>
                  <dd>
                    {statistics.safetyPass == null
                      ? "--"
                      : statistics.safetyPass
                      ? "PASS"
                      : "FAIL"}
                  </dd>
                </div>
                <div>
                  <dt>Benchmark grade</dt>
                  <dd>{displayValue(benchmark.grade)}</dd>
                </div>
                <div>
                  <dt>Attempt count</dt>
                  <dd>{displayValue(statistics.attemptCount)}</dd>
                </div>
              </dl>
            </section>

            <section className="kgen-slm-modal-stat-section">
              <h3>Response Provenance</h3>
              <dl className="kgen-slm-modal-facts">
                <div>
                  <dt>Mode</dt>
                  <dd>
                    {provenance
                      ? "Pre-evaluated cloud demo"
                      : "Configured model response"}
                  </dd>
                </div>
                <div>
                  <dt>Scenario</dt>
                  <dd>{displayValue(provenance?.scenarioId || result?.scenarioId)}</dd>
                </div>
                <div>
                  <dt>Lookup</dt>
                  <dd>{displayValue(provenance?.lookupMode)}</dd>
                </div>
                <div>
                  <dt>Source artifact</dt>
                  <dd>{displayValue(provenance?.sourceArtifactSet)}</dd>
                </div>
              </dl>
            </section>
          </aside>
        </div>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}

export default function CriticalInterpretationWidget({
  result,
  status = "loading",
  fallback,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const widget = useMemo(
    () => buildWidget(result),
    [result]
  );

  const metrics = useMemo(() => {
    const source = asArray(widget?.keyMetrics);
    const preferred = [];
    const additional = [];

    source.forEach((metric) => {
      if (
        PRIMARY_METRIC_KEYS.includes(metric.key)
      ) {
        preferred.push(metric);
      } else {
        additional.push(metric);
      }
    });

    return [
      ...preferred,
      ...additional,
    ].slice(0, 12);
  }, [widget?.keyMetrics]);

  if (!widget) {
    return (
      <div className="kgen-slm-widget pending">
        <h3>
          {status === "error"
            ? "Interpretation unavailable"
            : fallback?.title ||
              "Preparing interpretation"}
        </h3>
        <p>
          {status === "error"
            ? "The deterministic episode remains available. The configured response could not be loaded."
            : fallback?.rhythm ||
              "Deterministic analysis and the MedGemma response are being prepared."}
        </p>
      </div>
    );
  }

  const compactContributors =
    clinicalContributorList(widget);
  const validation =
    widget.validationSummary || {};
  const validationStatus = String(
    validation.status ||
    result?.validationStatus ||
    ""
  ).toLowerCase();
  const derivedStatusLabel =
    validationStatus === "accepted"
      ? "Grounded response accepted"
      : validationStatus === "accepted_with_review"
      ? "Grounded response accepted with review"
      : validationStatus === "rejected"
      ? "Model response rejected"
      : widget.statusLabel ||
        "Clinical interpretation";
  const compactMetrics = metrics.slice(0, 4);
  const provenanceLabel =
    result?.responseProvenanceLabel ||
    (result?.precomputedResponse
      ? "Pre-evaluated MedGemma response"
      : result?.modelState?.precomputed
      ? "Pre-evaluated MedGemma response"
      : "Configured model response");

  return (
    <>
      <button
        type="button"
        className={`kgen-slm-compact-card ${
          widget.severity || "warning"
        }`}
        onClick={() => setIsOpen(true)}
        aria-haspopup="dialog"
      >
        <div className="kgen-slm-compact-heading">
          <div>
            <span>{derivedStatusLabel}</span>
            <h3>{widget.headline}</h3>
          </div>
          <small>{provenanceLabel}</small>
        </div>

        <p className="kgen-slm-compact-etiology">
          <b>Most likely etiology:</b>{" "}
          {widget.etiologyContextNarrative ||
            widget.rootCauseNarrative}
        </p>

        {!!compactContributors.length && (
          <p className="kgen-slm-compact-etiology">
            <b>Contributing factors:</b>{" "}
            {compactContributors
              .map((item) =>
                typeof item === "string"
                  ? item
                  : item.title ||
                    item.name ||
                    item.label
              )
              .filter(Boolean)
              .join("; ")}
          </p>
        )}

        {!!compactMetrics.length && (
          <div className="kgen-slm-compact-metrics">
            {compactMetrics.map((metric) => (
              <MetricCard
                key={metric.key}
                metric={metric}
              />
            ))}
          </div>
        )}

        <span className="kgen-slm-open-prompt">
          Open full interpretation
          <strong aria-hidden="true">↗</strong>
        </span>
      </button>

      {isOpen && (
        <InterpretationModal
          result={result}
          widget={widget}
          metrics={metrics}
          onClose={() => setIsOpen(false)}
        />
      )}
    </>
  );
}
