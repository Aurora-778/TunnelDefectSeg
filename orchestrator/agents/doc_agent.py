"""Generate a concise orchestration summary document."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class DocAgent(BaseAgent):
    """Document the v1 pipeline output without editing README."""

    name = "doc"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        summary_path = self.resolve_path(context, inputs.get("summary_path", "outputs/orchestrator_v1_summary.md"))
        docs_path = self.resolve_path(context, inputs.get("docs_path", "docs/orchestrator_v1_summary.md"))
        memory_output = context.get("outputs", {}).get("memory", {})
        association_output = context.get("outputs", {}).get("association", {})
        web_output = context.get("outputs", {}).get("web", {})

        lines = [
            "## Pipeline",
            "",
            "1. MemoryAgent：从配置输入读取工程报告和增长分析，生成病害对象记忆库。",
            "2. AssociationAgent：从配置输入读取机器人帧记录，并与病害记忆对象关联。",
            "3. WebAgent：输出后续 Web Dashboard 可读取的结构说明。",
            "4. DocAgent：生成本摘要，方便检查和交接。",
            "",
            "## Outputs",
            "",
            f"- `{memory_output.get('disease_memory_bank_path', '')}`",
            f"- `{association_output.get('association_records_path', '')}`",
            f"- `{web_output.get('web_manifest_path', '')}`",
            "",
            "## Boundary",
            "",
            "Orchestrator 只负责 pipeline、registry、context 和 logging；业务文件路径由 `config/pipeline.yaml` 提供。",
        ]
        self.write_markdown(summary_path, "Generic Multi-Agent Framework Summary", lines)
        self.write_markdown(docs_path, "Generic Multi-Agent Framework Summary", lines)

        result = {
            "doc_summary_path": str(summary_path),
            "doc_project_summary_path": str(docs_path),
        }
        return result
