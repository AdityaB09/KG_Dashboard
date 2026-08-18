param(
  [switch]$TestGemma,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)

. (Join-Path $PSScriptRoot "Common.ps1")

Assert-Command gcloud
Initialize-CardinalContext `
  -RequestedProjectId $ProjectId `
  -RequestedRegion $Region

$script:fail = 0
$script:warn = 0

function Check([bool]$Ok, [string]$Label) {
  if ($Ok) {
    Write-Host "[PASS] $Label" -ForegroundColor Green
  }
  else {
    $script:fail++
    Write-Host "[FAIL] $Label" -ForegroundColor Red
  }
}

function Warn([string]$Label) {
  $script:warn++
  Write-Host "[WARN] $Label" -ForegroundColor Yellow
}

function Get-ServiceStatusUrl([string]$ServiceName) {
  $saved = $ErrorActionPreference
  $ErrorActionPreference = "Continue"

  try {
    $u = & gcloud run services describe $ServiceName `
      --project=$script:ProjectId `
      --region=$script:Region `
      --format="value(status.url)" 2>$null

    $code = $LASTEXITCODE
  }
  catch {
    $u = $null
    $code = 1
  }
  finally {
    $ErrorActionPreference = $saved
  }

  if ($code -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$u)) {
    return ([string]$u).Trim()
  }

  return $null
}

function Test-HttpStatus(
  [string]$Uri,
  [int]$TimeoutSec = 30
) {
  try {
    $r = Invoke-WebRequest `
      -UseBasicParsing `
      -Uri $Uri `
      -TimeoutSec $TimeoutSec

    return [int]$r.StatusCode
  }
  catch {
    try {
      if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
        return [int]$_.Exception.Response.StatusCode
      }
    }
    catch {}

    Write-Host "       HTTP error for ${Uri}: $($_.Exception.Message)" -ForegroundColor DarkYellow
    return 0
  }
}

Write-Host "=== CARDINAL GCP DEPLOYMENT VERIFIER V4.1 ==="

$frontUrl = Get-ServiceStatusUrl $script:FrontendService
$backUrl = Get-ServiceStatusUrl $script:BackendService
$modelUrl = Get-ServiceStatusUrl $script:ModelService

Check ([bool]$frontUrl) "$($script:FrontendService) exists"
if ($frontUrl) { Write-Host "       $frontUrl" }

Check ([bool]$backUrl) "$($script:BackendService) exists"
if ($backUrl) { Write-Host "       $backUrl" }

Check ([bool]$modelUrl) "$($script:ModelService) exists"
if ($modelUrl) { Write-Host "       $modelUrl" }

if ($frontUrl) {
  $status = Test-HttpStatus "$frontUrl/healthz" 30
  Check ($status -eq 200) "Frontend actual status.url /healthz returns 200"
}

if ($backUrl) {
  $status = Test-HttpStatus "$backUrl/health" 60
  Check ($status -eq 200) "Backend actual status.url /health returns 200"
}

# Read service-level scaling from the v2 Cloud Run Admin API, which exposes
# ServiceScaling.scalingMode and manualInstanceCount directly.
$accessToken = $null
try {
  $accessToken = (& gcloud auth print-access-token 2>$null).Trim()
}
catch {}

$manualCount = $null
$scalingMode = $null

if ($accessToken) {
  $serviceApi = "https://run.googleapis.com/v2/projects/$($script:ProjectId)/locations/$($script:Region)/services/$($script:ModelService)"

  try {
    $service = Invoke-RestMethod `
      -Method Get `
      -Uri $serviceApi `
      -Headers @{ Authorization = "Bearer $accessToken" } `
      -TimeoutSec 30

    if ($service.scaling) {
      $manualCount = $service.scaling.manualInstanceCount
      $scalingMode = [string]$service.scaling.scalingMode
    }
  }
  catch {
    Warn "Could not read scaling through Cloud Run v2 REST API: $($_.Exception.Message)"
  }
}
else {
  Warn "Could not obtain a gcloud access token for the scaling check."
}

Write-Host "Gemma scaling mode=$scalingMode manualInstanceCount=$manualCount"

if (-not $TestGemma) {
  $isManual = ($scalingMode -eq "MANUAL")
  $isZero = (($manualCount -eq 0) -or ($manualCount -eq "0"))

  Check ($isManual -and $isZero) "Gemma safe state is MANUAL with instance count 0"
}

if ($TestGemma) {
  if (-not $modelUrl) {
    Check $false "Gemma model URL exists"
  }
  else {
    try {
      $token = & gcloud auth print-identity-token --audiences=$modelUrl 2>$null

      if ($LASTEXITCODE -ne 0 -or -not $token) {
        $token = & gcloud auth print-identity-token 2>$null
      }

      $r = Invoke-RestMethod `
        -Uri "$modelUrl/v1/models" `
        -Headers @{ Authorization = "Bearer $token" } `
        -TimeoutSec 90

      Check ($null -ne $r) "Gemma /v1/models reachable"
    }
    catch {
      Check $false "Gemma /v1/models reachable: $($_.Exception.Message)"
    }
  }
}

Write-Host ""
Write-Host "PASS/FAIL SUMMARY: FAIL=$script:fail WARN=$script:warn"

if ($script:fail) {
  Write-Host "RESULT=FAIL" -ForegroundColor Red
  exit 1
}

Write-Host "RESULT=PASS" -ForegroundColor Green
exit 0
