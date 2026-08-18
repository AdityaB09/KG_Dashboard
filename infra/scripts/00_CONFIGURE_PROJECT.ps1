param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)
. (Join-Path $PSScriptRoot "Common.ps1")
Initialize-CardinalContext -RequestedProjectId $ProjectId -RequestedRegion $Region
Write-CardinalRuntimeTerraformConfig -ProjectRoot $ProjectRoot
$values = Write-GitHubRuntimeValues -ProjectRoot $ProjectRoot
Write-Host "Runtime Terraform configuration written for $($script:ProjectId)." -ForegroundColor Green
Write-Host "GitHub variable values written to: $values" -ForegroundColor Green
Write-Host "Frontend URL: $($script:FrontendUrl)"
Write-Host "Backend URL:  $($script:BackendUrl)"
Write-Host "Gemma URL:    $($script:ModelUrl)"
