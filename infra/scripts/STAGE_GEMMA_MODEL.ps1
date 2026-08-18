param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ProjectId = "",
  [string]$Region = "us-central1"
)

. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Command gcloud

Initialize-CardinalContext `
  -RequestedProjectId $ProjectId `
  -RequestedRegion $Region

$dest = "gs://$($script:ModelBucket)/gemma4-26b-a4b-q4/gemma-4-26B_q4_0-it.gguf"

if (Test-CardinalNativeProbe { gcloud storage ls $dest }) {
  Write-Host "Gemma 4 26B-A4B QAT Q4 already staged:" -ForegroundColor Green
  Write-Host "  $dest"
  exit 0
}

if ([string]::IsNullOrWhiteSpace($env:HF_TOKEN)) {
  throw @"
Gemma 4 26B-A4B QAT Q4 is not staged and HF_TOKEN is not set.
Set a replacement Hugging Face token ONLY in this PowerShell session:
  `$env:HF_TOKEN="YOUR_HF_TOKEN"
Then rerun this script.
"@
}

$tmp = [System.IO.Path]::GetTempFileName()

try {
  [System.IO.File]::WriteAllText(
    $tmp,
    $env:HF_TOKEN,
    [System.Text.UTF8Encoding]::new($false)
  )

  if (-not (Test-CardinalNativeProbe {
      gcloud secrets describe CARDINAL_HF_TOKEN_STAGE --project=$script:ProjectId
    })) {
    gcloud secrets create CARDINAL_HF_TOKEN_STAGE `
      --replication-policy=automatic `
      --project=$script:ProjectId

    if ($LASTEXITCODE -ne 0) {
      throw "Could not create temporary CARDINAL_HF_TOKEN_STAGE secret."
    }
  }

  gcloud secrets versions add CARDINAL_HF_TOKEN_STAGE `
    --data-file=$tmp `
    --project=$script:ProjectId

  if ($LASTEXITCODE -ne 0) {
    throw "Could not add temporary Hugging Face token version."
  }
}
finally {
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

$yaml = Join-Path $ProjectRoot "infra\cloudbuild\stage-gemma-model.yaml"
$buildExit = 1

try {
  gcloud builds submit `
    --no-source `
    --project=$script:ProjectId `
    --region=$script:Region `
    --service-account=$script:CloudBuildServiceAccountResource `
    --config=$yaml `
    --substitutions="_MODEL_BUCKET=$($script:ModelBucket)"

  $buildExit = $LASTEXITCODE
}
finally {
  gcloud secrets delete CARDINAL_HF_TOKEN_STAGE `
    --project=$script:ProjectId `
    --quiet 2>$null | Out-Null
}

if ($buildExit -ne 0) {
  throw "Gemma 26B-A4B staging build failed."
}

if (-not (Test-CardinalNativeProbe { gcloud storage ls $dest })) {
  throw "Cloud Build succeeded but expected Gemma GGUF was not found at $dest"
}

Write-Host ""
Write-Host "Gemma 4 26B-A4B QAT Q4 staging complete." -ForegroundColor Green
Write-Host $dest
