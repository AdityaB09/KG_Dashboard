import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Generic
    FHIR_PROVIDER = os.getenv("FHIR_PROVIDER", "firely").lower()
    POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))
    USE_FALLBACK_DEMO_DATA = os.getenv("USE_FALLBACK_DEMO_DATA", "true").lower() == "true"
    DEMO_PATIENT_ID = os.getenv("DEMO_PATIENT_ID", "kardiogenics-demo")
    DEBUG_FHIR_LOGS = os.getenv(
        "DEBUG_FHIR_LOGS",
        os.getenv("DEBUG_FIRELY_LOGS", "true"),
    ).lower() == "true"
    MAX_DEBUG_OBSERVATIONS = int(os.getenv("MAX_DEBUG_OBSERVATIONS", "25"))

    # Firely
    FIRELY_BASE_URL = os.getenv("FIRELY_BASE_URL", "https://server.fire.ly").rstrip("/")

    # Oracle / Cerner
    ORACLE_MODE = os.getenv("ORACLE_MODE", "open").lower()  # open | smart
    ORACLE_FHIR_BASE_URL = os.getenv("ORACLE_FHIR_BASE_URL", "").rstrip("/")
    ORACLE_CLIENT_ID = os.getenv("ORACLE_CLIENT_ID", "")
    ORACLE_REDIRECT_URI = os.getenv(
        "ORACLE_REDIRECT_URI",
        "http://127.0.0.1:8000/auth/oracle/callback",
    )
    ORACLE_LAUNCH_URI = os.getenv(
        "ORACLE_LAUNCH_URI",
        "http://127.0.0.1:8000/auth/oracle/launch",
    )
    ORACLE_SCOPES = os.getenv(
        "ORACLE_SCOPES",
        (
            "launch openid fhirUser online_access "
            "patient/Patient.rs "
            "patient/Observation.rs "
            "patient/MedicationRequest.rs "
            "patient/MedicationAdministration.rs "
            "patient/MedicationDispense.rs "
            "patient/DiagnosticReport.rs "
            "patient/DocumentReference.rs "
            "patient/Encounter.rs "
            "patient/Condition.rs"
        ),
    )
    ORACLE_TEST_PATIENT_ID = os.getenv("ORACLE_TEST_PATIENT_ID", "")


    # Epic SMART on FHIR (kept separate from Oracle)
    EPIC_FHIR_BASE_URL = os.getenv(
        "EPIC_FHIR_BASE_URL",
        "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4",
    ).rstrip("/")
    # Epic explicitly provides a separate non-production client ID. Sandbox
    # deployments should set EPIC_CLIENT_ID to the non-production ID.
    EPIC_CLIENT_ID = os.getenv(
        "EPIC_CLIENT_ID",
        os.getenv("EPIC_NONPROD_CLIENT_ID", ""),
    )
    EPIC_NONPROD_CLIENT_ID = os.getenv("EPIC_NONPROD_CLIENT_ID", "")
    EPIC_PRODUCTION_CLIENT_ID = os.getenv("EPIC_PRODUCTION_CLIENT_ID", "")
    EPIC_REDIRECT_URI = os.getenv(
        "EPIC_REDIRECT_URI",
        "http://127.0.0.1:8000/auth/epic/callback",
    )
    EPIC_LAUNCH_URI = os.getenv(
        "EPIC_LAUNCH_URI",
        "http://127.0.0.1:8000/auth/epic/launch",
    )
    # Epic recommends launch + openid + fhirUser for EHR launch. Incoming API
    # selection on the Epic app registration controls additional FHIR scopes.
    EPIC_SCOPES = os.getenv(
        "EPIC_SCOPES",
        "launch openid fhirUser",
    )
    EPIC_ALLOWED_ISSUERS = [
        item.strip().rstrip("/")
        for item in os.getenv(
            "EPIC_ALLOWED_ISSUERS",
            "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4",
        ).split(",")
        if item.strip()
    ]

    # Local demo session signing only. Not production.
    SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "dev-only-change-me")

    WAVEFORM_SAMPLE_RATE = int(os.getenv("WAVEFORM_SAMPLE_RATE", "220"))
    WAVEFORM_BATCH_MS = int(os.getenv("WAVEFORM_BATCH_MS", "50"))
    WAVEFORM_VISIBLE_SECONDS = float(os.getenv("WAVEFORM_VISIBLE_SECONDS", "6"))

    PHYSIONET_DB = os.getenv(
        "PHYSIONET_DB",
        "ptb-xl/1.0.3/records500/00000",
    )

    PHYSIONET_RECORD = os.getenv(
        "PHYSIONET_RECORD",
        "00001_hr",
    )

    PHYSIONET_FALLBACK_DB = os.getenv(
        "PHYSIONET_FALLBACK_DB",
        "ptb-xl/1.0.3/records100/00000",
    )

    PHYSIONET_FALLBACK_RECORD = os.getenv(
        "PHYSIONET_FALLBACK_RECORD",
        "00001_lr",
    )
    
    WAVEFORM_TEST_AUTO_GAIN_DEMO = os.getenv(
    "WAVEFORM_TEST_AUTO_GAIN_DEMO",
    "false",
).lower() in {"1", "true", "yes", "on"}
    
    FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
    
        # Waveform source switch
    WAVEFORM_SOURCE = os.getenv("WAVEFORM_SOURCE", "physionet").lower()

    # CSV waveform source
    WAVEFORM_CSV_PATHS = [
        item.strip()
        for item in os.getenv("WAVEFORM_CSV_PATHS", "").split(",")
        if item.strip()
    ]

    WAVEFORM_CSV_ACTIVE_INDEX = int(os.getenv("WAVEFORM_CSV_ACTIVE_INDEX", "0"))

    # If you know the real device calibration, set this.
    # Example: 1000 counts per mV.
    # If left as 0, backend uses demo calibration from raw ADC counts to mV-like display values.
    WAVEFORM_CSV_ECG_COUNTS_PER_MV = float(
        os.getenv("WAVEFORM_CSV_ECG_COUNTS_PER_MV", "0")
    )
    
    
    
    WAVEFORM_TEST_BUFFER_SECONDS = int(os.getenv("WAVEFORM_TEST_BUFFER_SECONDS", "60"))
    
    
    API_RANGE_URL = os.getenv("API_RANGE_URL", "").strip()
    API_RANGE_USER_ID = os.getenv("API_RANGE_USER_ID", "").strip()
    API_RANGE_DEVICE_ID = os.getenv("API_RANGE_DEVICE_ID", "").strip()
    API_RANGE_FROM_TIMESTAMP = os.getenv(
        "API_RANGE_FROM_TIMESTAMP", ""
    ).strip()
    API_RANGE_TO_TIMESTAMP = os.getenv(
        "API_RANGE_TO_TIMESTAMP", ""
    ).strip()
    API_RANGE_TIMEOUT_SECONDS = float(
        os.getenv("API_RANGE_TIMEOUT_SECONDS", "30")
    )
    API_RANGE_ECG_VALUE_TO_MV = float(
        os.getenv("API_RANGE_ECG_VALUE_TO_MV", "1.0")
    )
    API_RANGE_API_KEY = os.getenv("API_RANGE_API_KEY", "").strip()
    API_RANGE_API_KEY_HEADER = os.getenv(
        "API_RANGE_API_KEY_HEADER", "x-api-key"
    ).strip()
    
    INCART_PN_DIR = os.getenv(
        "INCART_PN_DIR",
        "incartdb/1.0.0",
    ).strip()

    INCART_RECORD = os.getenv(
        "INCART_RECORD",
        "I01",
    ).strip()

    INCART_ANNOTATOR = os.getenv(
        "INCART_ANNOTATOR",
        "atr",
    ).strip()
    
    EPISODES_ENABLED = os.getenv(
        "EPISODES_ENABLED",
        "false",
    ).lower() in {"1", "true", "yes", "on"}

    ANALYTICS_EPISODE_MODE = os.getenv(
        "ANALYTICS_EPISODE_MODE",
        "false",
    ).lower() in {"1", "true", "yes", "on"}

    EPISODE_PRE_SECONDS = float(
        os.getenv("EPISODE_PRE_SECONDS", "30")
    )

    EPISODE_POST_SECONDS = float(
        os.getenv("EPISODE_POST_SECONDS", "30")
    )

    

    EPISODE_STORAGE_PATH = os.getenv(
        "EPISODE_STORAGE_PATH",
        "data/episodes",
    ).strip()

    EPISODE_MAX_WAVEFORM_POINTS = int(
        os.getenv("EPISODE_MAX_WAVEFORM_POINTS", "1800")
    )
    
    
    EPISODE_EVENT_PADDING_SECONDS = float(
        os.getenv(
            "EPISODE_EVENT_PADDING_SECONDS",
            "1",
        )
    )

    EPISODE_MERGE_GAP_SECONDS = float(
        os.getenv(
            "EPISODE_MERGE_GAP_SECONDS",
            "3",
        )
    )

    EPISODE_MAX_CAPTURE_SECONDS = float(
        os.getenv(
            "EPISODE_MAX_CAPTURE_SECONDS",
            "60",
        )
    )

    EPISODE_PERSISTENT_COOLDOWN_SECONDS = float(
        os.getenv(
            "EPISODE_PERSISTENT_COOLDOWN_SECONDS",
            "60",
        )
    )
    
    INCIDENTS_ENABLED = os.getenv(
        "INCIDENTS_ENABLED",
        "true",
    ).lower() in {"1", "true", "yes", "on"}

    INCIDENT_STORAGE_PATH = os.getenv(
        "INCIDENT_STORAGE_PATH",
        "data/incidents",
    ).strip()

    INCIDENT_MERGE_GAP_SECONDS = float(
        os.getenv(
            "INCIDENT_MERGE_GAP_SECONDS",
            "15",
        )
    )

    INCIDENT_MAX_SECONDS = float(
        os.getenv(
            "INCIDENT_MAX_SECONDS",
            "300",
        )
    )
    
    CLINICAL_CONTEXT_ENABLED = os.getenv(
        "CLINICAL_CONTEXT_ENABLED",
        "true",
    ).lower() in {"1", "true", "yes", "on"}

    CLINICAL_CONTEXT_PROVIDER = os.getenv(
        "CLINICAL_CONTEXT_PROVIDER",
        "oracle",
    ).strip().lower()

    CLINICAL_CONTEXT_OBSERVATION_COUNT = int(
        os.getenv(
            "CLINICAL_CONTEXT_OBSERVATION_COUNT",
            "200",
        )
    )

    CLINICAL_CONTEXT_RESOURCE_COUNT = int(
        os.getenv(
            "CLINICAL_CONTEXT_RESOURCE_COUNT",
            "100",
        )
    )

    ALLOW_RESEARCH_FHIR_PAIRING = os.getenv(
        "ALLOW_RESEARCH_FHIR_PAIRING",
        "false",
    ).lower() in {"1", "true", "yes", "on"}
    
    
    MONGODB_ENABLED = os.getenv(
    "MONGODB_ENABLED",
    "false",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}

    MONGODB_URI = os.getenv(
        "MONGODB_URI",
        "mongodb://127.0.0.1:27017",
    ).strip()

    MONGODB_DATABASE = os.getenv(
        "MONGODB_DATABASE",
        "kardiogenics",
    ).strip()

    MONGODB_FHIR_COLLECTION = os.getenv(
        "MONGODB_FHIR_COLLECTION",
        "fhir_patient_snapshots",
    ).strip()

    MONGODB_SERVER_SELECTION_TIMEOUT_MS = int(
        os.getenv(
            "MONGODB_SERVER_SELECTION_TIMEOUT_MS",
            "1500",
        )
    )

    FHIR_CACHE_SOFT_REFRESH_SECONDS = int(
        os.getenv(
            "FHIR_CACHE_SOFT_REFRESH_SECONDS",
            "300",
        )
    )

    FHIR_CACHE_MAX_STALE_SECONDS = int(
        os.getenv(
            "FHIR_CACHE_MAX_STALE_SECONDS",
            "86400",
        )
    )

    FHIR_CACHE_BACKGROUND_REFRESH = os.getenv(
        "FHIR_CACHE_BACKGROUND_REFRESH",
        "true",
    ).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    ENABLE_SLM_EVAL = os.getenv(
    "ENABLE_SLM_EVAL",
    "false",
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

    SLM_EVAL_DATASET_ROOT = os.getenv(
        "SLM_EVAL_DATASET_ROOT",
        "SLM_Eval",
    ).strip()

    SLM_EVAL_RESULTS_PATH = os.getenv(
        "SLM_EVAL_RESULTS_PATH",
        "data/evaluation_runs",
    ).strip()

    SLM_EVAL_ALLOW_MODEL = os.getenv(
        "SLM_EVAL_ALLOW_MODEL",
        "false",
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


    # Optional INCART evaluation-injection demo.
    # Disabled by default so the existing stream is unchanged.
    EVALUATION_INJECTION_ENABLED = os.getenv(
        "EVALUATION_INJECTION_ENABLED",
        "false",
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    EVALUATION_INJECTION_DATASET_ROOT = os.getenv(
        "EVALUATION_INJECTION_DATASET_ROOT",
        SLM_EVAL_DATASET_ROOT,
    ).strip()

    EVALUATION_INJECTION_ALLOWED_SCENARIOS = [
        item.strip()
        for item in os.getenv(
            "EVALUATION_INJECTION_ALLOWED_SCENARIOS",
            (
                "VFIB-STEMI-001,TORSADES-LQT-002,VT-ISCHEMIC-003,"
                "AFIB-RVR-SEPSIS-004,CHB-HYPERK-005,BRADY-DIGTOX-006,"
                "SVT-PSVT-007,NSVT-ECTOPY-008,WCT-DIFF-009,WPW-AFIB-010,"
                "FLUTTER-IC-011,PERI-STEMI-012,BRASH-013,AMIO-DDI-014"
            ),
        ).split(",")
        if item.strip()
    ]

    EVALUATION_INJECTION_BASELINE_SECONDS = float(
        os.getenv(
            "EVALUATION_INJECTION_BASELINE_SECONDS",
            "10",
        )
    )

    EVALUATION_INJECTION_PRE_SECONDS = float(
        os.getenv(
            "EVALUATION_INJECTION_PRE_SECONDS",
            "6",
        )
    )

    EVALUATION_INJECTION_POST_SECONDS = float(
        os.getenv(
            "EVALUATION_INJECTION_POST_SECONDS",
            "6",
        )
    )

    EVALUATION_INJECTION_DETECTOR_HOLD_SECONDS = float(
        os.getenv(
            "EVALUATION_INJECTION_DETECTOR_HOLD_SECONDS",
            "1.2",
        )
    )

    EVALUATION_INJECTION_VT_RATE_THRESHOLD = float(
        os.getenv(
            "EVALUATION_INJECTION_VT_RATE_THRESHOLD",
            "150",
        )
    )

    EVALUATION_INJECTION_QRS_THRESHOLD_MS = float(
        os.getenv(
            "EVALUATION_INJECTION_QRS_THRESHOLD_MS",
            "120",
        )
    )


    # Automatic Oracle SMART -> INCART evaluation presentation flow.
    ORACLE_EVALUATION_DEMO_ENABLED = os.getenv(
        "ORACLE_EVALUATION_DEMO_ENABLED",
        "false",
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    ORACLE_EVALUATION_DEMO_MAP_PATH = os.getenv(
        "ORACLE_EVALUATION_DEMO_MAP_PATH",
        "app/evaluation_demo/patient_scenario_map.json",
    ).strip()

    # Keep this false for controlled presentations. An unmapped patient then
    # produces an explicit configuration error instead of an unsupported guess.
    ORACLE_EVALUATION_DEMO_ALLOW_DEFAULT_SCENARIO = os.getenv(
        "ORACLE_EVALUATION_DEMO_ALLOW_DEFAULT_SCENARIO",
        "false",
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # Automatic Epic SMART -> API Range + episode + API Range evaluation flow.
    # This is intentionally a separate mapping namespace from Oracle.
    EPIC_EVALUATION_DEMO_ENABLED = os.getenv(
        "EPIC_EVALUATION_DEMO_ENABLED",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}

    EPIC_EVALUATION_DEMO_MAP_PATH = os.getenv(
        "EPIC_EVALUATION_DEMO_MAP_PATH",
        "app/evaluation_demo/epic_patient_scenario_map.json",
    ).strip()



    # CARDINAL escalation ladder. Disabled unless explicitly enabled so the
    # existing Oracle/Epic evaluation runtime remains unchanged by default.
    ESCALATION_ENABLED = os.getenv(
        "ESCALATION_ENABLED",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}

    ESCALATION_STORAGE_PATH = os.getenv(
        "ESCALATION_STORAGE_PATH",
        "data/escalations",
    ).strip()

    # Optional site-managed automatic progression window. Automatic progression
    # is OFF by default to avoid notification storms / alert fatigue.
    ESCALATION_RESPONSE_WINDOW_SECONDS = float(
        os.getenv("ESCALATION_RESPONSE_WINDOW_SECONDS", os.getenv("ESCALATION_ACK_TIMEOUT_SECONDS", "120"))
    )
    # Legacy alias retained for older code/tests.
    ESCALATION_ACK_TIMEOUT_SECONDS = ESCALATION_RESPONSE_WINDOW_SECONDS

    ESCALATION_AUTO_ADVANCE_DEFAULT = os.getenv(
        "ESCALATION_AUTO_ADVANCE_DEFAULT",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}

    ESCALATION_LEGACY_MANUAL_ACTIONS_ENABLED = os.getenv(
        "ESCALATION_LEGACY_MANUAL_ACTIONS_ENABLED",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}

    ESCALATION_TEAMS_ENABLED = os.getenv(
        "ESCALATION_TEAMS_ENABLED",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}

    ESCALATION_TIMEOUT_POLL_SECONDS = float(
        os.getenv("ESCALATION_TIMEOUT_POLL_SECONDS", "5")
    )

    ESCALATION_SCENARIO_MINIMUMS_JSON = os.getenv(
        "ESCALATION_SCENARIO_MINIMUMS_JSON",
        "{}",
    ).strip()

    ESCALATION_POLICY_DEFAULT_LEVEL = os.getenv(
        "ESCALATION_POLICY_DEFAULT_LEVEL",
        "MONITOR_ONLY",
    ).strip()

    ESCALATION_EMAIL_ENABLED = os.getenv(
        "ESCALATION_EMAIL_ENABLED",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}

    # Versioned CARDINAL policy / operational identity.
    ESCALATION_POLICY_ID = os.getenv(
        "ESCALATION_POLICY_ID",
        "ORACLE-MILLENNIUM-HOSPITAL-RESPONSE-V1",
    ).strip()
    ESCALATION_POLICY_PATH = os.getenv(
        "ESCALATION_POLICY_PATH",
        "app/escalation/policies/oracle_millennium_hospital_response_v1.json",
    ).strip()
    ESCALATION_POLICY_FAIL_SAFE_LEVEL = os.getenv(
        "ESCALATION_POLICY_FAIL_SAFE_LEVEL",
        "URGENT_PROVIDER_REVIEW",
    ).strip()
    ESCALATION_SITE_ID = os.getenv("ESCALATION_SITE_ID", "CARDINAL-DEMO-SITE").strip()
    ESCALATION_FACILITY_ID = os.getenv("ESCALATION_FACILITY_ID", "CARDINAL-LOCAL").strip()
    ESCALATION_SERVICE_ID = os.getenv("ESCALATION_SERVICE_ID", "cardinal-escalation").strip()
    ESCALATION_INSTANCE_ID = os.getenv("ESCALATION_INSTANCE_ID", "local-dev-01").strip()
    ESCALATION_DEFAULT_ACTOR = os.getenv("ESCALATION_DEFAULT_ACTOR", "CARDINAL recipient").strip()
    ESCALATION_DEFAULT_ACTOR_ROLE = os.getenv("ESCALATION_DEFAULT_ACTOR_ROLE", "Clinical responder").strip()

settings = Settings()