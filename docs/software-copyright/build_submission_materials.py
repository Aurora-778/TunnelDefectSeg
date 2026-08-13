"""Build upload-ready software-copyright identification-material PDFs.

The builder intentionally creates two separate artifacts:

* ``程序鉴别材料.pdf``: 60 visual source-code pages (front/back 30 pages)
  from self-developed core files only.
* ``文档鉴别材料.pdf``: the complete user manual, with a boundary-safe
  process figure, when it is shorter than 60 pages.

This is a material-preparation helper, not part of the registered software.
It never copies datasets, model weights, logs, caches, third-party source, or
machine-specific absolute paths into the generated PDFs.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image as ReportLabImage,
    PageTemplate,
    PageBreak,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path(__file__).resolve().parent / "submission"
SOFTWARE_NAME = "机器人隧道巡检病害时空分析与复检管理系统"
REGISTRATION_TECHNICAL_FEATURES = (
    "人工智能辅助分析软件，支持单sequence、已有mask的离线校验、病害特征提取、"
    "路线关联和复检提示；在隔离临时沙箱内形成离线工程原型处理闭环，本地Web仅展示已生成结果。"
)

PROGRAM_PDF = OUTPUT_DIR / "程序鉴别材料.pdf"
DOCUMENT_PDF = OUTPUT_DIR / "文档鉴别材料.pdf"
MANIFEST = OUTPUT_DIR / "上传清单.md"

# Keep this list deliberately focused. It contains self-developed modules that
# demonstrate the input contract, route analysis, claim guard, controlled
# workflow boundary, video-demo path, and local Web result view. It excludes
# third-party source, model code/weights, datasets, logs, and generated files.
SOURCE_FILES = [
    "run.py",
    "scripts/extract_kict_mask_features.py",
    "scripts/merge_kict_with_simulation.py",
    "scripts/generate_engineering_report.py",
    "scripts/analyze_disease_growth.py",
    "robot_sequence.py",
    "spatiotemporal_monitoring.py",
    "robot_inspection_report.py",
    "orchestrator/agents/memory_agent.py",
    "orchestrator/agents/association_agent.py",
    "scripts/generate_visualization_and_recheck_list.py",
    "scripts/prepare_real_inspection_pilot.py",
    "scripts/run_inspection_workflow.py",
    "orchestrator/inspection_workflow/contracts.py",
    "orchestrator/inspection_workflow/controller.py",
    "orchestrator/inspection_workflow/lifecycle.py",
    "web_app.py",
]

DOCUMENT_SOURCE = ROOT / "docs/software-copyright/user-manual-draft.md"
DOCUMENT_LABEL = "用户使用说明书"


def register_fonts() -> tuple[str, str]:
    """Register a Chinese-capable font and return body/code font names."""

    font_path = Path("C:/Windows/Fonts/simhei.ttf")
    if font_path.exists():
        try:
            pdfmetrics.registerFont(TTFont("SoftCopyrightChinese", str(font_path)))
            return "SoftCopyrightChinese", "SoftCopyrightChinese"
        except Exception:
            pass
    # ReportLab's CID font is a portable fallback when a Windows TTF is absent.
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "STSong-Light"


BODY_FONT, CODE_FONT = register_fonts()


def sanitize_source_text(text: str) -> str:
    """Remove machine-specific environment paths from the submitted copy."""

    text = text.replace("\t", "    ")
    text = text.replace("D:/users/anaconda3/envs/segformer-phase2", "<python-environment>")
    text = re.sub(r"(?i)[A-Z]:[\\/](?:Users|home)[^\"'\s,;)]+", "<local-path>", text)
    return text.rstrip("\r\n")


def wrap_for_width(text: str, *, max_width: float, font_size: float) -> list[str]:
    """Wrap a source line without dropping any characters."""

    if not text:
        return [""]
    chunks: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and pdfmetrics.stringWidth(candidate, CODE_FONT, font_size) > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current or not chunks:
        chunks.append(current)
    return chunks


def source_visual_lines() -> tuple[list[str], dict[str, int]]:
    """Return wrapped source lines and per-file logical line counts."""

    font_size = 6.2
    max_width = A4[0] - 28 * mm
    visual: list[str] = []
    counts: dict[str, int] = {}
    for relative in SOURCE_FILES:
        path = ROOT / relative
        if not path.exists():
            raise FileNotFoundError(f"source candidate does not exist: {relative}")
        lines = path.read_text(encoding="utf-8").splitlines()
        counts[relative] = len(lines)
        visual.append(f"# --- {relative} ---")
        for line_number, raw in enumerate(lines, start=1):
            text = sanitize_source_text(raw)
            segments = wrap_for_width(
                f"{line_number:4d}  {text}",
                max_width=max_width,
                font_size=font_size,
            )
            visual.append(segments[0])
            visual.extend(f"       {segment}" for segment in segments[1:])
    return visual, counts


def draw_source_page(
    pdf: canvas.Canvas,
    page_lines: Iterable[str],
    page_number: int,
    section: str,
    *,
    first_page: bool,
) -> None:
    width, height = A4
    margin_x = 14 * mm
    pdf.setTitle(f"{SOFTWARE_NAME} 程序鉴别材料")
    pdf.setAuthor("software-copyright material preparation")

    pdf.setFillColor(colors.HexColor("#163A5F"))
    pdf.setFont(BODY_FONT, 8.5)
    pdf.drawString(margin_x, height - 20 * mm, SOFTWARE_NAME)
    pdf.drawRightString(width - margin_x, height - 20 * mm, f"程序鉴别材料｜{section}")
    pdf.setStrokeColor(colors.HexColor("#9FB4C7"))
    pdf.line(margin_x, height - 22 * mm, width - margin_x, height - 22 * mm)

    if first_page:
        pdf.setFillColor(colors.HexColor("#5B2C06"))
        pdf.setFont(BODY_FONT, 6.8)
        boundary = (
            "材料边界：本地、离线工程原型；Web 仅展示已生成结果；"
            "单图推理需模型环境；不提供在线推理或生产调度服务。"
        )
        pdf.drawString(margin_x, height - 28 * mm, boundary)

    pdf.setFillColor(colors.black)
    pdf.setFont(CODE_FONT, 6.2)
    top = height - (34 if first_page else 28) * mm
    leading = 10.2
    for line in page_lines:
        pdf.drawString(margin_x, top, line)
        top -= leading

    pdf.setStrokeColor(colors.HexColor("#C8D3DC"))
    pdf.line(margin_x, 14 * mm, width - margin_x, 14 * mm)
    pdf.setFillColor(colors.HexColor("#52606D"))
    pdf.setFont(BODY_FONT, 6.8)
    pdf.drawString(
        margin_x,
        9 * mm,
        "自研核心代码节选；不含第三方源码、数据集、模型权重、日志和运行状态。",
    )
    pdf.drawRightString(width - margin_x, 9 * mm, f"第 {page_number} 页")
    pdf.showPage()


def build_program_pdf() -> tuple[int, dict[str, int]]:
    visual, counts = source_visual_lines()
    page_size = 50
    if len(visual) < page_size * 60:
        raise RuntimeError(
            f"selected source visual lines are only {len(visual)}; at least 3000 are required"
        )
    front = visual[: page_size * 30]
    back = visual[-page_size * 30 :]
    pdf = canvas.Canvas(str(PROGRAM_PDF), pagesize=A4)
    for index in range(30):
        draw_source_page(
            pdf,
            front[index * page_size : (index + 1) * page_size],
            index + 1,
            "前30页",
            first_page=index == 0,
        )
    for index in range(30):
        draw_source_page(
            pdf,
            back[index * page_size : (index + 1) * page_size],
            index + 31,
            "后30页",
            first_page=False,
        )
    pdf.save()
    return 60, counts


def is_table_line(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def parse_table(lines: list[str], start: int) -> tuple[Table, int]:
    rows: list[list[str]] = []
    index = start
    while index < len(lines) and is_table_line(lines[index]):
        line = lines[index]
        if not table_separator(line):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            rows.append(cells)
        index += 1
    if not rows:
        raise ValueError("table parser received no rows")
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    cell_style = ParagraphStyle(
        "TableCell",
        parent=getSampleStyleSheet()["BodyText"],
        fontName=BODY_FONT,
        fontSize=8.4,
        leading=12,
        wordWrap="CJK",
        spaceAfter=0,
    )
    table_data = [
        [Paragraph(escape(cell), cell_style) for cell in row] for row in normalized
    ]
    available = A4[0] - 28 * mm
    if column_count == 2:
        widths = [32 * mm, available - 32 * mm]
    else:
        widths = [available / column_count] * column_count
    table = Table(table_data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF1F7")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#163A5F")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B8C7D3")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table, index


def build_manual_figure() -> Path:
    """Create a non-deceptive local/offline process diagram for the manual."""

    from PIL import Image, ImageDraw, ImageFont

    file_descriptor, file_name = tempfile.mkstemp(prefix="softcopyright-figure-", suffix=".png")
    os.close(file_descriptor)
    figure_path = Path(file_name)
    width, height = 1800, 900
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/simhei.ttf")
    if font_path.exists():
        title_font = ImageFont.truetype(str(font_path), 42)
        box_font = ImageFont.truetype(str(font_path), 30)
        note_font = ImageFont.truetype(str(font_path), 25)
    else:
        title_font = box_font = note_font = ImageFont.load_default()

    draw.text((width // 2, 45), "本地离线工程原型处理流程示意", font=title_font, fill="#163A5F", anchor="ma")
    boxes = [
        ("单 sequence\n已有 mask\n工程元数据", "#EAF1F7"),
        ("离线数据合同\n完整性与就绪性校验", "#F4F0E6"),
        ("病害特征、报告\n复检清单与图表", "#EAF1F7"),
        ("已生成 JSON/demo\n路线级结果", "#F4F0E6"),
        ("本地 Web\n结果展示", "#EAF1F7"),
    ]
    box_w, box_h, gap = 300, 170, 42
    start_x = (width - (box_w * len(boxes) + gap * (len(boxes) - 1))) // 2
    y = 250
    for index, (label, fill) in enumerate(boxes):
        x = start_x + index * (box_w + gap)
        draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=18, fill=fill, outline="#5B7890", width=3)
        draw.multiline_text((x + box_w // 2, y + box_h // 2), label, font=box_font, fill="#1F2933", anchor="mm", align="center", spacing=8)
        if index < len(boxes) - 1:
            x1 = x + box_w + 8
            x2 = x + box_w + gap - 8
            mid = y + box_h // 2
            draw.line((x1, mid, x2, mid), fill="#5B7890", width=5)
            draw.polygon([(x2, mid), (x2 - 15, mid - 11), (x2 - 15, mid + 11)], fill="#5B7890")
    note = "异常即阻断并交由受控维护流程处理；不提供在线推理或生产调度服务。"
    draw.rounded_rectangle((180, 565, width - 180, 700), radius=16, fill="#FFF8ED", outline="#C79A55", width=3)
    draw.multiline_text((width // 2, 632), note, font=note_font, fill="#5B2C06", anchor="mm", align="center")
    image.save(figure_path)
    return figure_path


def build_document_story() -> tuple[list[object], Path | None]:
    lines = DOCUMENT_SOURCE.read_text(encoding="utf-8").splitlines()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "SoftBody",
        parent=styles["BodyText"],
        fontName=BODY_FONT,
        fontSize=10.2,
        leading=15.2,
        wordWrap="CJK",
        alignment=TA_LEFT,
        spaceAfter=5,
    )
    title = ParagraphStyle(
        "SoftTitle",
        parent=body,
        fontName=BODY_FONT,
        fontSize=18,
        leading=24,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#163A5F"),
        spaceBefore=4,
        spaceAfter=12,
    )
    h1 = ParagraphStyle(
        "SoftH1",
        parent=body,
        fontName=BODY_FONT,
        fontSize=14,
        leading=20,
        textColor=colors.HexColor("#163A5F"),
        spaceBefore=10,
        spaceAfter=6,
    )
    h2 = ParagraphStyle(
        "SoftH2",
        parent=body,
        fontName=BODY_FONT,
        fontSize=12,
        leading=17,
        textColor=colors.HexColor("#245B7A"),
        spaceBefore=8,
        spaceAfter=4,
    )
    h3 = ParagraphStyle(
        "SoftH3",
        parent=body,
        fontName=BODY_FONT,
        fontSize=10.8,
        leading=15,
        textColor=colors.HexColor("#245B7A"),
        spaceBefore=6,
        spaceAfter=3,
    )
    bullet = ParagraphStyle(
        "SoftBullet",
        parent=body,
        leftIndent=14,
        firstLineIndent=0,
        spaceAfter=3,
    )
    code = ParagraphStyle(
        "SoftCode",
        parent=body,
        fontName=CODE_FONT,
        fontSize=8.3,
        leading=11.2,
        leftIndent=10,
        rightIndent=10,
        backColor=colors.HexColor("#F4F6F8"),
        borderColor=colors.HexColor("#D3DCE4"),
        borderWidth=0.4,
        borderPadding=5,
        spaceBefore=3,
        spaceAfter=6,
        wordWrap="CJK",
    )
    note = ParagraphStyle(
        "SoftNote",
        parent=body,
        fontName=BODY_FONT,
        fontSize=8.8,
        leading=13,
        leftIndent=9,
        rightIndent=9,
        borderColor=colors.HexColor("#D6B98C"),
        borderWidth=0.5,
        borderPadding=6,
        backColor=colors.HexColor("#FFF8ED"),
        spaceBefore=3,
        spaceAfter=8,
    )

    story: list[object] = []
    figure_path: Path | None = None
    # Follow the supplied school flow: the cover has the software name plus
    # “用户使用说明书”, without a version number or running header.
    story.append(Spacer(1, 72 * mm))
    story.append(Paragraph(escape(f"{SOFTWARE_NAME} {DOCUMENT_LABEL}"), title))
    story.append(Paragraph("本地、离线工程原型用户材料", ParagraphStyle(
        "CoverSubtitle",
        parent=body,
        fontName=BODY_FONT,
        fontSize=11.5,
        leading=18,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#52606D"),
        spaceAfter=8,
    )))
    story.append(PageBreak())
    boundary = (
        "提交版边界提示：本说明书描述本地、离线工程原型。真实输入适配限于单 sequence、"
        "已有 mask 的离线数据合同；Web 仅展示已生成 JSON/demo 结果；单图推理需模型环境；"
        "发现异常即阻断并交由受控维护流程处理，CLI 不提供普通用户恢复命令；不提供在线推理或生产调度服务。"
    )
    story.append(Paragraph(escape(boundary), note))

    # The flow sheet requires at least one image/flowchart in the user manual.
    if DOCUMENT_SOURCE.name == "user-manual-draft.md":
        figure_path = build_manual_figure()
        story.append(ReportLabImage(str(figure_path), width=165 * mm, height=82.5 * mm))
        story.append(Paragraph("图 2.1  本地离线工程原型处理流程示意", ParagraphStyle(
            "FigureCaption",
            parent=body,
            fontName=BODY_FONT,
            fontSize=8.8,
            leading=13,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#52606D"),
            spaceBefore=2,
            spaceAfter=10,
        )))

    in_code = False
    code_lines: list[str] = []
    bullet_number = 0
    index = 0
    # The draft contains internal formatting notes in section 0 and a draft
    # title/metadata block. The uploadable manual starts at section 1.
    if DOCUMENT_SOURCE.name == "user-manual-draft.md":
        for candidate, value in enumerate(lines):
            if value.startswith("## 1. 软件安装准备"):
                index = candidate
                break
    while index < len(lines):
        raw = lines[index].rstrip()
        if raw.startswith("```"):
            bullet_number = 0
            if in_code:
                text = "\n".join(escape(item) for item in code_lines)
                story.append(Preformatted(text, code))
                code_lines = []
                in_code = False
            else:
                in_code = True
            index += 1
            continue
        if in_code:
            code_lines.append(raw)
            index += 1
            continue
        if not raw.strip():
            bullet_number = 0
            story.append(Spacer(1, 3))
            index += 1
            continue
        if is_table_line(raw):
            bullet_number = 0
            table, index = parse_table(lines, index)
            story.extend([table, Spacer(1, 8)])
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", raw)
        if heading:
            bullet_number = 0
            level = len(heading.group(1))
            if DOCUMENT_SOURCE.name == "user-manual-draft.md":
                style = {1: h1, 2: h1, 3: h2}.get(level, h3)
            else:
                style = title if level == 1 and not story else {1: h1, 2: h2, 3: h3}[level]
            story.append(Paragraph(escape(heading.group(2).strip()), style))
            index += 1
            continue
        bullet_match = re.match(r"^\s*[-*]\s+(.*)$", raw)
        if bullet_match:
            # Keep list content as a plain paragraph. Some ReportLab/font
            # combinations render leading list markers as empty boxes; the
            # heading hierarchy already preserves the document structure.
            story.append(Paragraph(escape(bullet_match.group(1)), body))
            index += 1
            continue
        ordered_match = re.match(r"^\s*(\d+)\.\s+(.*)$", raw)
        if ordered_match:
            bullet_number = 0
            story.append(
                Paragraph(
                    f"{ordered_match.group(1)}. {escape(ordered_match.group(2))}",
                    bullet,
                )
            )
            index += 1
            continue
        bullet_number = 0
        story.append(Paragraph(escape(raw), body))
        index += 1
    return story, figure_path


def draw_document_header_footer(pdf: canvas.Canvas, doc: BaseDocTemplate) -> None:
    width, height = A4
    margin_x = 14 * mm
    pdf.saveState()
    if doc.page == 1:
        pdf.restoreState()
        return
    pdf.setFillColor(colors.HexColor("#163A5F"))
    pdf.setFont(BODY_FONT, 8.2)
    pdf.drawString(margin_x, height - 13 * mm, SOFTWARE_NAME)
    pdf.drawRightString(width - margin_x, height - 13 * mm, f"文档鉴别材料｜第 {doc.page} 页")
    pdf.setStrokeColor(colors.HexColor("#9FB4C7"))
    pdf.line(margin_x, height - 15 * mm, width - margin_x, height - 15 * mm)
    pdf.setFillColor(colors.HexColor("#52606D"))
    pdf.setFont(BODY_FONT, 7)
    pdf.drawString(margin_x, 9 * mm, "软件功能说明书；本地、离线工程原型材料。")
    pdf.restoreState()


def build_document_pdf() -> int:
    frame = Frame(
        14 * mm,
        16 * mm,
        A4[0] - 28 * mm,
        A4[1] - 34 * mm,
        id="normal",
        leftPadding=0,
        rightPadding=0,
        topPadding=4 * mm,
        bottomPadding=4 * mm,
    )
    template = PageTemplate(id="softcopyright", frames=[frame], onPage=draw_document_header_footer)
    doc = BaseDocTemplate(
        str(DOCUMENT_PDF),
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=17 * mm,
        bottomMargin=16 * mm,
        title=f"{SOFTWARE_NAME} {DOCUMENT_LABEL}",
        author="software-copyright material preparation",
    )
    doc.addPageTemplates([template])
    story, figure_path = build_document_story()
    try:
        doc.build(story)
    finally:
        if figure_path is not None:
            figure_path.unlink(missing_ok=True)
    from pypdf import PdfReader

    return len(PdfReader(str(DOCUMENT_PDF)).pages)


def write_manifest(program_pages: int, document_pages: int, counts: dict[str, int]) -> None:
    source_count = sum(counts.values())
    lines = [
        "# 软著鉴别材料上传清单",
        "",
        f"生成日期：{date.today().isoformat()}",
        f"软件名称：{SOFTWARE_NAME}",
        "",
        "## 直接上传",
        "",
        f"1. `程序鉴别材料.pdf`：{program_pages} 页，源程序前 30 页 + 后 30 页。",
        f"2. `文档鉴别材料.pdf`：{document_pages} 页，《用户使用说明书》全文，含图 2.1 流程图（不足 60 页时提交全文）。",
        "",
        "`办理流程对照清单.md` 仅供办理时核对，不上传到 R11。",
        "",
        "## 程序材料范围",
        "",
        f"本次节选文件的自研源程序逻辑行数合计约 {source_count} 行；PDF 仅截取 60 个可读代码页。",
        "未包含第三方源码、数据集、模型权重、缓存、日志、运行状态或机器绝对路径。",
        "",
    ]
    lines.extend(f"- `{path}`：{count} 行" for path, count in counts.items())
    lines.extend(
        [
            "",
            "## 上传前检查",
            "",
            "- 表单选择“一般交存”，没有机密代码时不要选择“例外交存”。",
            "- 两个 PDF 均不加密码，检查浏览器预览能正常显示中文和代码。",
            f"- R11‘软件的技术特点’短摘要（100字内）：{REGISTRATION_TECHNICAL_FEATURES}",
            "- 说明书中的边界保持为单 sequence、已有 mask、离线、已生成 JSON/demo 结果、隔离临时沙箱；不表述为真实现场闭环、在线推理或生产调度服务。",
        ]
    )
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    program_pages, counts = build_program_pdf()
    document_pages = build_document_pdf()
    write_manifest(program_pages, document_pages, counts)
    print(f"program: {PROGRAM_PDF} ({program_pages} pages)")
    print(f"document: {DOCUMENT_PDF} ({document_pages} pages)")
    print(f"manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
