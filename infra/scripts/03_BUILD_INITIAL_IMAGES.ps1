param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Command gcloud
Initialize-CardinalContext -RequestedProjectId $ProjectId -RequestedRegion $Region
$frontImage = "$($script:Region)-docker.pkg.dev/$($script:ProjectId)/$($script:ArtifactRepo)/kg-dashboard-frontend:stable"
$backImage  = "$($script:Region)-docker.pkg.dev/$($script:ProjectId)/$($script:ArtifactRepo)/kg-dashboard-backend:stable"
Push-Location $ProjectRoot
try {
  gcloud builds submit . `
    --project=$script:ProjectId `
    --region=$script:Region `
    --service-account=$script:CloudBuildServiceAccountResource `
    --config="infra/cloudbuild/build-app-images.yaml" `
    --substitutions="_FRONTEND_IMAGE=$frontImage,_BACKEND_IMAGE=$backImage,_BACKEND_URL=$($script:BackendUrl)"
  if ($LASTEXITCODE -ne 0) { throw "Cloud Build image build failed." }
} finally { Pop-Location }
