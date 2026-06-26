"""Prepare lightweight web-facing manifest artifacts."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class WebAgent(BaseAgent):
    """Summarize generated files for later Web Dashboard integration."""

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        manifest_path = self.outputs_dir / "orchestrator_web_manifest.md"
        memory_path = context.get("memory_bank_path", self.data_dir / "disease_memory_bank.csv")
        association_path = context.get("association_records_path", self.data_dir / "association_records.csv")

        lines = [
            "## Web Dashboard 可接入文件",
            "",
            f"- Disease Memory Bank：`{memory_path}`",
            f"- Association Records：`{association_path}`",
            f"- Memory rows：{context.get('memory_bank_rows', 0)}",
            f"- Association rows：{context.get('association_rows', 0)}",
            "",
            "本阶段不修改 `web_app.py` 和 `web_demo/`，只生成前端后续可读取的结构说明。",
        ]
        self.write_markdown(manifest_path, "Orchestrator Web Manifest", lines)

        return {"web_manifest_path": str(manifest_path)}
