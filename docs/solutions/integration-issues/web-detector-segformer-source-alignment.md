---
title: Web Detector SegFormer Source Alignment
date: 2026-06-11
category: docs/solutions/integration-issues
module: Web live detector
problem_type: integration_issue
component: tooling
symptoms:
  - "Static Web sample showed SegFormer probability TTA while drag-and-drop uploads could still run the legacy source"
  - "The Web launcher used default python even though SegFormer inference requires the segformer-phase2 environment"
root_cause: missing_workflow_step
resolution_type: workflow_improvement
severity: high
related_components:
  - run_web_app.ps1
  - web_app.py
  - run_confidence_risk.py
tags:
  - segformer
  - web-detector
  - model-source
  - windows-launcher
---

# Web Detector SegFormer Source Alignment

## Problem

The Web demo homepage was updated to show SegFormer probability TTA artifacts, but the live drag-and-drop path could still load the legacy model. That made the first screen and the uploaded-image workflow describe different model sources.

## Symptoms

- `web_demo/index.html` showed `probability_tta: true` and `tta_specs: ["segformer_identity", "segformer_hflip"]`.
- `web_app.py` called `load_model()` with no arguments, so it inherited `model_source="legacy"` from `run_confidence_risk.py`.
- `run_web_app.ps1` defaulted to plain `python`, despite the SegFormer runtime requiring `D:/users/anaconda3/envs/segformer-phase2/python.exe`.
- The user-facing promise "drag an image in and see the SegFormer result" was not guaranteed by the server path.

## What Didn't Work

- Updating only the static Web assets fixed the homepage but not the upload endpoint.
- Testing only `run_confidence_risk.py --model-source segformer` proved batch inference worked, but did not prove `web_app.py` passed the same source into `load_model()`.
- Checking image files in `web_demo/assets` proved the screenshots were same-source, but not the runtime upload path.

## Solution

Make the Web server own an explicit model-source configuration and have the Windows launcher pass it through.

The fixed path is:

```text
run_web_app.bat
  -> run_web_app.ps1
  -> D:/users/anaconda3/envs/segformer-phase2/python.exe web_app.py --model-source segformer ...
  -> web_app._load_model_once()
  -> load_model(model_source="segformer", ...)
```

The important implementation details are:

- `web_app.py` defines `WebModelConfig` with `model_source="segformer"` as the default.
- `_load_model_once()` passes `model_source`, SegFormer config, checkpoint, repo root, and device into `load_model()`.
- `/api/health` returns `model_source` and `segformer_device`, so a running server can be checked without uploading an image.
- `run_web_app.ps1` defaults to `D:/users/anaconda3/envs/segformer-phase2/python.exe` and passes `--model-source segformer`.
- README startup instructions use the same SegFormer runtime command.

Keep an explicit legacy escape hatch for debugging:

```powershell
.\run_web_app.ps1 -ModelSource legacy -PythonExe python
```

## Why This Works

The bug was a missing workflow step between the batch inference path and the live Web path. SegFormer probability TTA was implemented in `run_confidence_risk.py` and `segformer_inference_adapter.py`, but Web uploads only receive that behavior if `web_app.py` passes `model_source="segformer"` to `load_model()`.

Using the SegFormer Python executable in the launcher also matters because the old mmseg/SegFormer stack depends on the `segformer-phase2` environment. A correct model-source flag is not enough if the process starts under an environment that cannot import `mmseg`, `mmcv-full`, or the patched SegFormer source tree.

## Prevention

- Add a unit test that `WebModelConfig()` defaults to `segformer`.
- Add a unit test that `_load_model_once()` forwards `model_source` and all SegFormer paths to `load_model()`.
- Add a launcher contract test that `run_web_app.ps1` contains the SegFormer Python path and passes `--model-source $ModelSource`.
- For manual smoke tests, start the app on a temporary port and check:

```powershell
Invoke-RestMethod http://127.0.0.1:18080/api/health
```

The response should include:

```json
{"model_source":"segformer","segformer_device":"cuda:0"}
```

## Related Issues

- [Windows double-click SegFormer CUDA training launcher](../developer-experience/windows-double-click-segformer-cuda-training.md)
- `AGENTS.md` known pitfall: do not rely on default `python` for SegFormer launchers.
