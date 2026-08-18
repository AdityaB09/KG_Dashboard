$ErrorActionPreference = "Stop"
# CARDINAL V2.1 WINDOWS GCLOUD SHIM
# Windows PowerShell 5.1 may treat harmless stderr emitted by the Google Cloud
# SDK gcloud.ps1 Python wrapper as a terminating NativeCommandError whenever
# ErrorActionPreference is Stop. Prefer gcloud.cmd on Windows and temporarily
# relax ErrorActionPreference only while gcloud runs. Existing scripts still
# validate $LASTEXITCODE, so real gcloud failures remain failures.
if ($env:OS -eq "Windows_NT") {
    $cardinalGcloudCmd = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
    if ($cardinalGcloudCmd) {
        $script:CardinalNativeGcloud = $cardinalGcloudCmd.Source
        function gcloud {
            $previousErrorActionPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = "Continue"
                & $script:CardinalNativeGcloud @args
            }
            finally {
                $ErrorActionPreference = $previousErrorActionPreference
            }
        }
    }
}
# END CARDINAL V2.1 WINDOWS GCLOUD SHIM
$script:DefaultProjectId = "kg-dashboard-505622"
$script:DefaultRegion = "us-central1"
$script:ArtifactRepo = "cardinal-app"
$script:FrontendService = "kg-dashboard-frontend"
$script:BackendService = "kg-dashboard-backend"
$script:ModelService = "cardinal-gemma4-26b-a4b-it"
$script:AlertEmail = "aditya.bagayatkar09@gmail.com"

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' is not installed or not on PATH."
    }
}

