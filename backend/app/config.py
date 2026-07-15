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
    
settings = Settings()