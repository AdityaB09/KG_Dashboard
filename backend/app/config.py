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
settings = Settings()