function Invoke-Checked([scriptblock]$Command, [string]$Label) {
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Get-GitHubRepoIdentity([string]$ProjectRoot) {
    $owner = "AdityaB09"
    $repo = "KG_Dashboard"
    try {
        $remote = git -C $ProjectRoot config --get remote.origin.url 2>$null
        if ($remote -match 'github\.com[:/](?<owner>[^/]+)/(?<repo>[^/]+?)(?:\.git)?$') {
            $owner = $Matches.owner
            $repo = $Matches.repo -replace '\.git$',''
        }
    } catch {}
    return @{ owner=$owner; repo=$repo }
}

function Initialize-CardinalContext(
    [string]$RequestedProjectId = "",
    [string]$RequestedRegion = ""
) {
    Assert-Command gcloud
    $id = $RequestedProjectId
    if ([string]::IsNullOrWhiteSpace($id)) { $id = $env:CARDINAL_PROJECT_ID }
    if ([string]::IsNullOrWhiteSpace($id)) {
        try { $id = (gcloud config get-value project 2>$null).Trim() } catch {}
    }
    if ([string]::IsNullOrWhiteSpace($id) -or $id -eq '(unset)') { $id = $script:DefaultProjectId }

    $reg = $RequestedRegion
    if ([string]::IsNullOrWhiteSpace($reg)) { $reg = $env:CARDINAL_REGION }
    if ([string]::IsNullOrWhiteSpace($reg)) { $reg = $script:DefaultRegion }

    $number = (gcloud projects describe $id --format='value(projectNumber)' 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($number)) {
        throw "Cannot access GCP project '$id' with the active gcloud account. Run gcloud auth login with the account that owns/has access to this project, then retry."
    }

    $script:ProjectId = $id
    $script:ProjectNumber = $number
    $script:Region = $reg
    $script:StateBucket = "$id-cardinal-tfstate-$number"
    $script:ModelBucket = "$id-$reg-gemma4-models"
    $script:FrontendUrl = "https://$($script:FrontendService)-$number.$reg.run.app"
    $script:BackendUrl = "https://$($script:BackendService)-$number.$reg.run.app"
    $script:ModelUrl = "https://$($script:ModelService)-$number.$reg.run.app"
    $script:BackendRuntimeServiceAccount = "cardinal-backend-runtime@$id.iam.gserviceaccount.com"
    $script:ModelRuntimeServiceAccount = "cardinal-gemma-runtime@$id.iam.gserviceaccount.com"
    $script:GitHubDeployServiceAccount = "github-cardinal-deployer@$id.iam.gserviceaccount.com"
    $script:CloudBuildServiceAccount = "cardinal-cloudbuild@$id.iam.gserviceaccount.com"
    $script:CloudBuildServiceAccountResource = "projects/$id/serviceAccounts/$($script:CloudBuildServiceAccount)"
    $script:WifProvider = "projects/$number/locations/global/workloadIdentityPools/github-cardinal-pool/providers/github-cardinal-provider"

    Write-Host "CARDINAL GCP context:" -ForegroundColor Cyan
    Write-Host "  Project ID:     $script:ProjectId"
    Write-Host "  Project number: $script:ProjectNumber"
    Write-Host "  Region:         $script:Region"
    Write-Host "  Active account: $(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>$null)"
    # If services already exist, prefer their actual Cloud Run status.url.
    # Before first deployment the deterministic URL remains available.
    Resolve-CardinalRuntimeServiceUrls
}

function Get-CardinalCloudRunStatusUrl([string]$ServiceName) {
    if ([string]::IsNullOrWhiteSpace($ServiceName)) { return $null }

    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        $value = & gcloud run services describe $ServiceName `
            --project=$script:ProjectId `
            --region=$script:Region `
            --format="value(status.url)" 2>$null

        $code = $LASTEXITCODE
    }
    catch {
        $value = $null
        $code = 1
    }
    finally {
        $ErrorActionPreference = $savedPreference
    }

    if ($code -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$value)) {
        return ([string]$value).Trim()
    }

    return $null
}

function Resolve-CardinalRuntimeServiceUrls {
    $front = Get-CardinalCloudRunStatusUrl $script:FrontendService
    $back = Get-CardinalCloudRunStatusUrl $script:BackendService
    $model = Get-CardinalCloudRunStatusUrl $script:ModelService

    if ($front) { $script:FrontendUrl = $front }
    if ($back)  { $script:BackendUrl = $back }
    if ($model) { $script:ModelUrl = $model }
}
function Write-CardinalRuntimeTerraformConfig([string]$ProjectRoot) {
    $gh = Get-GitHubRepoIdentity $ProjectRoot
    $bootstrap = @{
        project_id = $script:ProjectId
        region = $script:Region
        state_bucket_name = $script:StateBucket
        model_bucket_name = $script:ModelBucket
        github_owner = $gh.owner
        github_repository = $gh.repo
        github_deploy_branch = 'master'
    } | ConvertTo-Json -Depth 5
    $app = @{
        project_id = $script:ProjectId
        project_number = $script:ProjectNumber
        region = $script:Region
        frontend_image = "$($script:Region)-docker.pkg.dev/$($script:ProjectId)/$($script:ArtifactRepo)/kg-dashboard-frontend:stable"
        backend_image = "$($script:Region)-docker.pkg.dev/$($script:ProjectId)/$($script:ArtifactRepo)/kg-dashboard-backend:stable"
        model_bucket_name = $script:ModelBucket
        backend_runtime_service_account = $script:BackendRuntimeServiceAccount
        model_runtime_service_account = $script:ModelRuntimeServiceAccount
        github_deployer_service_account = $script:GitHubDeployServiceAccount
        alert_email = $script:AlertEmail
    } | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText((Join-Path $ProjectRoot 'infra\bootstrap\runtime.auto.tfvars.json'), $bootstrap + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $ProjectRoot 'infra\app\runtime.auto.tfvars.json'), $app + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

function Write-GitHubRuntimeValues([string]$ProjectRoot) {
    $path = Join-Path $ProjectRoot 'infra\GITHUB_RUNTIME_VALUES.txt'
    $text = @"
GCP_PROJECT_ID=$script:ProjectId
GCP_PROJECT_NUMBER=$script:ProjectNumber
GCP_REGION=$script:Region
GCP_WIF_PROVIDER=$script:WifProvider

Derived URLs:
FRONTEND=$script:FrontendUrl
BACKEND=$script:BackendUrl
GEMMA=$script:ModelUrl
"@
    [IO.File]::WriteAllText($path, $text.Trim() + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    return $path
}

# CARDINAL V2.3 SAFE FIRST-RUN NATIVE PROBES
# PowerShell 5.1 + $ErrorActionPreference="Stop" can turn expected stderr from
# terraform/gcloud/gh existence probes into terminating NativeCommandError.
# Use this helper only for checks where a non-zero exit code means "not present"
# or "not authenticated yet". Real create/apply commands still use normal
# checked execution and fail on non-zero exit codes.
function Test-CardinalNativeProbe([scriptblock]$Command) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $Command *> $null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}
# END CARDINAL V2.3 SAFE FIRST-RUN NATIVE PROBES
