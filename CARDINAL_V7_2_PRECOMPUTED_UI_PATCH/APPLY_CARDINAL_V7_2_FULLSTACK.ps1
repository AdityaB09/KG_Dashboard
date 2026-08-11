param(
    [Parameter(Mandatory=$false)]
    [string]$ProjectRoot = "."
)

$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$BackendRoot = Join-Path $ProjectRoot "backend"
$FrontendRoot = Join-Path $ProjectRoot "src"

if (-not (Test-Path $BackendRoot)) {
    throw "backend folder not found under project root: $BackendRoot"
}
if (-not (Test-Path $FrontendRoot)) {
    throw "src folder not found under project root: $FrontendRoot"
}

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupRoot = Join-Path $ProjectRoot "cardinal_v7_2_backup_$Stamp"
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

$BackendFiles = @(
    "app/config.py",
    "app/episodes.py",
    "app/evaluation_injection/etiology_v7.py",
    "app/evaluation_injection/service.py",
    "app/evaluation_injection/scenario_catalog.py",
    "app/evaluation_demo/mapping.py",
    "app/evaluation_demo/service.py",
    "app/evaluation_demo/patient_scenario_map.json",
    "app/slm_widget/assembler.py",
    "tests/evaluation_injection/test_etiology_v7_integration.py",
    "CARDINAL_V7_ENV_PATCH.txt",
    "VERIFY_CARDINAL_V7_INTEGRATION.py"
)

$BackendDirectories = @(
    "SLM_Eval",
    "data/etiology_v7_precomputed"
)

$FrontendFiles = @(
    @{ Source = "frontend/components/CriticalInterpretationWidget.jsx"; Destination = "components/CriticalInterpretationWidget.jsx" },
    @{ Source = "frontend/components/CloudDemoAnalyticsAdditions.css"; Destination = "components/CloudDemoAnalyticsAdditions.css" },
    @{ Source = "frontend/components/ClinicalPhysiologyPage.jsx"; Destination = "components/ClinicalPhysiologyPage.jsx" },
    @{ Source = "frontend/evaluation/evaluationWidgetAdapter.js"; Destination = "evaluation/evaluationWidgetAdapter.js" }
)

function Copy-WithBackup {
    param(
        [string]$Source,
        [string]$Destination,
        [string]$Backup
    )

    if (-not (Test-Path $Source)) {
        throw "Patch source missing: $Source"
    }

    if (Test-Path $Destination) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Backup) | Out-Null
        Copy-Item -Force $Destination $Backup
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -Force $Source $Destination
}

foreach ($Relative in $BackendFiles) {
    Copy-WithBackup `
        -Source (Join-Path $PatchRoot $Relative) `
        -Destination (Join-Path $BackendRoot $Relative) `
        -Backup (Join-Path $BackupRoot (Join-Path "backend" $Relative))
}

foreach ($Relative in $BackendDirectories) {
    $Source = Join-Path $PatchRoot $Relative
    $Destination = Join-Path $BackendRoot $Relative
    $Backup = Join-Path $BackupRoot (Join-Path "backend" $Relative)

    if (-not (Test-Path $Source)) {
        throw "Patch directory missing: $Source"
    }

    if (Test-Path $Destination) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Backup) | Out-Null
        Copy-Item -Recurse -Force $Destination $Backup
        Remove-Item -Recurse -Force $Destination
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -Recurse -Force $Source $Destination
}

foreach ($Entry in $FrontendFiles) {
    $RelativeSource = $Entry.Source
    $RelativeDestination = $Entry.Destination
    Copy-WithBackup `
        -Source (Join-Path $PatchRoot $RelativeSource) `
        -Destination (Join-Path $FrontendRoot $RelativeDestination) `
        -Backup (Join-Path $BackupRoot (Join-Path "src" $RelativeDestination))
}

Write-Host "CARDINAL V7.2 full-stack patch applied." -ForegroundColor Green
Write-Host "Project:  $ProjectRoot"
Write-Host "Backend:  $BackendRoot"
Write-Host "Frontend: $FrontendRoot"
Write-Host "Backup:   $BackupRoot"
Write-Host ""
Write-Host "The script intentionally did NOT overwrite backend/.env or src/.env." -ForegroundColor Yellow
Write-Host "For precomputed E2B mode confirm these backend variables:" -ForegroundColor Yellow
Write-Host "  ETIOLOGY_V7_PRECOMPUTED_ENABLED=true"
Write-Host "  ETIOLOGY_V7_PRECOMPUTED_REQUIRED=true"
Write-Host "  ETIOLOGY_V7_PRECOMPUTED_PROFILE=google-gemma-4-E2B-it"
Write-Host "  ETIOLOGY_V7_LIVE_MODEL_ENABLED=false"
Write-Host "  SLM_EVAL_ALLOW_MODEL=false"
Write-Host "  SLM_PHASE6_CONTEXT_ENABLED=false"
Write-Host ""
Write-Host "Then restart FastAPI and Vite/your Vercel deployment." -ForegroundColor Cyan
