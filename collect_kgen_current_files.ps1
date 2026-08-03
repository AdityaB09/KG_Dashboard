param(
    [Parameter(Mandatory = $false)]
    [string]$ProjectRoot = "C:\Users\adity\Downloads\588_\7 Waveform"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$OutputRoot = Join-Path $ProjectRoot "kgen-current-files-$Timestamp"
$ZipPath = "$OutputRoot.zip"

$Candidates = @(
    "backend\main.py",
    "backend\app\config.py",

    "backend\app\evaluation_injection\service.py",
    "backend\app\evaluation_injection\routes.py",
    "backend\app\evaluation_injection\scenario_catalog.py",
    "backend\app\evaluation_injection\evidence_normalizer.py",
    "backend\app\evaluation_injection\diagnostic_event.py",
    "backend\app\evaluation_injection\grounded_cardinal_client.py",
    "backend\app\evaluation_injection\grounded_prompt_builder.py",
    "backend\app\evaluation_injection\cardinal_bridge.py",
    "backend\app\evaluation_injection\response_validator.py",
    "backend\app\evaluation_injection\etiology_context_scorer.py",
    "backend\app\evaluation_injection\benchmark_report.py",
    "backend\app\evaluation_injection\universal_evaluation_runner.py",
    "backend\app\evaluation_injection\canonical_episode_repository.py",
    "backend\app\evaluation_injection\canonicalize_episode.py",
    "backend\app\evaluation_injection\answer_key_loader.py",
    "backend\app\evaluation_injection\export_colab_batch.py",
    "backend\app\evaluation_injection\patient_scenario_map.json",

    "backend\app\analysis\episode_analyzer.py",
    "backend\app\analysis\io.py",
    "backend\app\analysis\rr_metrics.py",
    "backend\app\analysis\r_peaks.py",
    "backend\app\analysis\qrs.py",
    "backend\app\analysis\morphology.py",
    "backend\app\analysis\confidence.py",
    "backend\app\analysis\lead_agreement.py",

    "backend\app\phase7\evidence.py",
    "backend\app\phase7\prompt_builder.py",
    "backend\app\phase7\orchestrator.py",

    "frontend\src\components\SevenLeadWaveformPage.jsx",
    "frontend\src\evaluation\evaluationInjectionApi.js",
    "frontend\src\evaluation\oracleEvaluationDemo.js",

    "src\components\SevenLeadWaveformPage.jsx",
    "src\evaluation\evaluationInjectionApi.js",
    "src\evaluation\oracleEvaluationDemo.js"
)

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$Copied = @()
$Missing = @()

foreach ($RelativePath in $Candidates) {
    $Source = Join-Path $ProjectRoot $RelativePath

    if (Test-Path $Source -PathType Leaf) {
        $Destination = Join-Path $OutputRoot $RelativePath
        $DestinationDirectory = Split-Path $Destination -Parent

        New-Item `
            -ItemType Directory `
            -Path $DestinationDirectory `
            -Force | Out-Null

        Copy-Item `
            -Path $Source `
            -Destination $Destination `
            -Force

        $Copied += $RelativePath
    }
    else {
        $Missing += $RelativePath
    }
}

$TestDirectories = @(
    "backend\tests\evaluation_injection",
    "backend\data\evaluation_answer_keys"
)

foreach ($RelativeDirectory in $TestDirectories) {
    $SourceDirectory = Join-Path $ProjectRoot $RelativeDirectory

    if (Test-Path $SourceDirectory -PathType Container) {
        $DestinationDirectory = Join-Path $OutputRoot $RelativeDirectory

        New-Item `
            -ItemType Directory `
            -Path (Split-Path $DestinationDirectory -Parent) `
            -Force | Out-Null

        Copy-Item `
            -Path $SourceDirectory `
            -Destination $DestinationDirectory `
            -Recurse `
            -Force

        $Copied += "$RelativeDirectory\*"
    }
    else {
        $Missing += "$RelativeDirectory\*"
    }
}

# Export only relevant environment variable names and values.
# Secret-bearing keys are redacted automatically.
$EnvironmentFiles = @(
    "backend\.env",
    "frontend\.env",
    ".env"
)

$SafeEnvironmentLines = @()

foreach ($RelativeEnv in $EnvironmentFiles) {
    $EnvPath = Join-Path $ProjectRoot $RelativeEnv

    if (-not (Test-Path $EnvPath -PathType Leaf)) {
        continue
    }

    $SafeEnvironmentLines += ""
    $SafeEnvironmentLines += "# $RelativeEnv"

    foreach ($Line in Get-Content $EnvPath) {
        $Trimmed = $Line.Trim()

        if (
            -not $Trimmed -or
            $Trimmed.StartsWith("#") -or
            -not $Trimmed.Contains("=")
        ) {
            continue
        }

        $Parts = $Trimmed.Split("=", 2)
        $Name = $Parts[0].Trim()
        $Value = $Parts[1]

        $Relevant = (
            $Name -match "^(VITE_EVALUATION|VITE_ENABLE_SLM_EVAL|VITE_BACKEND_URL|" +
            "ENABLE_SLM_EVAL|EVALUATION_|EPISODES_ENABLED|INCIDENTS_ENABLED|" +
            "PHASE6_|PHASE7_|SLM_PROMPT_MODE|SLM_ENABLED|SLM_MODEL|" +
            "SLM_MAX_OUTPUT_TOKENS|SLM_TIMEOUT_SECONDS)"
        )

        if (-not $Relevant) {
            continue
        }

        $SecretBearing = (
            $Name -match "(TOKEN|SECRET|PASSWORD|API_KEY|AUTHORIZATION|CLIENT_SECRET)"
        )

        if ($SecretBearing) {
            $Value = "<REDACTED>"
        }

        $SafeEnvironmentLines += "$Name=$Value"
    }
}

$SafeEnvironmentPath = Join-Path $OutputRoot "RELEVANT_ENV_REDACTED.txt"
$SafeEnvironmentLines | Set-Content `
    -Path $SafeEnvironmentPath `
    -Encoding UTF8

$Manifest = [ordered]@{
    createdAt = (Get-Date).ToString("o")
    projectRoot = $ProjectRoot
    copiedCount = $Copied.Count
    copied = $Copied
    missing = $Missing
    note = "No tokens, API keys, OAuth secrets, or full environment files are included."
}

$Manifest |
    ConvertTo-Json -Depth 6 |
    Set-Content `
        -Path (Join-Path $OutputRoot "COLLECTION_MANIFEST.json") `
        -Encoding UTF8

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

Compress-Archive `
    -Path (Join-Path $OutputRoot "*") `
    -DestinationPath $ZipPath `
    -CompressionLevel Optimal

Write-Host ""
Write-Host "Collection completed."
Write-Host "Folder: $OutputRoot"
Write-Host "ZIP:    $ZipPath"
Write-Host ""
Write-Host "Upload the ZIP to the current ChatGPT conversation."
