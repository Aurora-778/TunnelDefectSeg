"""Generate a concise orchestration summary document."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class DocAgent(BaseAgent):
    """Document the v1 pipeline output without editing README."""

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        summary_path = self.outputs_dir / "orchestrator_v1_summary.md"
        docs_path = self.project_root / "docs" / "orchestrator_v1_summary.md"

        lines = [
            "## Pipeline",
            "",
            "1. MemoryAgent：从工程报告和增长分析生成病害对象记忆库。",
            "2. AssociationAgent：把机器人帧记录与病害记忆对象按 disease_id 关联。",
            "3. WebAgent：输出后续 Web Dashboard 可读取的结构说明。",
            "4. DocAgent：生成本摘要，方便检查和交接。",
            "",
            "## Outputs",
            "",
            f"- `{context.get('memory_bank_path', '')}`",
            f"- `{context.get('association_records_path', '')}`",
            f"- `{context.get('web_manifest_path', '')}`",
            "",
            "## Boundary",
            "",
            "该 Orchestrator v1 只做规则化 CSV 处理和工程编排，不训练模型、不下载数据、不接入 DINOv2/SAM/WinCLIP 等重模型。",
        ]
        self.write_markdown(summary_path, "Multi-Agent Orchestrator v1 Summary", lines)
        self.write_markdown(docs_path, "Multi-Agent Orchestrator v1 Summary", lines)

        return {
            "doc_summary_path": str(summary_path),
            "doc_project_summary_path": str(docs_path),
        }
