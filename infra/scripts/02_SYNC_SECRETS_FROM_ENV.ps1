param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)

. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Command gcloud
Initialize-CardinalContext -RequestedProjectId $ProjectId -RequestedRegion $Region

$envPath = Join-Path $ProjectRoot "backend\.env"
$mapPath = Join-Path $ProjectRoot "infra\app\backend_secret_env.production.json"

& (Join-Path $PSScriptRoot "REFRESH_PRODUCTION_ENV.ps1") -ProjectRoot $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw "Production env refresh failed." }

function Read-DotEnv([string]$Path) {
  $map = @{}
  foreach ($raw in Get-Content $Path) {
    $line = $raw.Trim().TrimStart([char]0xFEFF)
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
    $parts = $line.Split("=",2)
    $key = $parts[0].Trim().TrimStart([char]0xFEFF)
    $value = $parts[1].Trim()

    if ($value.Length -ge 2 -and (
      ($value.StartsWith('"') -and $value.EndsWith('"')) -or
      ($value.StartsWith("'") -and $value.EndsWith("'"))
    )) {
      $value = $value.Substring(1,$value.Length-2)
    }
    $map[$key] = $value
  }
  return $map
}

$values = Read-DotEnv $envPath
$obj = Get-Content $mapPath -Raw | ConvertFrom-Json
$secretNames = @()
if ($null -ne $obj) {
  $secretNames = @($obj.PSObject.Properties.Name | Sort-Object)
}

foreach ($sid in $secretNames) {
  if (-not $values.ContainsKey($sid)) { throw "Missing local value for $sid" }
  $value = [string]$values[$sid]
  if ([string]::IsNullOrWhiteSpace($value)) { throw "Empty local value for $sid" }

  if (-not (Test-CardinalNativeProbe {
    gcloud secrets describe $sid --project=$script:ProjectId
  })) {
    gcloud secrets create $sid --replication-policy=automatic --project=$script:ProjectId
    if ($LASTEXITCODE -ne 0) { throw "Could not create $sid" }
  }

  $tmp = [System.IO.Path]::GetTempFileName()
  try {
    [System.IO.File]::WriteAllText($tmp, $value, [System.Text.UTF8Encoding]::new($false))
    gcloud secrets versions add $sid --data-file=$tmp --project=$script:ProjectId
    if ($LASTEXITCODE -ne 0) { throw "Failed to add secret version: $sid" }
    Write-Host "[SYNC] $sid" -ForegroundColor Green
  }
  finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  }
}
