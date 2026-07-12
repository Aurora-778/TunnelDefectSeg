"""Prepare lightweight web-facing manifest artifacts."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent
from scripts.report_paths import report_path


class WebAgent(BaseAgent):
    """Summarize generated files for later Web Dashboard integration."""

    name = "web"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        manifest_path = self.resolve_path(context, inputs.get("manifest_path", "outputs/orchestrator_web_manifest.md"))
        memory_output = context.get("outputs", {}).get("memory", {})
        association_output = context.get("outputs", {}).get("association", {})
        memory_path = memory_output.get("disease_memory_bank_path") or inputs.get("memory_bank", "")
        association_path = association_output.get("association_records_path") or inputs.get("association_records", "")
        project_root = self.project_root(context)

        lines = [
            "## Web Dashboard 可接入文件",
            "",
            f"- Disease Memory Bank：`{report_path(memory_path, project_root)}`",
            f"- Association Records：`{report_path(association_path, project_root)}`",
            f"- Memory rows：{memory_output.get('memory_bank_rows', 0)}",
            f"- Association rows：{association_output.get('association_rows', 0)}",
            "",
            "本阶段不修改 `web_app.py` 和 `web_demo/`，只生成前端后续可读取的结构说明。",
        ]
        self.write_markdown(manifest_path, "Orchestrator Web Manifest", lines)

        return {"web_manifest_path": str(manifest_path)}
