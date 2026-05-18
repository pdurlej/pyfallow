from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/model-loop/run.py"


def load_model_loop() -> ModuleType:
    spec = importlib.util.spec_from_file_location("model_loop_run", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_matrix_summary_has_tasks_models_and_no_public_pr_automation() -> None:
    model_loop = load_model_loop()

    tasks = model_loop.load_tasks(model_loop.DEFAULT_TASKS)
    models = model_loop.load_models(model_loop.DEFAULT_MODELS)
    prompts = model_loop.load_prompt_variants(model_loop.DEFAULT_PROMPTS)
    summary = model_loop.matrix_summary(tasks, models, prompts)

    assert len(summary["tasks"]) >= 3
    assert {"model_only", "model_plus_fallow"} <= set(summary["conditions"])
    assert summary["planned_attempts"] == len(tasks) * len(models) * len(prompts)
    assert summary["public_pr_automation"] == "disabled"
    assert all(task["public_pr_allowed"] is False for task in summary["tasks"])


def test_plan_writes_prompt_without_secret_values(tmp_path: Path) -> None:
    model_loop = load_model_loop()
    output_dir = tmp_path / "plan"

    exit_code = model_loop.main(
        [
            "plan",
            "--task",
            "fixture-missing-runtime-dependency",
            "--model",
            "gpt-55-mid",
            "--prompt",
            "with-fallow-feedback",
            "--condition",
            "model_plus_fallow",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    plan = json.loads((output_dir / "plan.json").read_text(encoding="utf-8"))
    prompt = (output_dir / "prompt.md").read_text(encoding="utf-8")
    assert plan["condition"] == "model_plus_fallow"
    assert plan["public_pr_automation"] is False
    assert "Do not include provider keys" in plan["secret_policy"]
    assert "access secrets" in prompt


def test_record_template_validates_after_required_fields_are_filled(tmp_path: Path) -> None:
    model_loop = load_model_loop()
    record_path = tmp_path / "record.json"

    assert (
        model_loop.main(
            [
                "record-template",
                "--task",
                "fixture-missing-runtime-dependency",
                "--model",
                "gpt-55-mid",
                "--prompt",
                "with-fallow-feedback",
                "--condition",
                "model_plus_fallow",
                "--output",
                str(record_path),
            ]
        )
        == 0
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["decision"] = "safe_no_pr"
    record["intervention_label"] = "model_patch_codex_review_only"
    record["metrics"]["iteration_count"] = 1
    record_path.write_text(json.dumps(record), encoding="utf-8")

    assert model_loop.main(["validate", str(record_path)]) == 0


def test_validate_rejects_invalid_decision() -> None:
    model_loop = load_model_loop()
    records = model_loop.load_run_records([model_loop.DEFAULT_RECORDS])
    records[0]["decision"] = "ship_it_anyway"

    errors = model_loop.validate_records(records)

    assert any("invalid decision" in error for error in errors)


def test_aggregate_run_records_outputs_denominator_and_group_table(tmp_path: Path) -> None:
    model_loop = load_model_loop()
    output = tmp_path / "summary.md"
    json_output = tmp_path / "summary.json"

    assert (
        model_loop.main(
            [
                "aggregate",
                "--records",
                str(model_loop.DEFAULT_RECORDS),
                "--output",
                str(output),
                "--json-output",
                str(json_output),
            ]
        )
        == 0
    )

    markdown = output.read_text(encoding="utf-8")
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema"] == "fallow_py_model_loop_summary.v1"
    assert payload["record_count"] == 2
    assert payload["attempt_denominator"] == 2
    assert payload["by_condition"] == {"model_only": 1, "model_plus_fallow": 1}
    assert "internal evidence plumbing" in markdown
    assert "| Condition | Model | Prompt |" in markdown

