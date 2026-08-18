param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

Initialize-CardinalContext -RequestedProjectId $ProjectId -RequestedRegion $Region
Write-CardinalRuntimeTerraformConfig -ProjectRoot $ProjectRoot
Write-GitHubRuntimeValues -ProjectRoot $ProjectRoot | Out-Null

& (Join-Path $PSScriptRoot "REFRESH_PRODUCTION_ENV.ps1") -ProjectRoot $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw "Production environment refresh failed." }

Write-Host "=== CARDINAL FIRST-TIME GCP SETUP ===" -ForegroundColor Cyan
Write-Host "Target: $($script:ProjectId) / $($script:ProjectNumber) / $($script:Region)" -ForegroundColor Cyan

& (Join-Path $PSScriptRoot "01_BOOTSTRAP_GCP.ps1") -ProjectRoot $ProjectRoot -ProjectId $script:ProjectId -Region $script:Region
if ($LASTEXITCODE -ne 0) { throw "Bootstrap failed." }

& (Join-Path $PSScriptRoot "02_SYNC_SECRETS_FROM_ENV.ps1") -ProjectRoot $ProjectRoot -ProjectId $script:ProjectId -Region $script:Region
if ($LASTEXITCODE -ne 0) { throw "Secret synchronization failed." }

$gemmaConfig = "gs://$($script:ModelBucket)/gemma4-26b-a4b-q4/gemma-4-26B_q4_0-it.gguf"

if (-not (Test-CardinalNativeProbe { gcloud storage ls $gemmaConfig })) {
  if ([string]::IsNullOrWhiteSpace($env:HF_TOKEN)) {
    throw @"
The full google/gemma-4-26B-A4B-it-qat-q4_0-gguf QAT Q4 checkpoint is not staged yet.
Set:
  `$env:HF_TOKEN="YOUR_HUGGING_FACE_TOKEN"
Then rerun FIRST_TIME_SETUP.ps1.
"@
  }

  & (Join-Path $PSScriptRoot "STAGE_GEMMA_MODEL.ps1") -ProjectRoot $ProjectRoot -ProjectId $script:ProjectId -Region $script:Region
  if ($LASTEXITCODE -ne 0) { throw "Gemma staging failed." }
}

& (Join-Path $PSScriptRoot "03_BUILD_INITIAL_IMAGES.ps1") -ProjectRoot $ProjectRoot -ProjectId $script:ProjectId -Region $script:Region
if ($LASTEXITCODE -ne 0) { throw "Initial image build failed." }

& (Join-Path $PSScriptRoot "04_DEPLOY_APP.ps1") -ProjectRoot $ProjectRoot -ProjectId $script:ProjectId -Region $script:Region
if ($LASTEXITCODE -ne 0) { throw "App deployment failed." }

& (Join-Path $PSScriptRoot "06_CONFIGURE_GITHUB_VARIABLES.ps1") -ProjectRoot $ProjectRoot -ProjectId $script:ProjectId -Region $script:Region

& (Join-Path $PSScriptRoot "05_VERIFY_GCP_DEPLOYMENT.ps1") -ProjectId $script:ProjectId -Region $script:Region
if ($LASTEXITCODE -ne 0) { throw "GCP verification failed." }

Write-Host "FIRST-TIME SETUP COMPLETE" -ForegroundColor Green
