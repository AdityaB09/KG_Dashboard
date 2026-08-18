import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import "./CloudDemoAnalyticsAdditions.css";

const PRIMARY_METRIC_KEYS = [
  "model-rhythm",
  "heart-rate",
  "blood-pressure",
  "spo2",
  "qrs",
  "qtc",
  "potassium",
  "magnesium",
  "troponin",
  "creatinine",
  "wbc",
  "lactate",
];

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function compactText(value) {
  return typeof value === "string"
    ? value.replace(/\s+/g, " ").trim()
    : "";
}

function cleanStringList(value, limit = 12) {
  return asArray(value)
    .map((item) =>
      compactText(
        typeof item === "string"
          ? item
          : item?.text ||
              item?.detail ||
              item?.summary ||
              item?.title ||
              item?.name ||
              item?.reason
      )
    )
    .filter(
      (item, index, values) =>
        item && values.indexOf(item) === index
    )
    .slice(0, limit);
}

function normalizeAlternatives(value) {
  return asArray(value)
    .map((item) => {
      if (!item) return null;
      if (typeof item === "string") {
        return {
          alternative: compactText(item),
          why: "",
        };
      }

      const alternative = compactText(
        item.alternative ||
          item.title ||
          item.name ||
          item.label
      );
      const why = compactText(
        item.why ||
          item.reason ||
          item.rationale ||
          item.evidenceAgainst
      );

      return alternative
        ? { alternative, why }
        : null;
    })
    .filter(Boolean)
    .slice(0, 8);
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

function getClinicalInterpretation(result, widget) {
  const source =
    result?.clinicalInterpretation ||
    result?.modelResponse ||
    {};

  const episodeSummary = compactText(
    source.episodeSummary ||
      widget?.episodeSummary ||
      widget?.episodeNarrative
  );
  const rhythm = compactText(
    source.rhythm ||
      widget?.rhythm ||
      widget?.arrhythmiaNarrative
  );
  const primaryEtiology = compactText(
    source.primaryEtiology ||
      widget?.primaryEtiology
  );
  const mechanism = compactText(
    source.mechanism ||
      widget?.mechanism
  );

  return {
    episodeSummary,
    rhythm,
    primaryEtiology,
    mechanism,
    keyECGEvidence: cleanStringList(
      source.keyECGEvidence ||
        widget?.keyECGEvidence
    ),
    contributingFactors: cleanStringList(
      source.contributingFactors ||
        widget?.contributingFactors ||
        asArray(widget?.possibleContributors).map(
          (item) => item?.title || item
        )
    ),
    rejectedAlternatives: normalizeAlternatives(
      source.rejectedAlternatives ||
        widget?.rejectedAlternatives
    ),
    recommendedActions: cleanStringList(
      source.recommendedActions ||
        widget?.recommendedActions
    ),
    uncertainty: cleanStringList(
      source.uncertainty ||
        widget?.uncertainty ||
        widget?.materialEtiologicUncertainty ||
        widget?.importantLimitations
    ),
  };
}

function buildWidget(result) {
  if (!result) return null;

  const existing =
    result?.widgetInterpretation || null;
  const clinical = getClinicalInterpretation(
    result,
    existing
  );

  if (existing) {
    const combinedEtiology = [
      clinical.primaryEtiology,
      clinical.mechanism,
    ]
      .filter(Boolean)
      .join(". ");

    return {
      ...existing,
      schemaVersion:
        existing.schemaVersion ||
        "cardinal-etiology-widget-interpretation-v7.1",
      headline:
        clinical.rhythm ||
        existing.headline ||
        "SLM interpretation",
      episodeSummary:
        clinical.episodeSummary,
      rhythm: clinical.rhythm,
      primaryEtiology:
        clinical.primaryEtiology,
      mechanism: clinical.mechanism,
      keyECGEvidence:
        clinical.keyECGEvidence,
      contributingFactors:
        clinical.contributingFactors,
      rejectedAlternatives:
        clinical.rejectedAlternatives,
      recommendedActions:
        clinical.recommendedActions,
      uncertainty:
        clinical.uncertainty,
      episodeNarrative:
        clinical.episodeSummary ||
        existing.episodeNarrative ||
        "",
      etiologyContextNarrative:
        combinedEtiology ||
        existing.etiologyContextNarrative ||
        existing.rootCauseNarrative ||
        "",
      rootCauseNarrative:
        combinedEtiology ||
        existing.rootCauseNarrative ||
        existing.etiologyContextNarrative ||
        "",
    };
  }

  if (!clinical.episodeSummary && !clinical.primaryEtiology) {
    return null;
  }

  const precomputed = Boolean(
    result?.modelState?.precomputed ||
      result?.precomputedResponse
  );

  return {
    schemaVersion:
      "cardinal-etiology-widget-interpretation-v7.1",
    severity: "warning",
    statusLabel: precomputed
      ? "Precomputed SLM interpretation"
      : "SLM interpretation",
    headline:
      clinical.rhythm || "SLM interpretation",
    ...clinical,
    episodeNarrative: clinical.episodeSummary,
    etiologyContextNarrative: [
      clinical.primaryEtiology,
      clinical.mechanism,
    ]
      .filter(Boolean)
      .join(". "),
    rootCauseNarrative: [
      clinical.primaryEtiology,
      clinical.mechanism,
    ]
      .filter(Boolean)
      .join(". "),
    keyMetrics: [],
  };
}

function MetricValue({ metric }) {
  return (
    <>
      {displayValue(metric.value)}
      {metric.unit ? ` ${metric.unit}` : ""}
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

function DetailSection({
  title,
  className = "",
  children,
}) {
  if (!children) return null;

  return (
    <section
      className={`kgen-slm-modal-section ${className}`.trim()}
    >
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
  const clinical = getClinicalInterpretation(
    result,
    widget
  );


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
            <h2 id="kgen-slm-modal-title">
              {clinical.rhythm ||
                widget.headline ||
                "Clinical interpretation"}
            </h2>
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
            <DetailSection
              title="Identified Rhythm"
              className="kgen-slm-modal-highlight"
            >
              <p className="kgen-slm-rhythm-value">
                {clinical.rhythm || "--"}
              </p>
            </DetailSection>

            {!!clinical.keyECGEvidence.length && (
              <DetailSection title="Key ECG Evidence">
                <ul className="kgen-slm-modal-list">
                  {clinical.keyECGEvidence.map(
                    (item, index) => (
                      <li key={`${item}-${index}`}>
                        {item}
                      </li>
                    )
                  )}
                </ul>
              </DetailSection>
            )}

            <DetailSection
              title="Primary Etiology"
              className="kgen-slm-modal-highlight"
            >
              <p>
                {clinical.primaryEtiology || "--"}
              </p>
            </DetailSection>

            <DetailSection title="Mechanism">
              <p>{clinical.mechanism || "--"}</p>
            </DetailSection>

            {!!clinical.contributingFactors.length && (
              <DetailSection title="Contributing Factors">
                <ul className="kgen-slm-modal-list">
                  {clinical.contributingFactors.map(
                    (item, index) => (
                      <li key={`${item}-${index}`}>
                        {item}
                      </li>
                    )
                  )}
                </ul>
              </DetailSection>
            )}

            {!!clinical.rejectedAlternatives.length && (
              <DetailSection title="Rejected Alternatives">
                <div className="kgen-slm-alternative-list">
                  {clinical.rejectedAlternatives.map(
                    (item, index) => (
                      <article
                        key={`${item.alternative}-${index}`}
                        className="kgen-slm-alternative-item"
                      >
                        <strong>
                          {item.alternative}
                        </strong>
                        {item.why && <p>{item.why}</p>}
                      </article>
                    )
                  )}
                </div>
              </DetailSection>
            )}

            {!!clinical.recommendedActions.length && (
              <DetailSection title="Recommended Actions">
                <ol className="kgen-slm-modal-list kgen-slm-action-list">
                  {clinical.recommendedActions.map(
                    (item, index) => (
                      <li key={`${item}-${index}`}>
                        {item}
                      </li>
                    )
                  )}
                </ol>
              </DetailSection>
            )}

            {!!clinical.uncertainty.length && (
              <DetailSection title="Uncertainty">
                <ul className="kgen-slm-modal-list">
                  {clinical.uncertainty.map(
                    (item, index) => (
                      <li key={`${item}-${index}`}>
                        {item}
                      </li>
                    )
                  )}
                </ul>
              </DetailSection>
            )}
          </div>

          <aside className="kgen-slm-modal-aside">
            {!!metrics.length && (
              <section className="kgen-slm-modal-stat-section">
                <h3>Episode Measurements</h3>
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

  const clinical = useMemo(
    () =>
      widget
        ? getClinicalInterpretation(
            result,
            widget
          )
        : null,
    [result, widget]
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

  if (!widget || !clinical) {
    return (
      <div className="kgen-slm-widget pending">
        <h3>
          {status === "error"
            ? "Interpretation unavailable"
            : fallback?.title ||
              "Preparing SLM interpretation"}
        </h3>
        <p>
          {status === "error"
            ? "The configured SLM response could not be loaded."
            : fallback?.rhythm ||
              "Waiting for the configured SLM response."}
        </p>
      </div>
    );
  }

  const responseUnavailable =
    result?.validationStatus ===
      "precomputed_unavailable" ||
    result?.responseMeta?.contractValid === false;
  const compactMetrics = metrics
    .filter((metric) =>
      metric?.key !== "model-rhythm"
    )
    .slice(0, 4);

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
        <div className="kgen-slm-compact-rhythm kgen-slm-compact-rhythm-primary">
          <span>Identified rhythm</span>
          <strong>
            {clinical.rhythm || widget.headline || "--"}
          </strong>
        </div>

        {responseUnavailable ? (
          <div className="kgen-slm-compact-summary kgen-slm-compact-summary-error">
            <span>Interpretation status</span>
            <strong>
              {result?.responseMeta?.error ||
                "Response unavailable for this episode."}
            </strong>
          </div>
        ) : (
          <div className="kgen-slm-compact-summary">
            <span>Primary etiology</span>
            <strong>
              {clinical.primaryEtiology ||
                widget.etiologyContextNarrative ||
                "--"}
            </strong>
          </div>
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
          View full interpretation
          <strong aria-hidden="true">›</strong>
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
