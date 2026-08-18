param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Command gcloud
Initialize-CardinalContext -RequestedProjectId $ProjectId -RequestedRegion $Region

$valuesPath = Write-GitHubRuntimeValues -ProjectRoot $ProjectRoot
$gh = Get-GitHubRepoIdentity $ProjectRoot
$repo = "$($gh.owner)/$($gh.repo)"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  Write-Warning "GitHub CLI (gh) is not installed. Nothing is broken. Set the four variables shown in $valuesPath under GitHub > Settings > Secrets and variables > Actions > Variables."
  return
}

if (-not (Test-CardinalNativeProbe { gh auth status })) {
  Write-Warning "GitHub CLI is not authenticated. Run 'gh auth login', then rerun this script. The required values are in $valuesPath."
  return
}

Write-Host "Configuring GitHub repository variables for $repo" -ForegroundColor Cyan

gh variable set GCP_PROJECT_ID --repo $repo --body $script:ProjectId
if ($LASTEXITCODE -ne 0) { throw "Failed to set GCP_PROJECT_ID" }

gh variable set GCP_PROJECT_NUMBER --repo $repo --body $script:ProjectNumber
if ($LASTEXITCODE -ne 0) { throw "Failed to set GCP_PROJECT_NUMBER" }

gh variable set GCP_REGION --repo $repo --body $script:Region
if ($LASTEXITCODE -ne 0) { throw "Failed to set GCP_REGION" }

gh variable set GCP_WIF_PROVIDER --repo $repo --body $script:WifProvider
if ($LASTEXITCODE -ne 0) { throw "Failed to set GCP_WIF_PROVIDER" }

Write-Host "GitHub Actions variables configured successfully." -ForegroundColor Green
