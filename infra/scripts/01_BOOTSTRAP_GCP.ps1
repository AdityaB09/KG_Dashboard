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

& (Join-Path $PSScriptRoot "REFRESH_PRODUCTION_ENV.ps1") -ProjectRoot $ProjectRoot

gcloud config set project $script:ProjectId | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not set gcloud project." }

$active = gcloud auth list --filter=status:ACTIVE --format="value(account)"
if (-not $active) { throw "No active gcloud account." }

Write-Host "Using GCP project: $($script:ProjectId) ($($script:ProjectNumber))" -ForegroundColor Green

# Terraform's google_project data source and multiple project/IAM resources
# require Cloud Resource Manager before Terraform can evaluate the configuration.
# This is a true bootstrap dependency, so enable it OUTSIDE Terraform first.
Write-Host "Ensuring bootstrap APIs are enabled before Terraform..." -ForegroundColor Cyan

gcloud services enable `
  serviceusage.googleapis.com `
  cloudresourcemanager.googleapis.com `
  --project=$script:ProjectId

if ($LASTEXITCODE -ne 0) {
  throw "Could not enable Service Usage / Cloud Resource Manager bootstrap APIs."
}

$resourceManagerReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
  $state = gcloud services list `
    --enabled `
    --filter="config.name=cloudresourcemanager.googleapis.com" `
    --format="value(config.name)" `
    --project=$script:ProjectId

  if ($LASTEXITCODE -eq 0 -and
      $state -eq "cloudresourcemanager.googleapis.com") {
    $resourceManagerReady = $true
    break
  }

  Start-Sleep -Seconds 2
}

if (-not $resourceManagerReady) {
  throw "Cloud Resource Manager API did not become enabled for $($script:ProjectId)."
}

Write-Host "Cloud Resource Manager API: ENABLED" -ForegroundColor Green

if (-not (Test-CardinalNativeProbe {
  gcloud auth application-default print-access-token
})) {
  gcloud auth application-default login
  if ($LASTEXITCODE -ne 0) { throw "ADC login failed." }
}

Test-CardinalNativeProbe {
  gcloud auth application-default set-quota-project $script:ProjectId
} | Out-Null

$billing = ""
try {
  $billing = (gcloud billing projects describe $script:ProjectId --format="value(billingAccountName)" 2>$null) -replace '^billingAccounts/',''
} catch {}

$dir = Join-Path $ProjectRoot "infra\bootstrap"

Push-Location $dir
try {
  Invoke-Checked { terraform init -upgrade } "Terraform bootstrap init"

  function Import-IfNeeded([string]$Address, [string]$Id, [scriptblock]$Exists) {
    if (Test-CardinalNativeProbe { terraform state show $Address }) { return }

    if (Test-CardinalNativeProbe $Exists) {
      Write-Host "Importing existing $Address" -ForegroundColor Yellow
      terraform import $Address $Id
      if ($LASTEXITCODE -ne 0) { throw "Import failed: $Address" }
    }
  }

  Import-IfNeeded "google_storage_bucket.tf_state" $script:StateBucket {
    gcloud storage buckets describe "gs://$($script:StateBucket)"
  }

  Import-IfNeeded "google_storage_bucket.model" $script:ModelBucket {
    gcloud storage buckets describe "gs://$($script:ModelBucket)"
  }

  Import-IfNeeded `
    "google_artifact_registry_repository.app" `
    "projects/$($script:ProjectId)/locations/$($script:Region)/repositories/$($script:ArtifactRepo)" `
    {
      gcloud artifacts repositories describe $script:ArtifactRepo `
        --location=$script:Region `
        --project=$script:ProjectId
    }

  $saMap = @{
    "google_service_account.backend_runtime" = $script:BackendRuntimeServiceAccount
    "google_service_account.model_runtime" = $script:ModelRuntimeServiceAccount
    "google_service_account.github_deployer" = $script:GitHubDeployServiceAccount
    "google_service_account.cloudbuild_runtime" = $script:CloudBuildServiceAccount
  }

  foreach ($entry in $saMap.GetEnumerator()) {
    if (-not (Test-CardinalNativeProbe { terraform state show $entry.Key })) {
      if (Test-CardinalNativeProbe {
        gcloud iam service-accounts describe $entry.Value --project=$script:ProjectId
      }) {
        Write-Host "Importing existing service account $($entry.Value)" -ForegroundColor Yellow
        terraform import $entry.Key "projects/$($script:ProjectId)/serviceAccounts/$($entry.Value)"
        if ($LASTEXITCODE -ne 0) { throw "Service account import failed." }
      }
    }
  }

  # Secret Manager resources are reconciled by Terraform's dynamic for_each set.
  # Never run a separate fixed per-secret import loop.
  $secretMapPath = Join-Path $ProjectRoot "infra\app\backend_secret_env.production.json"
  $secretObj = Get-Content $secretMapPath -Raw | ConvertFrom-Json
  $secretNames = @()
  if ($null -ne $secretObj) {
    $secretNames = @($secretObj.PSObject.Properties.Name | Sort-Object)
  }

  Write-Host ("Terraform desired secret set (names only): " + (($secretNames -join ", ") -replace '^$', '<none>')) -ForegroundColor Cyan

  $args = @("apply", "-auto-approve")
  if ($billing) { $args += "-var=billing_account_id=$billing" }
  if ($active) { $args += "-var=bootstrap_operator_email=$active" }

  & terraform @args
  if ($LASTEXITCODE -ne 0) { throw "Terraform bootstrap apply failed." }

  terraform output
  if ($LASTEXITCODE -ne 0) { throw "Terraform bootstrap output failed." }

  Write-Host "Persistent bootstrap is ready." -ForegroundColor Green
}
finally {
  Pop-Location
}
