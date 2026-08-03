param(
    [Parameter(Mandatory = $true)]
    [string]$ZipPath,

    [string]$BackendRoot = (Get-Location).Path,

    [switch]$RequireAllEight
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedModel = "google/medgemma-27b-it"
$ExpectedPromptMode = "episode_pack_only"

$BackendRoot = (
    Resolve-Path $BackendRoot
).Path

Set-Location $BackendRoot

$Python = Join-Path `
    $BackendRoot `
    ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Virtual-environment Python was not found: $Python"
}

$ZipPath = (
    Resolve-Path $ZipPath
).Path

Write-Host "Backend root:" $BackendRoot
Write-Host "Python:" $Python
Write-Host "Result ZIP:" $ZipPath

$ImportRoot = Join-Path `
    $BackendRoot `
    "data\colab_model_benchmark\imported\medgemma-27b-it-v6-0-1"

if (Test-Path $ImportRoot) {
    Remove-Item $ImportRoot -Recurse -Force
}

New-Item `
    -ItemType Directory `
    -Path $ImportRoot `
    -Force |
Out-Null

Expand-Archive `
    -Path $ZipPath `
    -DestinationPath $ImportRoot `
    -Force

$ManifestFile = Get-ChildItem `
    $ImportRoot `
    -Recurse `
    -File `
    -Filter "manifest.json" |
Select-Object -First 1

if (-not $ManifestFile) {
    throw "manifest.json was not found in the extracted ZIP."
}

$ResultsRoot = $ManifestFile.Directory.FullName

$Manifest = Get-Content `
    $ManifestFile.FullName `
    -Raw |
ConvertFrom-Json

Write-Host ""
Write-Host "Manifest:"
$Manifest |
Select-Object `
    schemaVersion,
    provider,
    model,
    modelKey,
    loaderType,
    modelLoader,
    repeats,
    clinicalPromptMode,
    scenarioCount,
    expectedGenerationCount,
    completedGenerationCount,
    sourceBatchSha256,
    messageAdapter,
    responseCueVersion,
    generationEnvelopeVersion |
Format-List

if ($Manifest.model -ne $ExpectedModel) {
    throw "Wrong model in manifest: $($Manifest.model)"
}

if ($Manifest.clinicalPromptMode -ne $ExpectedPromptMode) {
    throw (
        "Wrong clinicalPromptMode: " +
        [string]$Manifest.clinicalPromptMode
    )
}

$ExpectedCount = [int]$Manifest.expectedGenerationCount
$CompletedCount = [int]$Manifest.completedGenerationCount
$ScenarioCount = [int]$Manifest.scenarioCount

if ($ExpectedCount -ne $CompletedCount) {
    throw (
        "The generation set is incomplete: " +
        "$CompletedCount/$ExpectedCount"
    )
}

if ($RequireAllEight -and $ScenarioCount -ne 8) {
    throw (
        "-RequireAllEight was supplied, but the manifest contains " +
        "$ScenarioCount scenario(s)."
    )
}

if ($Manifest.repeats -ne 1) {
    throw "This validation script expects repeats=1."
}

$ManifestResults = @($Manifest.results)

if ($ManifestResults.Count -ne $CompletedCount) {
    throw "Manifest results count does not match completedGenerationCount."
}

$GenerationFailures = @(
    $ManifestResults |
    Where-Object {
        $_.generationSucceeded -ne $true -or
        $_.validContract -ne $true -or
        $_.rawEnvelopeValid -ne $true -or
        $_.jsonCompleted -ne $true -or
        $_.emptyDecodedOutput -ne $false -or
        $_.hitMaxOutputTokens -ne $false -or
        $_.usableResponse -ne $true
    }
)

if ($GenerationFailures.Count -gt 0) {
    $GenerationFailures |
    Format-List

    throw "One or more Lightning responses failed generation preflight."
}

$SourceBatchFile = Get-ChildItem `
    $ResultsRoot `
    -File `
    -Filter "source_batch.json" |
Select-Object -First 1

if (-not $SourceBatchFile) {
    throw "source_batch.json was not found beside manifest.json."
}

