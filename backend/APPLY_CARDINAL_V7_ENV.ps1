param(
    [string]$EnvFile = ".env",
    [string]$Profile = "google-medgemma-27b-it"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $EnvFile)) { New-Item -ItemType File -Path $EnvFile | Out-Null }

$values = [ordered]@{
    "EVALUATION_INJECTION_ALLOWED_SCENARIOS" = "VFIB-STEMI-001,TORSADES-LQT-002,VT-ISCHEMIC-003,AFIB-RVR-SEPSIS-004,CHB-HYPERK-005,BRADY-DIGTOX-006,SVT-PSVT-007,NSVT-ECTOPY-008,WCT-DIFF-009,WPW-AFIB-010,FLUTTER-IC-011,PERI-STEMI-012,BRASH-013,AMIO-DDI-014"
    "ETIOLOGY_V7_PRECOMPUTED_ENABLED" = "true"
    "ETIOLOGY_V7_PRECOMPUTED_REQUIRED" = "true"
    "ETIOLOGY_V7_PRECOMPUTED_ROOT" = "data/etiology_v7_precomputed"
    "ETIOLOGY_V7_PRECOMPUTED_PROFILE" = $Profile
    "ETIOLOGY_V7_LIVE_MODEL_ENABLED" = "false"
    "SLM_PHASE6_CONTEXT_ENABLED" = "false"
    "SLM_MAX_OUTPUT_TOKENS" = "2500"
}

$lines = @(Get-Content $EnvFile -ErrorAction SilentlyContinue)
foreach ($key in $values.Keys) {
    $replacement = "$key=$($values[$key])"
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$([regex]::Escape($key))\s*=") {
            $lines[$i] = $replacement
            $found = $true
        }
    }
    if (-not $found) { $lines += $replacement }
}
Set-Content -Path $EnvFile -Value $lines -Encoding UTF8
Write-Host "CARDINAL V7 environment values applied to $EnvFile"
Write-Host "Precomputed profile: $Profile"
