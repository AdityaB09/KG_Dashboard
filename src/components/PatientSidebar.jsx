import { useMemo, useState } from "react";

function patientInitials(patient) {
  const name = String(
    patient?.name || "Patient"
  ).trim();

  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) =>
        part.charAt(0).toUpperCase()
      )
      .join("") || "P"
  );
}

function patientSexInitial(patient) {
  const value = String(
    patient?.sex ||
      patient?.gender ||
      ""
  ).trim();

  return value
    ? value.charAt(0).toUpperCase()
    : "U";
}

export default function PatientSidebar({
  patients,
  selectedPatientId,
  onSelectPatient,
  onAddPatient,
  collapsed,
}) {
  const [query, setQuery] =
    useState("");

  const safePatients =
    Array.isArray(patients)
      ? patients
      : [];

  const groupedPatients =
    useMemo(() => {
      const normalizedQuery =
        query.trim().toLowerCase();

      const filtered =
        safePatients.filter(
          (patient) => {
            const searchable = [
              patient?.name,
              patient?.mrn,
              patient?.id,
              patient?.unit,
              patient?.location,
            ]
              .filter(Boolean)
              .join(" ")
              .toLowerCase();

            return searchable.includes(
              normalizedQuery
            );
          }
        );

      return filtered.reduce(
        (groups, patient) => {
          const unit = String(
            patient?.unit ||
              patient?.location ||
              "Unassigned"
          );

          groups[unit] =
            groups[unit] || [];

          groups[unit].push(patient);
          return groups;
        },
        {}
      );
    }, [safePatients, query]);

  return (
    <aside
      className={`patient-sidebar ${
        collapsed ? "collapsed" : ""
      }`}
    >
      <div className="sidebar-header">
        <h2>Patient directory</h2>

        <p>
          Search among{" "}
          {safePatients.length} patients
        </p>

        <label className="sidebar-search">
          <span>⌕</span>

          <input
            value={query}
            onChange={(event) =>
              setQuery(
                event.target.value
              )
            }
            placeholder="Search all patients"
          />

          <kbd>⌘K</kbd>
        </label>
      </div>

      <div className="patient-list">
        {Object.entries(
          groupedPatients
        ).map(
          ([
            unit,
            unitPatients,
          ]) => (
            <section
              key={unit}
              className="patient-group"
            >
              <div className="group-title">
                <span>{unit}</span>

                <small>
                  ({unitPatients.length}{" "}
                  patients)
                </small>

                <button
                  type="button"
                  onClick={onAddPatient}
                  aria-label={`Add patient to ${unit}`}
                >
                  +
                </button>
              </div>

              {unitPatients.map(
                (patient, index) => {
                  const patientId =
                    String(
                      patient?.id ||
                        patient?.mrn ||
                        `patient-${index}`
                    );

                  const name =
                    patient?.name ||
                    "Unnamed patient";

                  const age =
                    patient?.age ??
                    "--";

                  const mrn =
                    patient?.mrn ||
                    patient?.id ||
                    "--";

                  return (
                    <button
                      key={patientId}
                      type="button"
                      draggable
                      onDragStart={(
                        event
                      ) => {
                        event.dataTransfer.setData(
                          "patientId",
                          patientId
                        );
                      }}
                      className={`patient-row ${
                        String(
                          selectedPatientId
                        ) === patientId
                          ? "selected"
                          : ""
                      }`}
                      onClick={() =>
                        onSelectPatient(
                          patientId
                        )
                      }
                    >
                      <div className="avatar">
                        {patient?.avatar ||
                          patientInitials(
                            patient
                          )}
                      </div>

                      <div>
                        <strong>
                          {name}
                        </strong>

                        <span>
                          {age}{" "}
                          {patientSexInitial(
                            patient
                          )}{" "}
                          | MRN: {mrn}
                        </span>
                      </div>

                      <small>
                        {patient?.lastSeen ||
                          "—"}
                      </small>
                    </button>
                  );
                }
              )}
            </section>
          )
        )}

        {Object.keys(
          groupedPatients
        ).length === 0 && (
          <p className="empty-state">
            No patient found. Try a
            name, unit or MRN.
          </p>
        )}
      </div>
    </aside>
  );
}