$SourceBatchHash = (
    Get-FileHash `
        $SourceBatchFile.FullName `
        -Algorithm SHA256
).Hash.ToLowerInvariant()

if (
    $SourceBatchHash -ne
    ([string]$Manifest.sourceBatchSha256).ToLowerInvariant()
) {
    throw "source_batch.json SHA-256 does not match the manifest."
}

$Batch = Get-Content `
    $SourceBatchFile.FullName `
    -Raw |
ConvertFrom-Json

if ($Batch.clinicalPromptMode -ne $ExpectedPromptMode) {
    throw "The source batch does not use episode_pack_only."
}

$BatchItems = @($Batch.items)

$IdentityRows = foreach ($ManifestResult in $ManifestResults) {
    $ScenarioId = [string]$ManifestResult.scenarioId
    $EpisodeId = [string]$ManifestResult.episodeId
    $Fingerprint = [string]$ManifestResult.sourcePromptFingerprint

    $BatchMatches = @(
        $BatchItems |
        Where-Object {
            $_.scenarioId -eq $ScenarioId -and
            $_.episodeId -eq $EpisodeId -and
            $_.promptFingerprint -eq $Fingerprint
        }
    )

    $ExpectedEpisodeDirectory = Join-Path `
        $BackendRoot `
        "data\episodes\$EpisodeId"

    $GroundedInput = Join-Path `
        $ExpectedEpisodeDirectory `
        "grounded_model_input.json"

    if (-not (Test-Path $GroundedInput)) {
        $CandidateDirectories = @()

        if ($BatchMatches.Count -eq 1) {
            $BatchSourceDirectory = [string]$BatchMatches[0].sourceDirectory

            if (
                $BatchSourceDirectory -and
                (Test-Path $BatchSourceDirectory)
            ) {
                $CandidateDirectories += Get-Item `
                    -LiteralPath $BatchSourceDirectory
            }
        }

        $DataRoot = Join-Path $BackendRoot "data"

        if (Test-Path $DataRoot) {
            $CandidateDirectories += @(
                Get-ChildItem `
                    -LiteralPath $DataRoot `
                    -Directory `
                    -Recurse `
                    -Filter $EpisodeId `
                    -ErrorAction SilentlyContinue
            )
        }

        $CandidateDirectories = @(
            $CandidateDirectories |
            Where-Object {
                Test-Path (
                    Join-Path `
                        $_.FullName `
                        "grounded_model_input.json"
                )
            } |
            Sort-Object FullName -Unique
        )

        if ($CandidateDirectories.Count -eq 1) {
            $ResolvedSourceDirectory = `
                $CandidateDirectories[0].FullName

            if (
                $ResolvedSourceDirectory -ne
                $ExpectedEpisodeDirectory
            ) {
                $ExpectedParent = Split-Path `
                    $ExpectedEpisodeDirectory `
                    -Parent

                New-Item `
                    -ItemType Directory `
                    -Path $ExpectedParent `
                    -Force |
                Out-Null

                if (Test-Path $ExpectedEpisodeDirectory) {
                    throw (
                        "The canonical episode path exists but does not " +
                        "contain grounded_model_input.json: " +
                        $ExpectedEpisodeDirectory
                    )
                }

                New-Item `
                    -ItemType Junction `
                    -Path $ExpectedEpisodeDirectory `
                    -Target $ResolvedSourceDirectory |
                Out-Null

                Write-Host "Created canonical episode junction:"
                Write-Host "  Expected:" $ExpectedEpisodeDirectory
                Write-Host "  Actual:  " $ResolvedSourceDirectory
            }

            $GroundedInput = Join-Path `
                $ExpectedEpisodeDirectory `
                "grounded_model_input.json"
        }
        elseif ($CandidateDirectories.Count -gt 1) {
            Write-Host (
                "Multiple exact episode directories were found " +
                "for ${EpisodeId}:"
            )

            $CandidateDirectories |
            Select-Object FullName |
            Format-Table -AutoSize

            throw (
                "Resolve duplicate episode directories before validation."
            )
        }
    }

    $ResponseFiles = @(
        Get-ChildItem `
            $ResultsRoot `
            -Recurse `
            -File `
            -Filter "$Fingerprint.json"
    )

    $ResponseIdentityMatches = $false
    $ResponseUsable = $false

    if ($ResponseFiles.Count -eq 1) {
        try {
            $ResponseData = Get-Content `
                $ResponseFiles[0].FullName `
                -Raw |
            ConvertFrom-Json

            $ResponseIdentityMatches = (
                $ResponseData.model -eq $ExpectedModel -and
                $ResponseData.scenarioId -eq $ScenarioId -and
                $ResponseData.episodeId -eq $EpisodeId -and
                $ResponseData.sourcePromptFingerprint -eq $Fingerprint -and
                $ResponseData.sourceBatchSha256 -eq
                    $Manifest.sourceBatchSha256
            )

            $ResponseUsable = (
                $ResponseData.generationSucceeded -eq $true -and
                $ResponseData.validContract -eq $true -and
                $ResponseData.rawEnvelopeValid -eq $true -and
                $ResponseData.jsonCompleted -eq $true -and
                $ResponseData.emptyDecodedOutput -eq $false -and
                $ResponseData.hitMaxOutputTokens -eq $false -and
                $ResponseData.usableResponse -eq $true -and
                $null -ne $ResponseData.modelResponse
            )
        }
        catch {
            $ResponseIdentityMatches = $false
            $ResponseUsable = $false
        }
    }

    [pscustomobject]@{
        Scenario = $ScenarioId
        EpisodeId = $EpisodeId
        BatchMatchCount = $BatchMatches.Count
        ExactEpisodeExists = Test-Path $GroundedInput
        ExactResponseCount = $ResponseFiles.Count
        ResponseIdentityMatches = $ResponseIdentityMatches
        ResponseUsable = $ResponseUsable
        GenerationSucceeded = $ManifestResult.generationSucceeded
        ValidContract = $ManifestResult.validContract
        RawEnvelopeValid = $ManifestResult.rawEnvelopeValid
        JsonCompleted = $ManifestResult.jsonCompleted
        EmptyDecodedOutput = $ManifestResult.emptyDecodedOutput
        HitMaxOutputTokens = $ManifestResult.hitMaxOutputTokens
        UsableResponse = $ManifestResult.usableResponse
    }
}

Write-Host ""
Write-Host "Identity preflight:"
$IdentityRows |
Sort-Object Scenario |
Format-Table -AutoSize

$IdentityFailures = @(
    $IdentityRows |
    Where-Object {
        $_.BatchMatchCount -ne 1 -or
        $_.ExactEpisodeExists -ne $true -or
        $_.ExactResponseCount -ne 1 -or
        $_.ResponseIdentityMatches -ne $true -or
        $_.ResponseUsable -ne $true -or
        $_.GenerationSucceeded -ne $true -or
        $_.ValidContract -ne $true -or
        $_.RawEnvelopeValid -ne $true -or
        $_.JsonCompleted -ne $true -or
        $_.EmptyDecodedOutput -ne $false -or
        $_.HitMaxOutputTokens -ne $false -or
        $_.UsableResponse -ne $true
    }
)

if ($IdentityFailures.Count -gt 0) {
    $IdentityFailures |
    Format-List

    throw (
        "Batch, episode, response, fingerprint, or generation " +
        "identity verification failed."
    )
}

Write-Host ""
Write-Host "LIGHTNING / BATCH / EPISODE IDENTITY: PASS"

$EvaluatedRoot = Join-Path `
    $BackendRoot `
    "data\colab_model_benchmark\evaluated\medgemma-27b-it-all8-incart-v6-0-1"

if (Test-Path $EvaluatedRoot) {
    Remove-Item $EvaluatedRoot -Recurse -Force
}

New-Item `
    -ItemType Directory `
    -Path $EvaluatedRoot `
    -Force |
Out-Null

$env:PYTHONPATH = $BackendRoot
$env:SLM_MEDICAL_VALIDATOR_ENABLED = "false"
$env:SLM_GROUNDED_RETRY_ENABLED = "false"

$ExecutionRows = foreach (
    $ManifestResult in
    ($ManifestResults | Sort-Object scenarioId)
) {
    $ScenarioId = [string]$ManifestResult.scenarioId
    $EpisodeId = [string]$ManifestResult.episodeId
    $Fingerprint = [string]$ManifestResult.sourcePromptFingerprint

    Write-Host ""
    Write-Host ("=" * 72)
    Write-Host "Validating:" $ScenarioId
    Write-Host "Episode:" $EpisodeId
    Write-Host "Fingerprint:" $Fingerprint
    Write-Host "Model:" $ExpectedModel
    Write-Host ("=" * 72)

    & $Python `
        -m scripts.run_colab_imported_benchmarks `
        --results-root "$ResultsRoot" `
        --model "$ExpectedModel" `
        --runs 1 `
        --episode-id "$EpisodeId" `
        --output-root "$EvaluatedRoot" `
        --overwrite

    if ($LASTEXITCODE -ne 0) {
        throw "Local validation execution failed for $ScenarioId."
    }

    $RunSummaryFile = Get-ChildItem `
        $EvaluatedRoot `
        -Recurse `
        -File `
        -Filter "run_summary.json" |
    Where-Object {
        try {
            $SummaryCandidate = Get-Content `
                $_.FullName `
                -Raw |
            ConvertFrom-Json

            (
                $SummaryCandidate.scenarioId -eq $ScenarioId -and
                $SummaryCandidate.episodeId -eq $EpisodeId -and
                $SummaryCandidate.model -eq $ExpectedModel
            )
        }
        catch {
            $false
        }
    } |
    Select-Object -First 1

    if (-not $RunSummaryFile) {
        throw "run_summary.json was not found for $ScenarioId."
    }

    $Summary = Get-Content `
        $RunSummaryFile.FullName `
        -Raw |
    ConvertFrom-Json

    [pscustomobject]@{
        Scenario = $Summary.scenarioId
        EpisodeId = $Summary.episodeId
        Status = $Summary.status
        StrictlyAccepted = $Summary.strictlyAccepted
        DisplayableWithReview = $Summary.displayableWithReview
        ValidatorPassed = $Summary.validatorPassed
        SafetyPass = $Summary.safetyPass
        Score = $Summary.totalScore
        Grade = $Summary.grade
        OverallPass = $Summary.overallPass
        Contradictions = $Summary.contradictionCount
        UnsupportedFacts = $Summary.unsupportedFactCount
        HardErrors = $Summary.hardErrorCount
        QualityErrors = $Summary.qualityErrorCount
        EvidenceCoverageCount = $Summary.evidenceCoverageCount
        EvidenceCoverageRequired = $Summary.evidenceCoverageRequired
        LatencySeconds = $Summary.elapsedSeconds
        InputTokens = $Summary.inputTokens
        OutputTokens = $Summary.outputTokens
        PeakGpuMemoryGiB = $Summary.peakGpuMemoryGiB
        RunSummaryPath = $RunSummaryFile.FullName
    }
}

$ValidationFiles = @(
    Get-ChildItem `
        $EvaluatedRoot `
        -Recurse `
        -File `
        -Filter "grounding_validation_v4.json"
)

$BenchmarkFiles = @(
    Get-ChildItem `
        $EvaluatedRoot `
        -Recurse `
        -File `
        -Filter "benchmark_result_v4.json"
)

$RunSummaryFiles = @(
    Get-ChildItem `
        $EvaluatedRoot `
        -Recurse `
        -File `
        -Filter "run_summary.json"
)

$WidgetFiles = @(
    Get-ChildItem `
        $EvaluatedRoot `
        -Recurse `
        -File `
        -Filter "slm_widget_result_v4.json"
)

Write-Host ""
Write-Host "Created artifact counts:"
Write-Host "Grounding validations:" $ValidationFiles.Count
Write-Host "Benchmark results:" $BenchmarkFiles.Count
Write-Host "Run summaries:" $RunSummaryFiles.Count
Write-Host "Widget results:" $WidgetFiles.Count

foreach (
    $CountCheck in @(
        @{
            Name = "grounding validations"
            Count = $ValidationFiles.Count
        },
        @{
            Name = "benchmark results"
            Count = $BenchmarkFiles.Count
        },
        @{
            Name = "run summaries"
            Count = $RunSummaryFiles.Count
        },
        @{
            Name = "widget results"
            Count = $WidgetFiles.Count
        }
    )
) {
    if ($CountCheck.Count -ne $CompletedCount) {
        throw (
            "Expected $CompletedCount $($CountCheck.Name), " +
            "found $($CountCheck.Count)."
        )
    }
}

$Comparison = $ExecutionRows |
Sort-Object Scenario

Write-Host ""
Write-Host "Final scenario comparison:"
$Comparison |
Format-Table -AutoSize

$ComparisonCsv = Join-Path `
    $EvaluatedRoot `
    "medgemma_27b_it_v6_0_1_comparison.csv"

$ComparisonJson = Join-Path `
    $EvaluatedRoot `
    "medgemma_27b_it_v6_0_1_comparison.json"

$Comparison |
Export-Csv `
    -Path $ComparisonCsv `
    -NoTypeInformation `
    -Encoding UTF8

$Comparison |
ConvertTo-Json `
    -Depth 30 |
Set-Content `
    -Path $ComparisonJson `
    -Encoding UTF8

$ValidatorDetails = foreach ($ValidationFile in $ValidationFiles) {
    $Validation = Get-Content `
        $ValidationFile.FullName `
        -Raw |
    ConvertFrom-Json

    $MatchingSummary = $Comparison |
    Where-Object {
        $_.RunSummaryPath -like (
            $ValidationFile.Directory.FullName + "*"
        )
    } |
    Select-Object -First 1

    if (-not $MatchingSummary) {
        $RunSummaryPath = Join-Path `
            $ValidationFile.Directory.FullName `
            "run_summary.json"

        if (Test-Path $RunSummaryPath) {
            $FallbackSummary = Get-Content `
                $RunSummaryPath `
                -Raw |
            ConvertFrom-Json

            $ScenarioId = $FallbackSummary.scenarioId
        }
        else {
            $ScenarioId = $ValidationFile.Directory.Parent.Name
        }
    }
    else {
        $ScenarioId = $MatchingSummary.Scenario
    }

    [pscustomobject]@{
        Scenario = $ScenarioId
        Status = $Validation.status
        Accepted = $Validation.accepted
        HardAccepted = $Validation.hardAccepted
        DisplayableWithReview = $Validation.displayableWithReview
        HardErrors = @($Validation.hardErrors) -join " || "
        QualityErrors = @($Validation.qualityErrors) -join " || "
        Contradictions = @($Validation.contradictions) -join " || "
        UnsupportedFacts = @($Validation.unsupportedFacts) -join " || "
        MissingCoverage =
            @($Validation.missingRequiredCoverage) -join ", "
        Warnings = @($Validation.warnings) -join " || "
    }
}

$ValidatorReportPath = Join-Path `
    $EvaluatedRoot `
    "medgemma_27b_it_exact_validator_report.json"

$ValidatorDetails |
Sort-Object Scenario |
ConvertTo-Json `
    -Depth 30 |
Set-Content `
    -Path $ValidatorReportPath `
    -Encoding UTF8

Write-Host ""
Write-Host "Exact validator details:"
$ValidatorDetails |
Sort-Object Scenario |
Format-List

$RunCount = $Comparison.Count

function Rate(
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

$StrictAcceptedCount = @(
    $Comparison |
    Where-Object {
        $_.StrictlyAccepted -eq $true
    }
).Count

$ReviewCount = @(
    $Comparison |
    Where-Object {
        $_.DisplayableWithReview -eq $true
    }
).Count

$ValidatorPassCount = @(
    $Comparison |
    Where-Object {
        $_.ValidatorPassed -eq $true
    }
).Count

$SafetyPassCount = @(
    $Comparison |
    Where-Object {
        $_.SafetyPass -eq $true
    }
).Count

$OverallPassCount = @(
    $Comparison |
    Where-Object {
        $_.OverallPass -eq $true
    }
).Count

$Scores = @(
    $Comparison |
    ForEach-Object {
        [double]$_.Score
    }
)

$SortedScores = @(
    $Scores |
    Sort-Object
)

if ($SortedScores.Count -eq 0) {
    $MedianScore = 0.0
}
elseif ($SortedScores.Count % 2 -eq 1) {
    $MedianScore = $SortedScores[
        [math]::Floor(
            $SortedScores.Count / 2
        )
    ]
}
else {
    $UpperIndex = $SortedScores.Count / 2
    $MedianScore = (
        $SortedScores[$UpperIndex - 1] +
        $SortedScores[$UpperIndex]
    ) / 2
}

$OverallStatistics = [pscustomobject]@{
    Model = $ExpectedModel
    RunCount = $RunCount
    GenerationSuccessRate = 1.0
    StrictAcceptanceRate = $(Rate $StrictAcceptedCount $RunCount)
    AcceptedWithReviewCount = $ReviewCount
    ValidatorPassRate = $(Rate $ValidatorPassCount $RunCount)
    SafetyPassRate = $(Rate $SafetyPassCount $RunCount)
    OverallPassRate = $(Rate $OverallPassCount $RunCount)
    AverageScore = [math]::Round(
        (
            $Comparison |
            Measure-Object `
                -Property Score `
                -Average
        ).Average,
        3
    )
    MedianScore = [math]::Round($MedianScore, 3)
    MinimumScore = (
        $Comparison |
        Measure-Object `
            -Property Score `
            -Minimum
    ).Minimum
    MaximumScore = (
        $Comparison |
        Measure-Object `
            -Property Score `
            -Maximum
    ).Maximum
    TotalContradictions = (
        $Comparison |
        Measure-Object `
            -Property Contradictions `
            -Sum
    ).Sum
    TotalUnsupportedFacts = (
        $Comparison |
        Measure-Object `
            -Property UnsupportedFacts `
            -Sum
    ).Sum
    AverageLatencySeconds = [math]::Round(
        (
            $Comparison |
            Measure-Object `
                -Property LatencySeconds `
                -Average
        ).Average,
        3
    )
    AverageInputTokens = [math]::Round(
        (
            $Comparison |
            Measure-Object `
                -Property InputTokens `
                -Average
        ).Average,
        3
    )
    AverageOutputTokens = [math]::Round(
        (
            $Comparison |
            Measure-Object `
                -Property OutputTokens `
                -Average
        ).Average,
        3
    )
    AveragePeakGpuMemoryGiB = [math]::Round(
        (
            $Comparison |
            Measure-Object `
                -Property PeakGpuMemoryGiB `
                -Average
        ).Average,
        3
    )
}

$StatisticsPath = Join-Path `
    $EvaluatedRoot `
    "medgemma_27b_it_overall_statistics.json"

$OverallStatistics |
ConvertTo-Json `
    -Depth 20 |
Set-Content `
    -Path $StatisticsPath `
    -Encoding UTF8

Write-Host ""
Write-Host "Overall statistics:"
$OverallStatistics |
Format-List

$EvaluatedZip = "${EvaluatedRoot}.zip"

if (Test-Path $EvaluatedZip) {
    Remove-Item $EvaluatedZip -Force
}

Compress-Archive `
    -Path "$EvaluatedRoot\*" `
    -DestinationPath $EvaluatedZip `
    -Force

Write-Host ""
Write-Host "LOCAL VALIDATION AND SCORING: COMPLETE"
Write-Host "Evaluated root:" $EvaluatedRoot
Write-Host "Comparison CSV:" $ComparisonCsv
Write-Host "Comparison JSON:" $ComparisonJson
Write-Host "Validator report:" $ValidatorReportPath
Write-Host "Statistics:" $StatisticsPath
Write-Host "Evaluated ZIP:" $EvaluatedZip

