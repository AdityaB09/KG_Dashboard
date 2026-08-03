param(
    [string]$BackendRoot = (Get-Location).Path,

    [string]$TextOnlyRoot = "",

    [string]$MultimodalRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$BackendRoot = (Resolve-Path $BackendRoot).Path

if (-not $TextOnlyRoot) {
    $TextOnlyRoot = Join-Path `
        $BackendRoot `
        "data\colab_model_benchmark\evaluated\medgemma-27b-all8-incart-v6-0-integrated-v6-0-1\google-medgemma-27b-text-it"
}

if (-not $MultimodalRoot) {
    $MultimodalRoot = Join-Path `
        $BackendRoot `
        "data\colab_model_benchmark\evaluated\medgemma-27b-it-all8-incart-v6-0-1\google-medgemma-27b-it"
}

if (-not (Test-Path $TextOnlyRoot)) {
    throw "Text-only evaluated root was not found: $TextOnlyRoot"
}

if (-not (Test-Path $MultimodalRoot)) {
    throw "Multimodal evaluated root was not found: $MultimodalRoot"
}

function Read-RunSummaries(
    [string]$Root,
    [string]$ExpectedModel
) {
    $Rows = @(
        Get-ChildItem `
            -LiteralPath $Root `
            -Recurse `
            -File `
            -Filter "run_summary.json" |
        ForEach-Object {
            $Data = Get-Content `
                -LiteralPath $_.FullName `
                -Raw |
            ConvertFrom-Json

            if ($Data.model -eq $ExpectedModel) {
                $Data
            }
        }
    )

    if ($Rows.Count -ne 8) {
        throw (
            "Expected 8 run summaries for $ExpectedModel, " +
            "found $($Rows.Count). Root: $Root"
        )
    }

    return $Rows
}

function Get-Median(
    [double[]]$Values
) {
    $Sorted = @($Values | Sort-Object)

    if ($Sorted.Count -eq 0) {
        return 0.0
    }

    if ($Sorted.Count % 2 -eq 1) {
        return [double]$Sorted[
            [math]::Floor($Sorted.Count / 2)
        ]
    }

    $Upper = $Sorted.Count / 2

    return (
        [double]$Sorted[$Upper - 1] +
        [double]$Sorted[$Upper]
    ) / 2
}

function Get-Rate(
    [int]$Numerator,
    [int]$Denominator
) {
    if ($Denominator -eq 0) {
        return 0.0
    }

    return [math]::Round(
        $Numerator / $Denominator,
        4
    )
}

function Build-ModelStatistics(
    [object[]]$Rows,
    [string]$ModelName
) {
    $Count = $Rows.Count
    $Scores = @($Rows | ForEach-Object { [double]$_.totalScore })
    $Latencies = @($Rows | ForEach-Object { [double]$_.elapsedSeconds })

    $StrictCount = @(
        $Rows |
        Where-Object { $_.strictlyAccepted -eq $true }
    ).Count

    $ReviewCount = @(
        $Rows |
        Where-Object { $_.displayableWithReview -eq $true }
    ).Count

    $ValidatorCount = @(
        $Rows |
        Where-Object { $_.validatorPassed -eq $true }
    ).Count

    $SafetyCount = @(
        $Rows |
        Where-Object { $_.safetyPass -eq $true }
    ).Count

    $OverallCount = @(
        $Rows |
        Where-Object { $_.overallPass -eq $true }
    ).Count

    $Worst = $Rows |
        Sort-Object totalScore |
        Select-Object -First 1

    [pscustomobject]@{
        Model = $ModelName
        RunCount = $Count
        GenerationSuccessRate = Get-Rate `
            (@($Rows | Where-Object {
                $_.generationSucceeded -eq $true
            }).Count) `
            $Count
        StrictAcceptanceRate = Get-Rate $StrictCount $Count
        AcceptedWithReviewCount = $ReviewCount
        ValidatorPassRate = Get-Rate $ValidatorCount $Count
        SafetyPassRate = Get-Rate $SafetyCount $Count
        OverallPassRate = Get-Rate $OverallCount $Count
        AverageScore = [math]::Round(
            ($Scores | Measure-Object -Average).Average,
            3
        )
        MedianScore = [math]::Round(
            (Get-Median $Scores),
            3
        )
        MinimumScore = ($Scores | Measure-Object -Minimum).Minimum
        MaximumScore = ($Scores | Measure-Object -Maximum).Maximum
        WorstScenario = $Worst.scenarioId
        TotalContradictions = (
            $Rows |
            Measure-Object `
                -Property contradictionCount `
                -Sum
        ).Sum
        TotalUnsupportedFacts = (
            $Rows |
            Measure-Object `
                -Property unsupportedFactCount `
                -Sum
        ).Sum
        AverageLatencySeconds = [math]::Round(
            ($Latencies | Measure-Object -Average).Average,
            3
        )
        MedianLatencySeconds = [math]::Round(
            (Get-Median $Latencies),
            3
        )
    }
}

$TextRows = Read-RunSummaries `
    $TextOnlyRoot `
    "google/medgemma-27b-text-it"

$MultiRows = Read-RunSummaries `
    $MultimodalRoot `
    "google/medgemma-27b-it"

$TextByScenario = @{}
foreach ($Row in $TextRows) {
    $TextByScenario[[string]$Row.scenarioId] = $Row
}

$MultiByScenario = @{}
foreach ($Row in $MultiRows) {
    $MultiByScenario[[string]$Row.scenarioId] = $Row
}

$ScenarioIds = @(
    $TextByScenario.Keys |
    Sort-Object
)

if (
    (@($MultiByScenario.Keys | Sort-Object) -join "|") -ne
    ($ScenarioIds -join "|")
) {
    throw "The two evaluated roots do not contain the same scenario set."
}

$ScenarioComparison = foreach ($ScenarioId in $ScenarioIds) {
    $Text = $TextByScenario[$ScenarioId]
    $Multi = $MultiByScenario[$ScenarioId]

    [pscustomobject]@{
        Scenario = $ScenarioId
        SameEpisodeId = (
            $Text.episodeId -eq $Multi.episodeId
        )
        EpisodeId = $Multi.episodeId
        TextStatus = $Text.status
        MultimodalStatus = $Multi.status
        TextScore = [double]$Text.totalScore
        MultimodalScore = [double]$Multi.totalScore
        ScoreDelta_MultimodalMinusText = [math]::Round(
            (
                [double]$Multi.totalScore -
                [double]$Text.totalScore
            ),
            3
        )
        TextValidatorPassed = $Text.validatorPassed
        MultimodalValidatorPassed = $Multi.validatorPassed
        TextSafetyPass = $Text.safetyPass
        MultimodalSafetyPass = $Multi.safetyPass
        TextContradictions = $Text.contradictionCount
        MultimodalContradictions = $Multi.contradictionCount
        TextUnsupportedFacts = $Text.unsupportedFactCount
        MultimodalUnsupportedFacts = $Multi.unsupportedFactCount
        TextLatencySeconds = [double]$Text.elapsedSeconds
        MultimodalLatencySeconds = [double]$Multi.elapsedSeconds
        LatencyDelta_MultimodalMinusText = [math]::Round(
            (
                [double]$Multi.elapsedSeconds -
                [double]$Text.elapsedSeconds
            ),
            3
        )
    }
}

$EpisodeMismatch = @(
    $ScenarioComparison |
    Where-Object { $_.SameEpisodeId -ne $true }
)

if ($EpisodeMismatch.Count -gt 0) {
    $EpisodeMismatch | Format-List
    throw "The two models were not evaluated on the same episode IDs."
}

$OverallComparison = @(
    Build-ModelStatistics `
        $TextRows `
        "google/medgemma-27b-text-it"

    Build-ModelStatistics `
        $MultiRows `
        "google/medgemma-27b-it"
)

$OutputRoot = Join-Path `
    $BackendRoot `
    "data\colab_model_benchmark\evaluated\medgemma-text-vs-multimodal-v6-0-1"

New-Item `
    -ItemType Directory `
    -Path $OutputRoot `
    -Force |
Out-Null

$ScenarioCsv = Join-Path `
    $OutputRoot `
    "medgemma_text_vs_multimodal_scenario_comparison.csv"

$ScenarioJson = Join-Path `
    $OutputRoot `
    "medgemma_text_vs_multimodal_scenario_comparison.json"

$OverallCsv = Join-Path `
    $OutputRoot `
    "medgemma_text_vs_multimodal_overall_comparison.csv"

$OverallJson = Join-Path `
    $OutputRoot `
    "medgemma_text_vs_multimodal_overall_comparison.json"

$ScenarioComparison |
Export-Csv `
    -Path $ScenarioCsv `
    -NoTypeInformation `
    -Encoding UTF8

$ScenarioComparison |
ConvertTo-Json `
    -Depth 30 |
Set-Content `
    -Path $ScenarioJson `
    -Encoding UTF8

$OverallComparison |
Export-Csv `
    -Path $OverallCsv `
    -NoTypeInformation `
    -Encoding UTF8

$OverallComparison |
ConvertTo-Json `
    -Depth 30 |
Set-Content `
    -Path $OverallJson `
    -Encoding UTF8

Write-Host ""
Write-Host "OVERALL MODEL COMPARISON"

$OverallComparison |
Format-Table -AutoSize

Write-Host ""
Write-Host "SCENARIO-BY-SCENARIO COMPARISON"

$ScenarioComparison |
Format-Table -AutoSize

Write-Host ""
Write-Host "Comparison outputs:"
Write-Host $ScenarioCsv
Write-Host $ScenarioJson
Write-Host $OverallCsv
Write-Host $OverallJson
