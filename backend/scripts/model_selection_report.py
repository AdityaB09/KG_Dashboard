from __future__ import annotations

import argparse
import csv
import html
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def number(value: Any) -> float | None:
    if isinstance(value, bool): return None
    try: value = float(value)
    except (TypeError, ValueError): return None
    return value if math.isfinite(value) else None


def mean(values): return round(statistics.mean(values), 3) if values else None

def median(values): return round(statistics.median(values), 3) if values else None

def stdev(values): return round(statistics.pstdev(values), 3) if len(values) > 1 else (0.0 if values else None)

def rate(count, total): return round(count / total, 4) if total else 0.0


def collect(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in root.rglob("run_summary.json"):
        value = read_json(path)
        if value: rows.append({**value, "summaryPath": str(path)})
    return rows


def model_summary(model: str, rows: list[dict[str, Any]], fastest: float | None) -> dict[str, Any]:
    total = len(rows)
    generated = [x for x in rows if x.get("generationSucceeded") is True]
    scores = [v for x in rows if (v := number(x.get("totalScore"))) is not None]
    latencies = [v for x in generated if (v := number(x.get("elapsedSeconds"))) is not None]
    strict = sum(x.get("strictlyAccepted") is True for x in generated)
    review = sum(x.get("displayableWithReview") is True for x in generated)
    valid = sum(x.get("validatorPassed") is True for x in generated)
    safety = sum(x.get("safetyPass") is True for x in generated)
    overall = sum(x.get("overallPass") is True for x in generated)
    retry = sum(int(x.get("attemptCount") or 0) > 1 for x in generated)
    contrad = sum(int(x.get("contradictionCount") or 0) > 0 for x in generated)
    unsupported = sum(int(x.get("unsupportedFactCount") or 0) > 0 for x in generated)
    by_scenario: dict[str, list[float]] = defaultdict(list)
    for x in rows:
        score = number(x.get("totalScore"))
        if score is not None: by_scenario[str(x.get("scenarioId") or "unknown")].append(score)
    scenario_avg = {k: round(statistics.mean(v), 3) for k,v in by_scenario.items() if v}
    worst = min(scenario_avg, key=scenario_avg.get) if scenario_avg else None
    worst_score = scenario_avg.get(worst) if worst else None
    med_latency = median(latencies)
    latency_eff = min(1.0, fastest / med_latency) if fastest and med_latency else (1.0 if generated else 0.0)
    gen_rate = rate(len(generated), total)
    strict_rate, safety_rate = rate(strict,len(generated)), rate(safety,len(generated))
    valid_rate = rate(valid,len(generated))
    contradiction_rate, unsupported_rate = rate(contrad,len(generated)), rate(unsupported,len(generated))
    score_quality, worst_quality = (mean(scores) or 0)/100, (worst_score or 0)/100
    index = 100*(.28*strict_rate + .22*safety_rate + .20*score_quality + .12*worst_quality + .10*gen_rate + .08*latency_eff)
    index -= 100*(.18*contradiction_rate + .14*unsupported_rate + .12*(1-gen_rate))
    return {
        "model": model, "runCount": total,
        "scenarioCount": len({str(x.get("scenarioId")) for x in rows if x.get("scenarioId")}),
        "generationSuccessRate": gen_rate, "widgetDisplayRate": gen_rate,
        "strictAcceptanceRate": strict_rate, "acceptedWithReviewCount": review,
        "validatorPassRate": valid_rate, "validationFailureRate": round(1-valid_rate,4) if generated else 1.0,
        "safetyPassRate": safety_rate, "overallPassRate": rate(overall,len(generated)),
        "retryRate": rate(retry,len(generated)), "contradictionRunRate": contradiction_rate,
        "unsupportedFactRunRate": unsupported_rate,
        "totalContradictions": sum(int(x.get("contradictionCount") or 0) for x in generated),
        "totalUnsupportedFacts": sum(int(x.get("unsupportedFactCount") or 0) for x in generated),
        "averageScore": mean(scores), "medianScore": median(scores),
        "minimumScore": min(scores) if scores else None, "maximumScore": max(scores) if scores else None,
        "scoreStandardDeviation": stdev(scores), "worstScenario": worst,
        "worstScenarioAverageScore": worst_score, "averageLatencySeconds": mean(latencies),
        "medianLatencySeconds": med_latency, "selectionIndex": round(max(0,index),3),
        "scenarioAverageScores": scenario_avg,
    }


def build_report(root: Path) -> dict[str, Any]:
    rows = collect(root)
    grouped = defaultdict(list)
    for row in rows: grouped[str(row.get("model") or row.get("modelId") or "unknown")].append(row)
    medians = []
    for items in grouped.values():
        vals = [v for x in items if (v := number(x.get("elapsedSeconds"))) is not None]
        if vals: medians.append(statistics.median(vals))
    fastest = min(medians) if medians else None
    models = [model_summary(k,v,fastest) for k,v in grouped.items()]
    models.sort(key=lambda x: (x["selectionIndex"], x["strictAcceptanceRate"], x["averageScore"] or -1), reverse=True)
    return {
        "schemaVersion": "kgen-model-selection-report-v2", "informationalOnly": True,
        "rankingPolicy": {
            "weights": {"strictAcceptanceRate":.28,"safetyPassRate":.22,"averageScore":.20,"worstScenarioScore":.12,"generationSuccessRate":.10,"latencyEfficiency":.08},
            "penalties": {"contradictionRunRate":.18,"unsupportedFactRunRate":.14,"generationFailureRate":.12},
        },
        "runCount": len(rows), "modelCount": len(models),
        "recommendedModelByIndex": models[0]["model"] if models else None,
        "models": models, "runs": rows,
    }


def write_csv(path: Path, models: list[dict[str, Any]]) -> None:
    columns = ["model","selectionIndex","runCount","scenarioCount","generationSuccessRate","strictAcceptanceRate","validatorPassRate","safetyPassRate","overallPassRate","retryRate","contradictionRunRate","unsupportedFactRunRate","averageScore","medianScore","minimumScore","maximumScore","worstScenario","worstScenarioAverageScore","scoreStandardDeviation","averageLatencySeconds","medianLatencySeconds"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader()
        for item in models: writer.writerow({key:item.get(key) for key in columns})


def write_html(path: Path, report: dict[str, Any]) -> None:
    rows = []
    for rank,item in enumerate(report["models"],1):
        rows.append("<tr>" + "".join([
            f"<td>{rank}</td>", f"<td>{html.escape(item['model'])}</td>",
            f"<td>{item['selectionIndex']:.1f}</td>", f"<td>{100*item['generationSuccessRate']:.1f}%</td>",
            f"<td>{100*item['strictAcceptanceRate']:.1f}%</td>", f"<td>{100*item['safetyPassRate']:.1f}%</td>",
            f"<td>{item['averageScore'] if item['averageScore'] is not None else '--'}</td>",
            f"<td>{item['worstScenarioAverageScore'] if item['worstScenarioAverageScore'] is not None else '--'}</td>",
            f"<td>{item['medianLatencySeconds'] if item['medianLatencySeconds'] is not None else '--'}</td>",
            f"<td>{100*item['contradictionRunRate']:.1f}%</td>", f"<td>{100*item['unsupportedFactRunRate']:.1f}%</td>",
        ]) + "</tr>")
    path.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><title>KGEN Model Report</title><style>body{{font-family:Arial;margin:28px;color:#172033}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d8deea;padding:8px}}th{{background:#f1f4f9}}.note{{border-left:4px solid #4568dc;background:#f6f8fc;padding:12px}}</style></head><body><h1>KGEN Medical Model Selection Report</h1><p class="note">Informational comparison. SLM generation is displayed independently from validator outcome.</p><p><b>Recommended by index:</b> {html.escape(str(report.get("recommendedModelByIndex") or "--"))}</p><table><thead><tr><th>Rank</th><th>Model</th><th>Index</th><th>Generation</th><th>Strict acceptance</th><th>Safety</th><th>Average score</th><th>Worst scenario</th><th>Median latency</th><th>Contradictions</th><th>Unsupported facts</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>""", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output-root", required=True); args=parser.parse_args()
    root=Path(args.output_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    report=build_report(root)
    (root/"model_selection_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    write_csv(root/"model_selection_report.csv", report["models"])
    write_html(root/"model_selection_report.html", report)
    print(json.dumps({"recommendedModelByIndex":report.get("recommendedModelByIndex"),"runCount":report["runCount"]},indent=2))


if __name__ == "__main__": main()
