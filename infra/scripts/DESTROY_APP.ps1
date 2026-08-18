param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Command gcloud
Assert-Command terraform
Initialize-CardinalContext -RequestedProjectId $ProjectId -RequestedRegion $Region
Write-CardinalRuntimeTerraformConfig -ProjectRoot $ProjectRoot
& (Join-Path $PSScriptRoot "MODEL_OFF.ps1") -ProjectId $script:ProjectId -Region $script:Region
$dir=Join-Path $ProjectRoot "infra\app"
Push-Location $dir
try{
  terraform init -reconfigure -backend-config="bucket=$($script:StateBucket)" -backend-config="prefix=cardinal/app"
  if($LASTEXITCODE -ne 0){throw "Terraform init failed"}
  terraform destroy -auto-approve
  if($LASTEXITCODE -ne 0){throw "Terraform destroy failed"}
}finally{Pop-Location}
Write-Host "Application layer destroyed. Bootstrap/state/artifacts/model bucket/secrets remain intact." -ForegroundColor Green
