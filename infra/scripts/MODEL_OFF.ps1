param(
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Command gcloud
Initialize-CardinalContext -RequestedProjectId $ProjectId -RequestedRegion $Region
Write-Host "Turning OFF $($script:ModelService) (manual instance count 0)." -ForegroundColor Yellow
gcloud run services update $script:ModelService --project=$script:ProjectId --region=$script:Region --scaling=0
if($LASTEXITCODE -ne 0){throw "Could not disable Gemma."}
