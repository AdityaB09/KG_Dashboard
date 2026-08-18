param(
  [switch]$Warm,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Command gcloud
Initialize-CardinalContext -RequestedProjectId $ProjectId -RequestedRegion $Region
Write-Host "Turning ON $($script:ModelService). This starts model compute billing." -ForegroundColor Yellow
gcloud run services update $script:ModelService --project=$script:ProjectId --region=$script:Region --scaling=1
if ($LASTEXITCODE -ne 0) { throw "Could not enable Gemma." }
if ($Warm) {
  Write-Host "Waiting for /v1/models..."
  $deadline = (Get-Date).AddMinutes(20); $ready = $false
  do {
    Start-Sleep -Seconds 15
    try {
      $token = gcloud auth print-identity-token --audiences=$script:ModelUrl
      if ($LASTEXITCODE -ne 0 -or -not $token) { $token = gcloud auth print-identity-token }
      $models = Invoke-RestMethod -Uri "$($script:ModelUrl)/v1/models" -Headers @{Authorization="Bearer $token"} -TimeoutSec 60
      if ($models) { $ready=$true; Write-Host "Gemma is ready." -ForegroundColor Green; break }
    } catch { Write-Host "." -NoNewline }
  } while ((Get-Date) -lt $deadline)
  if (-not $ready) { throw "Gemma did not become ready within 20 minutes. Run MODEL_OFF.ps1 to stop billing, then inspect Cloud Run logs." }
}
