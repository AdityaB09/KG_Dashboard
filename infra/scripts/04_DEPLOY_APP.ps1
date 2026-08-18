param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)

. (Join-Path $PSScriptRoot "Common.ps1")

Assert-Command gcloud
Assert-Command terraform

Initialize-CardinalContext `
  -RequestedProjectId $ProjectId `
  -RequestedRegion $Region

Write-CardinalRuntimeTerraformConfig -ProjectRoot $ProjectRoot

$dir = Join-Path $ProjectRoot "infra\app"
$terraformCommand = Get-Command terraform -ErrorAction Stop
$terraformExe = $terraformCommand.Source

if (-not $terraformExe) {
  $terraformExe = $terraformCommand.Path
}

if (-not $terraformExe) {
  throw "Could not resolve the real terraform executable."
}

# Terraform supports TF_CLI_ARGS* environment variables, which are injected
# into CLI invocations before explicit arguments. A stale TF_CLI_ARGS_plan can
# make a perfectly valid `terraform plan -out=...` look like it has extra
# positional arguments. Preserve them, clear them only for this deploy, then
# restore them in finally.
$cliArgNames = @(
  "TF_CLI_ARGS",
  "TF_CLI_ARGS_init",
  "TF_CLI_ARGS_plan",
  "TF_CLI_ARGS_apply"
)

$savedCliArgs = @{}

foreach ($name in $cliArgNames) {
  $savedCliArgs[$name] = [Environment]::GetEnvironmentVariable($name, "Process")

  if ($null -ne $savedCliArgs[$name]) {
    Write-Host "Temporarily clearing process env $name for deterministic Terraform invocation." -ForegroundColor Yellow
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
  }
}

Push-Location $dir

try {
  Write-Host "`n=== Terraform app init ===" -ForegroundColor Cyan

  $initArgs = @(
    "init",
    "-reconfigure",
    "-backend-config=bucket=$($script:StateBucket)",
    "-backend-config=prefix=cardinal/app"
  )

  & $terraformExe @initArgs

  if ($LASTEXITCODE -ne 0) {
    throw "Terraform app init failed with exit code $LASTEXITCODE"
  }

  $planPath = Join-Path $dir "cardinal.tfplan"
  Remove-Item $planPath -Force -ErrorAction SilentlyContinue

  Write-Host "`n=== Terraform app plan ===" -ForegroundColor Cyan
  Write-Host "Terraform executable: $terraformExe"
  Write-Host "Working directory:    $dir"
  Write-Host "Plan output:          $planPath"

  $planArgs = @(
    "plan",
    "-input=false",
    "-lock-timeout=60s",
    "-out=cardinal.tfplan"
  )

  & $terraformExe @planArgs

  if ($LASTEXITCODE -ne 0) {
    throw "Terraform app plan failed with exit code $LASTEXITCODE"
  }

  if (-not (Test-Path $planPath)) {
    throw "Terraform plan reported success but cardinal.tfplan was not created."
  }

  Write-Host "`n=== Terraform app apply ===" -ForegroundColor Cyan

  $applyArgs = @(
    "apply",
    "-input=false",
    "-auto-approve",
    "cardinal.tfplan"
  )

  & $terraformExe @applyArgs

  if ($LASTEXITCODE -ne 0) {
    throw "Terraform app apply failed with exit code $LASTEXITCODE"
  }

  Write-Host "`n=== Terraform app outputs ===" -ForegroundColor Cyan
  & $terraformExe "output"

  if ($LASTEXITCODE -ne 0) {
    throw "Terraform app output failed with exit code $LASTEXITCODE"
  }
}
finally {
  Pop-Location

  foreach ($name in $cliArgNames) {
    [Environment]::SetEnvironmentVariable(
      $name,
      $savedCliArgs[$name],
      "Process"
    )
  }
}
