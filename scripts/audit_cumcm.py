#!/usr/bin/env python3
"""Audit a CUMCM LaTeX project and write a prioritized Markdown report."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SEVERITY_ORDER = {
    "取消资格风险": 0,
    "硬错误": 1,
    "质量警告": 2,
    "优化建议": 3,
}


@dataclass(frozen=True)
class SourceFile:
    path: Path
    text: str


@dataclass(frozen=True)
class Issue:
    severity: str
    title: str
    evidence: str
    fix: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="审查 CUMCM LaTeX 论文的格式、引用、匿名、AI 披露与质量风险。"
    )
    parser.add_argument("main_tex", type=Path, help="论文入口 document.tex 或 main.tex")
    parser.add_argument("--pdf", type=Path, help="已编译的论文 PDF")
    parser.add_argument("--ai-manifest", type=Path, help="AI 使用记录 JSON")
    parser.add_argument("--results", type=Path, help="结果声明 JSON")
    parser.add_argument("--support-archive", type=Path, help="支撑材料 RAR/ZIP")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/PAPER_QUALITY_REPORT.md"),
        help="Markdown 报告路径",
    )
    return parser.parse_args()


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def evidence_at(source: SourceFile, offset: int, excerpt: str = "") -> str:
    line = line_number(source.text, offset)
    suffix = f"：`{excerpt.strip()}`" if excerpt.strip() else ""
    return f"{source.path}:{line}{suffix}"


def load_sources(main_tex: Path) -> tuple[list[SourceFile], list[Issue]]:
    issues: list[Issue] = []
    sources: list[SourceFile] = []
    visited: set[Path] = set()

    def visit(path: Path) -> None:
        resolved = path.resolve()
        if resolved in visited:
            return
        visited.add(resolved)
        if not resolved.is_file():
            issues.append(
                Issue(
                    "硬错误",
                    "被包含的 LaTeX 文件不存在",
                    str(resolved),
                    "修正 \\input/\\include 路径或补齐文件。",
                )
            )
            return
        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(
                Issue(
                    "硬错误",
                    "LaTeX 文件不是 UTF-8",
                    str(resolved),
                    "将源文件无损转换为 UTF-8 后重新编译。",
                )
            )
            return
        source = SourceFile(resolved, text)
        sources.append(source)
        for match in re.finditer(r"\\(?:input|include)\{([^}]+)\}", text):
            child = Path(match.group(1))
            if child.suffix == "":
                child = child.with_suffix(".tex")
            visit((resolved.parent / child).resolve())

    visit(main_tex)
    for style_name in ("cumcmthesis.cls", "cumcm-paper.sty"):
        style_path = main_tex.parent / style_name
        if style_path.is_file():
            try:
                sources.append(
                    SourceFile(style_path.resolve(), style_path.read_text(encoding="utf-8"))
                )
            except UnicodeDecodeError:
                issues.append(
                    Issue(
                        "硬错误",
                        "样式文件不是 UTF-8",
                        str(style_path),
                        "将样式文件转换为 UTF-8。",
                    )
                )
    return sources, issues


def combined_text(sources: Iterable[SourceFile]) -> str:
    return "\n".join(source.text for source in sources)


def find_first(sources: Iterable[SourceFile], pattern: str, flags: int = 0) -> tuple[SourceFile, re.Match[str]] | None:
    regex = re.compile(pattern, flags)
    for source in sources:
        match = regex.search(source.text)
        if match:
            return source, match
    return None


def add_pattern_issue(
    issues: list[Issue],
    sources: list[SourceFile],
    pattern: str,
    severity: str,
    title: str,
    fix: str,
    flags: int = 0,
) -> None:
    found = find_first(sources, pattern, flags)
    if found:
        source, match = found
        issues.append(
            Issue(
                severity,
                title,
                evidence_at(source, match.start(), match.group(0)),
                fix,
            )
        )


def resolve_asset(main_dir: Path, raw: str, extra_dirs: tuple[Path, ...] = ()) -> Path | None:
    for root in (main_dir, *extra_dirs):
        candidate = (root / raw).resolve()
        if candidate.is_file():
            return candidate
        if candidate.suffix == "":
            for suffix in (".pdf", ".png", ".jpg", ".jpeg", ".eps"):
                with_suffix = candidate.with_suffix(suffix)
                if with_suffix.is_file():
                    return with_suffix
    return None


def check_assets(main_tex: Path, sources: list[SourceFile], issues: list[Issue]) -> None:
    patterns = (
        (r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", "图片"),
        (r"\\lstinputlisting(?:\[[^\]]*\])?\{([^}]+)\}", "代码文件"),
    )
    all_text = combined_text(sources)
    graphic_dirs: list[Path] = []
    for declaration in re.findall(r"\\graphicspath\{((?:\{[^}]+\})+)\}", all_text):
        for raw_dir in re.findall(r"\{([^}]+)\}", declaration):
            graphic_dirs.append((main_tex.parent / raw_dir).resolve())
    for source in sources:
        search_dirs = (source.path.parent.resolve(), *graphic_dirs)
        for pattern, kind in patterns:
            for match in re.finditer(pattern, source.text):
                raw = match.group(1)
                asset = resolve_asset(main_tex.parent, raw, search_dirs)
                if asset is None:
                    issues.append(
                        Issue(
                            "硬错误",
                            f"{kind}不存在",
                            evidence_at(source, match.start(), raw),
                            f"修正路径或补齐{kind}，再重新编译。",
                        )
                    )
                elif kind == "图片" and asset.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                    try:
                        from PIL import Image
                        with Image.open(asset) as raster:
                            width_px, height_px = raster.size
                        if width_px < 1200 or height_px < 600:
                            issues.append(
                                Issue(
                                    "质量警告",
                                    "位图分辨率可能不足",
                                    f"{asset}：{width_px} × {height_px} px",
                                    "优先换成矢量 PDF；位图在最终尺寸下建议达到 300 dpi，并逐页确认文字和线条清晰。",
                                )
                            )
                    except (ImportError, OSError):
                        pass
                elif kind == "代码文件":
                    code = asset.read_text(encoding="utf-8", errors="replace")
                    placeholder_code = re.search(
                        r"NotImplementedError|Replace this placeholder|TODO\s*:\s*replace",
                        code,
                        re.I,
                    )
                    if placeholder_code or not code.strip():
                        issues.append(
                            Issue(
                                "硬错误",
                                "附录代码仍为占位程序",
                                str(asset),
                                "替换为完整、可运行且与论文结果一致的源程序。",
                            )
                        )
                    elif asset.suffix.lower() == ".py":
                        try:
                            tree = ast.parse(code, filename=str(asset))
                        except SyntaxError as exc:
                            issues.append(
                                Issue(
                                    "硬错误",
                                    "附录 Python 代码存在语法错误",
                                    f"{asset}:{exc.lineno}：{exc.msg}",
                                    "修正语法并实际运行代码，确认可复现正文结果。",
                                )
                            )
                        else:
                            executable = [
                                node
                                for node in tree.body
                                if not isinstance(node, (ast.Expr, ast.Pass))
                                or not (
                                    isinstance(node, ast.Expr)
                                    and isinstance(node.value, ast.Constant)
                                    and isinstance(node.value.value, str)
                                )
                            ]
                            if not executable:
                                issues.append(
                                    Issue(
                                        "硬错误",
                                        "附录 Python 代码没有可执行内容",
                                        str(asset),
                                        "补充完整入口、数据处理、求解和结果输出代码。",
                                    )
                                )


ACADEMIC_BIB_TYPES = {
    "article",
    "book",
    "inbook",
    "incollection",
    "inproceedings",
    "conference",
    "proceedings",
    "mastersthesis",
    "phdthesis",
}


def parse_bib_entries(text: str) -> dict[str, tuple[str, str]]:
    """Return key -> (entry type, full entry text) without requiring a BibTeX parser."""
    entries: dict[str, tuple[str, str]] = {}
    start_pattern = re.compile(r"@([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,")
    position = 0
    while match := start_pattern.search(text, position):
        depth = 1
        cursor = match.end()
        while cursor < len(text) and depth:
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
            cursor += 1
        entries[match.group(2)] = (match.group(1).lower(), text[match.start() : cursor])
        position = max(cursor, match.end())
    return entries


def check_references(main_tex: Path, sources: list[SourceFile], issues: list[Issue]) -> None:
    text = combined_text(sources)
    labels = set(re.findall(r"\\label\{([^}]+)\}", text))
    refs = set(re.findall(r"\\(?:ref|eqref|cref|Cref|autoref)\{([^}]+)\}", text))
    for missing in sorted(refs - labels):
        issues.append(
            Issue(
                "硬错误",
                "交叉引用没有对应标签",
                missing,
                "补充对应 \\label 或修正引用键。",
            )
        )

    resources = re.findall(r"\\addbibresource\{([^}]+)\}", text)
    for group in re.findall(r"\\bibliography\{([^}]+)\}", text):
        resources.extend(
            item if Path(item).suffix else f"{item}.bib"
            for item in (part.strip() for part in group.split(","))
            if item
        )
    bib_entries: dict[str, tuple[str, str]] = {}
    bibliography_sources: list[SourceFile] = []
    for source in sources:
        match = re.search(r"\\(?:bibliography|printbibliography)\b", source.text)
        if match:
            bibliography_sources.append(source)
            if not re.search(r"\\(?:clearpage|newpage)", source.text[: match.start()]):
                issues.append(
                    Issue(
                        "硬错误",
                        "参考文献没有单独起页",
                        evidence_at(source, match.start(), match.group(0)),
                        "在参考文献命令之前加入 \\clearpage，确保参考文献标题位于新页顶部。",
                    )
                )
    if not bibliography_sources:
        issues.append(
            Issue(
                "硬错误",
                "未检测到参考文献输出命令",
                str(main_tex),
                "使用 BibTeX/GB/T 7714 数字顺序制输出真实参考文献。",
            )
        )

    for raw in resources:
        bib = (main_tex.parent / raw).resolve()
        if not bib.is_file():
            issues.append(
                Issue("硬错误", "参考文献数据库不存在", str(bib), "补齐 .bib 文件或修正路径。")
            )
            continue
        bib_text = bib.read_text(encoding="utf-8", errors="replace")
        bib_entries.update(parse_bib_entries(bib_text))
        if re.search(
            r"\[(?:AI\s*工具|YYYY|请|版本|开发机构|中文作者|English Author|"
            r"与论文|期刊名称|年份|卷|期|页码|Relevant|Journal|Year|Volume|Number|Pages)",
            bib_text,
        ):
            issues.append(
                Issue(
                    "硬错误",
                    "参考文献仍含占位内容",
                    str(bib),
                    "替换为真实、可核验的文献与 AI 工具信息。",
                )
            )

    citation_keys: set[str] = set()
    for group in re.findall(
        r"\\(?:cite|citep|citet|parencite|textcite|autocite|footcite|supercite)\*?"
        r"(?:\[[^\]]*\]){0,2}\{([^}]+)\}",
        text,
    ):
        citation_keys.update(key.strip() for key in group.split(",") if key.strip())
    bib_keys = set(bib_entries)
    for missing in sorted(citation_keys - bib_keys):
        issues.append(
            Issue(
                "硬错误",
                "引用键不在参考文献数据库中",
                missing,
                "补充真实文献条目或修正引用键。",
            )
        )
    for unused in sorted(bib_keys - citation_keys):
        issues.append(
            Issue(
                "质量警告",
                "参考文献条目未在正文引用",
                unused,
                "删除无关条目或在正文首次使用相应成果处规范引用；不得用未引用条目凑数量或比例。",
            )
        )

    cited_academic = {
        key: entry
        for key, entry in bib_entries.items()
        if key in citation_keys and entry[0] in ACADEMIC_BIB_TYPES
    }
    chinese_count = sum(
        bool(re.search(r"[\u3400-\u9fff]", entry_text))
        for _, entry_text in cited_academic.values()
    )
    english_count = len(cited_academic) - chinese_count
    if not cited_academic:
        issues.append(
            Issue(
                "硬错误",
                "未检测到正文引用的学术文献",
                "中文 0 篇，英文 0 篇（AI、软件、题面、网站不计入）",
                "补充并实际引用与模型和数据直接相关的真实中英文学术文献。",
            )
        )
    elif chinese_count < english_count:
        issues.append(
            Issue(
                "硬错误",
                "中文学术文献少于英文学术文献",
                f"正文引用的学术文献：中文 {chinese_count} 篇，英文 {english_count} 篇",
                "补充真实且相关的中文学术文献，使中文数量不少于英文；不得用无关条目凑比例。",
            )
        )
    if len(bib_keys) < 3:
        issues.append(
            Issue(
                "质量警告",
                "参考文献数量偏少",
                f"检测到 {len(bib_keys)} 个条目",
                "按实际使用补充算法原始论文、数据来源和权威资料；不得为凑数虚构文献。",
            )
        )


def project_root(main_tex: Path) -> Path:
    return main_tex.parent.parent if main_tex.parent.name.lower() == "paper" else main_tex.parent


def check_writeability_reports(main_tex: Path, issues: list[Issue]) -> None:
    root = project_root(main_tex)
    writeability = root / "reports" / "MODELING_WRITEABILITY_REPORT.md"
    matrix = root / "reports" / "CLAIM_EVIDENCE_MATRIX.md"
    for path, title in (
        (writeability, "缺少建模成果可写性审查"),
        (matrix, "缺少主张—证据矩阵"),
    ):
        if not path.is_file():
            issues.append(
                Issue(
                    "硬错误",
                    title,
                    str(path),
                    "正式写作前生成并完成该报告；存在证据缺口时停止写作并询问用户。",
                )
            )
            continue
        report = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\[(?:填写|文件与位置|确认|赛题|代码输出|主程序)", report):
            issues.append(
                Issue(
                    "硬错误",
                    f"{title.removeprefix('缺少')}仍含占位内容",
                    str(path),
                    "用真实题目、结果来源、验证和边界替换全部占位内容。",
                )
            )
    if writeability.is_file():
        report = writeability.read_text(encoding="utf-8", errors="replace")
        status = re.search(r"状态\s*[:：]\s*(PASS|BLOCK)", report, re.I)
        if not status or status.group(1).upper() != "PASS":
            issues.append(
                Issue(
                    "硬错误",
                    "建模成果可写性审查未通过",
                    str(writeability),
                    "解决全部阻断项并记录依据后把状态改为 PASS；不得仅修改状态文字。",
                )
            )


def check_log(main_tex: Path, issues: list[Issue]) -> None:
    log_path = main_tex.with_suffix(".log")
    if not log_path.is_file():
        issues.append(
            Issue(
                "质量警告",
                "未找到编译日志",
                str(log_path),
                "使用 XeLaTeX→BibTeX→XeLaTeX 两遍的完整链路编译后重新运行审查。",
            )
        )
        return
    log = log_path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(undefined references|Citation .+ undefined|Reference .+ undefined)", log, re.I):
        issues.append(
            Issue(
                "硬错误",
                "编译日志包含未解析引用",
                str(log_path),
                "运行完整的 XeLaTeX/BibTeX 编译链并修正缺失标签或文献。",
            )
        )
    overfull = re.findall(r"Overfull \\[hv]box[^\n]*", log)
    if overfull:
        issues.append(
            Issue(
                "质量警告",
                "存在越出版心的内容",
                f"{log_path}：{overfull[0]}",
                "检查长公式、宽表、URL、代码和图片尺寸；不得用整体缩小正文掩盖。",
            )
        )
    if "Missing character:" in log:
        issues.append(
            Issue(
                "硬错误",
                "编译日志报告缺字",
                str(log_path),
                "替换不可用字符或选择可移植字体后重新编译。",
            )
        )


def parse_label_page(aux_path: Path, label: str) -> int | None:
    if not aux_path.is_file():
        return None
    aux = aux_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        rf"\\newlabel\{{{re.escape(label)}\}}\{{\{{[^}}]*\}}\{{(\d+)\}}",
        aux,
    )
    return int(match.group(1)) if match else None


def parse_body_page(aux_path: Path) -> int | None:
    return parse_label_page(aux_path, "cumcm:body-end")


def check_pdf(main_tex: Path, pdf_path: Path | None, issues: list[Issue]) -> None:
    if pdf_path is None:
        issues.append(
            Issue("硬错误", "未提供论文 PDF", "", "编译论文并通过 --pdf 指定最终 PDF。")
        )
        return
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        issues.append(Issue("硬错误", "论文 PDF 不存在", str(pdf_path), "完成编译并修正路径。"))
        return
    if pdf_path.stat().st_size > 20 * 1024 * 1024:
        issues.append(
            Issue(
                "取消资格风险",
                "论文 PDF 超过 20 MB",
                f"{pdf_path}：{pdf_path.stat().st_size / 1024 / 1024:.2f} MB",
                "无损压缩图片和 PDF，保持图表清晰后重新检查。",
            )
        )
    try:
        from pypdf import PdfReader
    except ImportError:
        issues.append(
            Issue(
                "质量警告",
                "缺少 pypdf，未完成 PDF 结构检查",
                "",
                "安装 pypdf 后重新运行审查。",
            )
        )
        return
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        issues.append(Issue("硬错误", "PDF 无法读取", str(exc), "重新生成有效 PDF。"))
        return
    if not reader.pages:
        issues.append(Issue("硬错误", "PDF 没有页面", str(pdf_path), "重新编译论文。"))
        return
    first = reader.pages[0]
    width = float(first.mediabox.width)
    height = float(first.mediabox.height)
    if not (590 <= width <= 600 and 837 <= height <= 847):
        issues.append(
            Issue(
                "取消资格风险",
                "PDF 页面不是 A4",
                f"第一页尺寸 {width:.1f} × {height:.1f} pt",
                "把文档类和 geometry 设置为 A4 后重新编译。",
            )
        )
    first_text = first.extract_text() or ""
    if "摘要" not in first_text or "关键词" not in first_text:
        issues.append(
            Issue(
                "取消资格风险",
                "电子版第一页不像摘要专用页",
                "第一页未同时识别到“摘要”和“关键词”",
                "确保第一页只包含题目、摘要和关键词，并从该页开始编号。",
            )
        )
    if "承诺书" in first_text or "编号专用页" in first_text or "目录" in first_text:
        issues.append(
            Issue(
                "取消资格风险",
                "电子版首页包含禁止的前置内容",
                "第一页识别到承诺书、编号专用页或目录",
                "电子版删除承诺书、编号页和目录，使第一页为摘要。",
            )
        )
    reference_page = None
    for page_number, page in enumerate(reader.pages, 1):
        page_text = page.extract_text() or ""
        heading = re.search(r"(?m)^\s*参考文献\s*$", page_text)
        if heading is None:
            continue
        reference_page = page_number
        prefix = re.sub(r"[\s\d]+", "", page_text[: heading.start()])
        if len(prefix) > 8:
            issues.append(
                Issue(
                    "硬错误",
                    "参考文献未从独立页面开始",
                    f"第 {page_number} 页在“参考文献”之前仍有正文：{prefix[:40]}",
                    "在参考文献命令前使用 \\clearpage，重新完整编译并复核该页。",
                )
            )
        break
    if reference_page is None:
        issues.append(
            Issue(
                "硬错误",
                "PDF 中未识别到参考文献页",
                str(pdf_path),
                "补充真实参考文献，完成 BibTeX 编译并确保标题可见。",
            )
        )
    abstract_end_page = parse_label_page(main_tex.with_suffix(".aux"), "cumcm:abstract-end")
    if abstract_end_page is None:
        issues.append(
            Issue(
                "硬错误",
                "无法确定摘要结束页",
                str(main_tex.with_suffix(".aux")),
                "在摘要环境后保留 \\label{cumcm:abstract-end} 并完成至少两轮编译。",
            )
        )
    elif abstract_end_page > 1:
        issues.append(
            Issue(
                "取消资格风险",
                "摘要超过一页",
                f"摘要结束标签位于第 {abstract_end_page} 页",
                "压缩背景和过程描述，保留逐问方法、关键结果、检验与关键词，使摘要完整落在第一页。",
            )
        )
    metadata = reader.metadata or {}
    author = str(metadata.get("/Author", "") or "").strip()
    if author:
        issues.append(
            Issue(
                "取消资格风险",
                "PDF 作者元数据非空",
                author,
                "清空 PDF 作者、创建者中的个人或学校信息。",
            )
        )
    body_page = parse_body_page(main_tex.with_suffix(".aux"))
    if body_page is None:
        issues.append(
            Issue(
                "硬错误",
                "无法确定正文结束页",
                str(main_tex.with_suffix(".aux")),
                "保留 \\CumcmBodyEnd 标签并完成至少两轮编译。",
            )
        )
    elif body_page > 30:
        issues.append(
            Issue(
                "取消资格风险",
                "正文超过 30 页",
                f"正文结束标签位于第 {body_page} 页",
                "压缩非核心内容，把大表和代码移入附录，正文控制在 30 页内。",
            )
        )
    if len(reader.pages) < (body_page or 1):
        issues.append(
            Issue(
                "硬错误",
                "PDF 页数与正文标签矛盾",
                f"PDF 共 {len(reader.pages)} 页，正文标签为 {body_page}",
                "清理辅助文件并重新完整编译。",
            )
        )


def is_placeholder(value: object) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return bool(re.search(r"\[(?:AI|YYYY|使用|受影响|关键|采纳|开发|版本|工具|q\d|精确|单位|结果)", text))


def check_ai_manifest(
    main_tex: Path,
    sources: list[SourceFile],
    manifest_path: Path | None,
    issues: list[Issue],
) -> None:
    text = combined_text(sources)
    if manifest_path is None or not manifest_path.is_file():
        issues.append(
            Issue(
                "取消资格风险",
                "缺少 AI 使用记录",
                str(manifest_path or ""),
                "填写 support/ai-use.json，并生成 AI工具使用详情.pdf。",
            )
        )
        return
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(Issue("硬错误", "AI 使用记录不是有效 JSON", str(exc), "修正 JSON 结构。"))
        return
    if data.get("used") is not True:
        issues.append(
            Issue(
                "取消资格风险",
                "使用本 skill 却声明未使用 AI",
                str(manifest_path),
                "把 used 设为 true，并完成正文、参考文献和详情 PDF 三处披露。",
            )
        )
    required = ("tools", "purposes", "key_interactions")
    for key in required:
        if not data.get(key):
            issues.append(
                Issue(
                    "取消资格风险",
                    f"AI 使用记录缺少 {key}",
                    str(manifest_path),
                    "补充工具、用途、关键交互、采纳和人工修改信息。",
                )
            )
    if is_placeholder(data):
        issues.append(
            Issue(
                "硬错误",
                "AI 使用记录仍含占位内容",
                str(manifest_path),
                "替换全部方括号占位信息，保持记录真实完整。",
            )
        )
    if "人工智能工具使用说明" not in text or "ai-tool" not in text:
        issues.append(
            Issue(
                "取消资格风险",
                "论文中的 AI 披露或参考文献引用缺失",
                str(main_tex),
                "在正文相应位置标注，并在参考文献及说明节列出 AI 工具。",
            )
        )
    if "未使用任何" in text:
        issues.append(
            Issue(
                "取消资格风险",
                "论文同时声称未使用 AI",
                str(main_tex),
                "删除与实际使用冲突的未使用声明。",
            )
        )
    details_pdf = manifest_path.parent / "AI工具使用详情.pdf"
    if not details_pdf.is_file():
        issues.append(
            Issue(
                "取消资格风险",
                "缺少 AI工具使用详情.pdf",
                str(details_pdf),
                "根据真实 ai-use.json 编写并编译同名 PDF，放入支撑材料。",
            )
        )


def check_results(results_path: Path | None, sources: list[SourceFile], issues: list[Issue]) -> None:
    if results_path is None or not results_path.is_file():
        issues.append(
            Issue(
                "质量警告",
                "未提供结果声明表",
                str(results_path or ""),
                "填写 support/result-claims.json，并用 --results 让关键数值可追溯核对。",
            )
        )
        return
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(Issue("硬错误", "结果声明表不是有效 JSON", str(exc), "修正 JSON 结构。"))
        return
    claims = data.get("claims") or []
    if not claims or is_placeholder(data):
        issues.append(
            Issue(
                "硬错误",
                "结果声明表为空或仍含占位内容",
                str(results_path),
                "逐项填写论文关键结果、单位及其代码/数据来源。",
            )
        )
        return
    text = combined_text(sources)
    seen: set[str] = set()
    for claim in claims:
        claim_id = str(claim.get("id", "")).strip()
        value = str(claim.get("value", "")).strip()
        source = str(claim.get("source", "")).strip()
        unit = str(claim.get("unit", "")).strip()
        if not claim_id or not value or not unit or not source:
            issues.append(
                Issue(
                    "硬错误",
                    "结果声明字段不完整",
                    json.dumps(claim, ensure_ascii=False),
                    "每项填写 id、value、unit 和 source。",
                )
            )
            continue
        if claim_id in seen:
            issues.append(Issue("硬错误", "结果声明 ID 重复", claim_id, "为每项结果使用唯一 ID。"))
        seen.add(claim_id)
        if value not in text:
            issues.append(
                Issue(
                    "硬错误",
                    "声明的关键结果未出现在论文中",
                    f"{claim_id} = {value}",
                    "核对数值、精度和单位，统一结果记录与论文。",
                )
            )
        source_path_text = re.sub(r":\d+(?:-\d+)?$", "", source.split("#", 1)[0]).strip()
        source_path = (results_path.parent / source_path_text).resolve()
        if source_path_text and not source_path.is_file():
            issues.append(
                Issue(
                    "硬错误",
                    "结果声明的来源文件不存在",
                    f"{claim_id}：{source_path}",
                    "修正 source 路径，并保留能够复现该数值的代码输出或数据表。",
                )
            )



GREEK_SYMBOL_NAMES = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta",
    "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho",
    "sigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega",
}


def math_symbol_tokens(raw: str) -> set[str]:
    """Extract conservative variable tokens from a LaTeX math fragment."""
    cleaned = re.sub(r"\\(?:label|tag|ref|eqref)\{[^{}]*\}", " ", raw)
    cleaned = re.sub(r"\\(?:text|mathrm|operatorname)\{[^{}]*\}", " ", cleaned)
    greek = {
        match.group(1)
        for match in re.finditer(r"\\([A-Za-z]+)", cleaned)
        if match.group(1) in GREEK_SYMBOL_NAMES
    }
    cleaned = re.sub(r"\\[A-Za-z]+\*?", " ", cleaned)
    latin = set(re.findall(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", cleaned))
    return latin | greek


def symbol_table_tokens(text: str) -> set[str]:
    match = re.search(r"\\section\{定义与符号说明\}(.*?)(?=\\section\{|\\appendix|\Z)", text, re.S)
    if not match:
        return set()
    tokens: set[str] = set()
    for row in re.split(r"\\\\", match.group(1)):
        if "&" not in row:
            continue
        first_cell = row.split("&", 1)[0]
        for math in re.findall(r"\$(.+?)\$|\\\((.+?)\\\)", first_cell, re.S):
            tokens.update(math_symbol_tokens(math[0] or math[1]))
    return tokens


def displayed_formulae(text: str):
    patterns = (
        r"\\begin\{(?:equation|align|gather|multline|split)\*?\}(.*?)\\end\{(?:equation|align|gather|multline|split)\*?\}",
        r"\\\[(.*?)\\\]",
    )
    found = []
    for pattern in patterns:
        found.extend((m.start(), m.end(), m.group(1)) for m in re.finditer(pattern, text, re.S))
    return sorted(found)


def section_body(text: str, title: str) -> str:
    match = re.search(rf"\\section\{{{re.escape(title)}\}}(.*?)(?=\\section\{{|\\appendix|\Z)", text, re.S)
    return match.group(1) if match else ""

def check_quality(sources: list[SourceFile], issues: list[Issue]) -> None:
    content_sources = [source for source in sources if source.path.suffix.lower() == ".tex"]
    text = combined_text(content_sources)

    abstract_match = re.search(r"\\begin\{cumcmabstract\}\{([^{}]*)\}", text)
    if abstract_match:
        keywords = [item.strip() for item in abstract_match.group(1).split("；")]
        if len(keywords) != 5 or any(not item for item in keywords) or ";" in abstract_match.group(1):
            issues.append(
                Issue(
                    "硬错误",
                    "摘要关键词格式不符合五项中文分号规范",
                    abstract_match.group(1),
                    "改为：关键词：关键词 1；关键词 2；关键词 3；关键词 4；关键词 5。",
                )
            )
    else:
        issues.append(Issue("硬错误", "缺少标准摘要环境", "未找到 cumcmabstract", "使用模板摘要环境并填写五个关键词。"))

    expected_sections = [
        "问题描述",
        "问题分析",
        "模型假设",
        "定义与符号说明",
        "模型的建立与求解",
        "模型的评价",
    ]
    section_positions = {
        title: text.find(f"\\section{{{title}}}") for title in expected_sections
    }
    missing_sections = [title for title, position in section_positions.items() if position < 0]
    present_positions = [section_positions[title] for title in expected_sections if section_positions[title] >= 0]
    wrong_order = present_positions != sorted(present_positions)
    if missing_sections or wrong_order:
        evidence = (
            "缺少：" + "、".join(missing_sections)
            if missing_sections
            else "六个编号章节顺序不符合固定框架"
        )
        issues.append(
            Issue(
                "硬错误",
                "正文六个编号章节不符合固定框架",
                evidence,
                "依次使用：问题描述、问题分析、模型假设、定义与符号说明、模型的建立与求解、模型的评价。",
            )
        )

    problem_description = section_body(text, "问题描述")
    description_titles = ("问题背景", "问题重述")
    description_positions = [
        problem_description.find(f"\\subsection{{{title}}}")
        for title in description_titles
    ]
    if problem_description and (
        any(position < 0 for position in description_positions)
        or description_positions != sorted(description_positions)
    ):
        issues.append(
            Issue(
                "硬错误",
                "问题描述缺少固定二级结构或顺序错误",
                "需要依次设置 1.1 问题背景和 1.2 问题重述",
                "在问题描述内依次设置问题背景、问题重述，并在问题重述中逐问说明任务。",
            )
        )

    problem_analysis = section_body(text, "问题分析")
    if not problem_analysis:
        issues.append(Issue("硬错误", "缺少独立的问题分析章节", "未找到 \\section{问题分析}", "将问题分析作为正文第二节。"))
    else:
        plain_analysis = re.sub(r"\\[A-Za-z]+\*?(?:\[[^]]*\])?(?:\{[^{}]*\})?", "", problem_analysis)
        groups = {
            "任务输出": r"任务|目标|输出|要求",
            "数据与可识别性": r"数据|附件|字段|信息|识别|边界|能说明|不能说明",
            "难点与风险": r"难点|风险|异常|缺失|偏差",
            "备选方案取舍": r"备选|取舍|相比|选择|不采用|舍弃",
            "预处理与求解": r"预处理|清洗|特征|求解|算法|步骤",
            "验证方案": r"验证|检验|误差|稳健|敏感|对照",
            "问题依赖": r"依赖|复用|前问|后问|递进",
            "结论边界": r"结论边界|适用范围|仅基于|不能证明|不能说明",
        }
        missing_groups = [name for name, pattern in groups.items() if not re.search(pattern, plain_analysis)]
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", plain_analysis))
        if chinese_chars < 250 or len(missing_groups) >= 3:
            issues.append(
                Issue(
                    "质量警告",
                    "问题分析没有形成完整建模决策链",
                    f"约 {chinese_chars} 个汉字；缺少：{'、'.join(missing_groups) or '无'}",
                    "逐问补充任务输出、数据可识别性、难点风险、备选取舍、预处理求解、验证、依赖和结论边界。",
                )
            )
        analysis_matches = list(
            re.finditer(r"\\subsection\{问题([一二三四五六七八九十\d]+)\}", problem_analysis)
        )
        for index, match in enumerate(analysis_matches):
            question = match.group(1)
            end = analysis_matches[index + 1].start() if index + 1 < len(analysis_matches) else len(problem_analysis)
            block = problem_analysis[match.end() : end]
            plain_block = re.sub(r"\\[A-Za-z]+\*?(?:\[[^]]*\])?(?:\{[^{}]*\})?", "", block)
            missing_for_question = [
                name for name, pattern in groups.items() if not re.search(pattern, plain_block)
            ]
            question_chars = len(re.findall(r"[\u4e00-\u9fff]", plain_block))
            if question_chars < 180 or len(missing_for_question) >= 3:
                issues.append(
                    Issue(
                        "质量警告",
                        f"问题{question}分析不够完整",
                        f"约 {question_chars} 个汉字；缺少：{'、'.join(missing_for_question) or '无'}",
                        "补充本问的关键疑点、可能方向、取舍理由、求解验证和结论边界；篇幅服从论证需要，不机械压缩。",
                    )
                )

    analysis_questions = set(
        re.findall(r"\\subsection\{问题([一二三四五六七八九十\d]+)\}", problem_analysis)
    )
    modeling = section_body(text, "模型的建立与求解")
    model_matches = list(
        re.finditer(
            r"\\subsection\{问题([一二三四五六七八九十\d]+)的模型建立与求解\}",
            modeling,
        )
    )
    model_questions = {match.group(1) for match in model_matches}
    restated_questions = set(
        re.findall(r"\\textbf\{问题([一二三四五六七八九十\d]+)：\}", problem_description)
    )
    if not model_matches:
        issues.append(
            Issue(
                "硬错误",
                "模型建立与求解章没有逐问结构",
                "未找到 5.1 问题一的模型建立与求解等二级标题",
                "至少为一个子问题建立对应的逐问建模与求解二级标题。",
            )
        )
    if model_matches:
        preprocessing = modeling[: model_matches[0].start()]
        if "数据预处理" not in preprocessing:
            issues.append(
                Issue(
                    "硬错误",
                    "模型建立与求解章缺少总数据预处理",
                    "第一处逐问建模节之前未找到“数据预处理”",
                    "在第五节开头先写各问共用的数据预处理；问题特定处理放入对应问题。",
                )
            )
        for index, match in enumerate(model_matches):
            question = match.group(1)
            end = model_matches[index + 1].start() if index + 1 < len(model_matches) else len(modeling)
            block = modeling[match.end() : end]
            required = (
                "模型的建立",
                "模型的求解",
                "模型的计算结果",
                f"问题{question}结果验证与解释",
            )
            child_positions = [
                block.find(f"\\subsubsection{{{title}}}") for title in required
            ]
            missing = [
                title for title, position in zip(required, child_positions) if position < 0
            ]
            wrong_child_order = (
                not missing and child_positions != sorted(child_positions)
            )
            if missing or wrong_child_order:
                evidence = (
                    "缺少：" + "、".join(missing)
                    if missing
                    else "四个三级标题顺序错误"
                )
                issues.append(
                    Issue(
                        "硬错误",
                        f"问题{question}的建模求解层级不完整或顺序错误",
                        evidence,
                        "每问先写总览，再固定设置模型的建立、模型的求解、模型的计算结果、该问结果验证与解释。",
                    )
                )
    if not (restated_questions == analysis_questions == model_questions):
        issues.append(
            Issue(
                "硬错误",
                "问题重述、问题分析与逐问建模没有一一对应",
                f"重述问题：{sorted(restated_questions)}；分析问题：{sorted(analysis_questions)}；建模问题：{sorted(model_questions)}",
                "为每个子问题同时建立对应的逐问重述、问题分析二级标题和模型建立与求解二级标题。",
            )
        )

    evaluation = section_body(text, "模型的评价")
    if evaluation:
        evaluation_titles = ("模型的优点", "模型的缺点", "模型的推广")
        evaluation_positions = [
            evaluation.find(f"\\subsection{{{title}}}") for title in evaluation_titles
        ]
        missing_evaluation = [
            title for title, position in zip(evaluation_titles, evaluation_positions)
            if position < 0
        ]
        wrong_evaluation_order = (
            not missing_evaluation and evaluation_positions != sorted(evaluation_positions)
        )
        if missing_evaluation or wrong_evaluation_order:
            evidence = (
                "缺少：" + "、".join(missing_evaluation)
                if missing_evaluation
                else "三个二级标题顺序错误"
            )
            issues.append(
                Issue(
                    "硬错误",
                    "模型评价缺少固定二级结构或顺序错误",
                    evidence,
                    "在第六节依次设置模型的优点、模型的缺点和模型的推广。",
                )
            )

    assumptions = section_body(text, "模型假设")
    symbols = section_body(text, "定义与符号说明")
    if not assumptions or not symbols:
        issues.append(
            Issue(
                "硬错误",
                "模型假设与定义和符号说明未独立成第三、第四节",
                "需要 \\section{模型假设} 和 \\section{定义与符号说明}",
                "保持工程结构不变，在同一章节文件中拆成两个独立 section。",
            )
        )
    else:
        item_count = len(re.findall(r"\\item\b", assumptions))
        if not item_count:
            item_count = len(re.findall(r"(?m)^\s*\d+[\.、．]\s*", assumptions))
        if item_count > 6:
            issues.append(
                Issue(
                    "质量警告",
                    "模型假设数量偏多",
                    f"检测到 {item_count} 条假设",
                    "默认压缩为 3--6 条，只保留会改变模型关系、约束或适用边界的假设。",
                )
            )
        symbol_table_ok = all(token in symbols for token in ("\\toprule", "\\midrule", "\\bottomrule"))
        headers_ok = all(label in symbols for label in ("符号定义", "符号说明", "单位"))
        if not symbol_table_ok or not headers_ok:
            issues.append(
                Issue(
                    "硬错误",
                    "定义与符号说明不是规定的三列三线表",
                    "缺少三线命令或“符号定义、符号说明、单位”列名",
                    "在第四节“定义与符号说明”使用 booktabs 三线表，列名固定为符号定义、符号说明、单位。",
                )
            )

    global_symbols = symbol_table_tokens(text)
    for start, end, formula in displayed_formulae(text):
        formula_symbols = math_symbol_tokens(formula)
        if not formula_symbols:
            continue
        following = text[end : end + 600]
        following = re.split(r"\\(?:section|subsection|subsubsection|begin\{(?:equation|align|gather|multline))", following, maxsplit=1)[0]
        local_symbols: set[str] = set()
        if "其中" in following:
            for pair in re.findall(r"\$(.+?)\$|\\\((.+?)\\\)", following, re.S):
                local_symbols.update(math_symbol_tokens(pair[0] or pair[1]))
        missing = sorted(formula_symbols - global_symbols - local_symbols)
        if missing:
            line = text.count("\n", 0, start) + 1
            issues.append(
                Issue(
                    "硬错误",
                    "展示公式存在未说明符号",
                    f"约第 {line} 行：{', '.join(missing)}",
                    "把重要符号加入第四节三线表；其余符号在该公式后立即用“其中，符号为……”逐项说明。",
                )
            )

    graphics = re.findall(r"\\includegraphics(?:\[([^]]*)\])?\{[^}]+\}", text)
    graphic_widths = []
    missing_width = 0
    for options in graphics:
        width = re.search(r"width\s*=\s*([^,]+)", options or "")
        if width:
            graphic_widths.append(re.sub(r"\s+", "", width.group(1)))
        else:
            missing_width += 1
    if missing_width:
        issues.append(
            Issue(
                "质量警告",
                "图片未显式使用统一宽度",
                f"{missing_width} 张图片缺少 width 参数",
                "同类单图默认使用 width=\\CumcmFigureWidth，并在最终 PDF 中检查清晰度。",
            )
        )
    if len(set(graphic_widths)) > 2:
        issues.append(
            Issue(
                "质量警告",
                "图片宽度规格过多",
                "检测到：" + "、".join(sorted(set(graphic_widths))),
                "将同类图统一为 \\CumcmFigureWidth；仅为高信息密度横图保留 \\CumcmWideFigureWidth。",
            )
        )

    placeholder = re.search(
        r"\[(?:请|写明|替换|关键词|单位|使用环节|受影响|逐问|完整可运行|跨章节|论文标题|摘要应|说明|压缩|本节|先|集中|选择|优点|指出)",
        text,
    )
    if placeholder:
        source_match = find_first(
            content_sources,
            r"\[(?:请|写明|替换|关键词|单位|使用环节|受影响|逐问|完整可运行|跨章节|论文标题|摘要应|说明|压缩|本节|先|集中|选择|优点|指出)",
        )
        assert source_match
        source, match = source_match
        issues.append(
            Issue(
                "硬错误",
                "论文仍含模板占位内容",
                evidence_at(source, match.start(), match.group(0)),
                "用真实内容替换全部占位文字，并再次搜索方括号提示。",
            )
        )
    problem_sections = len(re.findall(r"\\subsection\{问题[一二三四五六七八九十\d]+的模型建立与求解", text))
    abstract_targets = len(re.findall(r"针对问题[一二三四五六七八九十\d]+", text))
    if problem_sections and abstract_targets < problem_sections:
        issues.append(
            Issue(
                "质量警告",
                "摘要没有逐个覆盖所有子问题",
                f"正文问题建模节 {problem_sections} 个，摘要明确覆盖 {abstract_targets} 个",
                "逐问补充方法、问题特定处理、关键结果和可信性证据。",
            )
        )
    innovation_claim = re.search(r"创新|首次|显著提升|创新性", text)
    innovation_window = (
        text[max(0, innovation_claim.start() - 800) : innovation_claim.end() + 1600]
        if innovation_claim
        else ""
    )
    innovation_evidence = re.search(
        r"基线|对照|消融|敏感|稳定|鲁棒|置信|误差|收敛|提升\s*\d",
        innovation_window,
    )
    if innovation_claim and not innovation_evidence:
        issues.append(
            Issue(
                "质量警告",
                "创新主张缺少定量证据",
                innovation_claim.group(0),
                "补充基线、消融、误差、稳定性或效率证据；否则改称特点或针对性设计。",
            )
        )
    subjective = re.search(
        r"判断矩阵|专家(?:打分|赋权)|(?:预先|人为|主观)设定.{0,16}(?:阈值|判据|权重)|"
        r"一票否决|组合权重",
        text,
    )
    if subjective:
        window = text[max(0, subjective.start() - 500) : subjective.end() + 900]
        if not re.search(
            r"来源|依据|题面给定|用户确认|专家记录|访谈记录|文献|数据估计|训练集|敏感性",
            window,
        ):
            issues.append(
                Issue(
                    "硬错误",
                    "主观参数或判定规则缺少来源",
                    subjective.group(0),
                    "给出题面、真实专家记录、可靠文献、数据估计或用户确认；否则停止写作。",
                )
            )
    strong_claim = re.search(
        r"(?:证明|说明|因此|由此可见).{0,36}(?:真实|因果|正确|错误|准确|不准确)",
        text,
    )
    if strong_claim:
        window = text[max(0, strong_claim.start() - 700) : strong_claim.end() + 900]
        if not re.search(r"适用范围|结论边界|仅基于|不能说明|真实标签|对照|识别条件", window):
            issues.append(
                Issue(
                    "质量警告",
                    "强结论缺少证据边界说明",
                    strong_claim.group(0),
                    "区分描述、相关、一致、预测与因果/正确性判断，补充识别条件或降低表述强度。",
                )
            )
    algorithm_named = re.search(
        r"遗传算法|粒子群|模拟退火|神经网络|随机森林|XGBoost|LightGBM|支持向量机|"
        r"TOPSIS|AHP|动态规划|线性规划|整数规划|混合整数|MILP|有限差分|数值积分|"
        r"Runge[-– ]?Kutta|龙格[-– ]?库塔",
        text,
        re.I,
    )
    algorithm_window = (
        text[max(0, algorithm_named.start() - 500) : algorithm_named.end() + 1400]
        if algorithm_named
        else ""
    )
    algorithm_details = re.search(
        r"参数|步长|容差|求解器|停止条件|终止条件|收敛|随机种子|约束处理|交叉验证",
        algorithm_window,
    )
    if algorithm_named and not algorithm_details:
        issues.append(
            Issue(
                "质量警告",
                "算法说明缺少复现要素",
                algorithm_named.group(0),
                "补充适用性、输入输出、关键参数、停止条件和验证，不要只列算法名。",
            )
        )
    algorithm_names = set(
        re.findall(
            r"遗传算法|粒子群|模拟退火|神经网络|随机森林|XGBoost|LightGBM|支持向量机|"
            r"TOPSIS|AHP|熵权|CRITIC|K[- ]?means|动态规划|线性规划|整数规划|"
            r"混合整数|MILP|Bootstrap|置换检验",
            text,
            re.I,
        )
    )
    if len(algorithm_names) >= 4 and not re.search(
        r"主模型|基线|对照|消融|稳健性|分别用于|用于验证|求解器",
        text,
    ):
        issues.append(
            Issue(
                "质量警告",
                "疑似模型或算法堆叠",
                "检测到：" + "、".join(sorted(algorithm_names, key=str.lower)),
                "每个子问题突出一个主模型，并为其他方法标明基线、对照、验证或求解角色；无明确作用则删除。",
            )
        )
    labels = set(re.findall(r"\\label\{((?:fig|tab):[^}]+)\}", text))
    refs = set(re.findall(r"\\(?:ref|cref|Cref|autoref)\{((?:fig|tab):[^}]+)\}", text))
    for unused in sorted(labels - refs):
        issues.append(
            Issue(
                "质量警告",
                "图表没有在正文交叉引用",
                unused,
                "在图表出现前引导并在其后解释；无论证作用则删除。",
            )
        )
    if "NotImplementedError" in text:
        issues.append(
            Issue(
                "硬错误",
                "附录代码仍为占位程序",
                "NotImplementedError",
                "替换为完整、可运行且与论文结果一致的源程序。",
            )
        )


def check_static_rules(main_tex: Path, sources: list[SourceFile], issues: list[Issue]) -> None:
    text = combined_text(sources)
    add_pattern_issue(
        issues,
        sources,
        r"\\tableofcontents",
        "取消资格风险",
        "论文包含目录",
        "删除 \\tableofcontents；2026 规范明确要求正文不要目录。",
    )
    add_pattern_issue(
        issues,
        sources,
        r"承诺书|编号专用页",
        "取消资格风险",
        "电子论文源文件包含承诺书或编号专用页",
        "从电子版工程中删除，只在纸质版按要求另行处理。",
    )
    add_pattern_issue(
        issues,
        sources,
        r"\\author\{\s*[^}\s][^}]*\}",
        "取消资格风险",
        "LaTeX 作者字段非空",
        "删除作者、学校、赛区和指导教师信息，并清理 PDF 元数据。",
    )
    identity_patterns = (
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        r"(?:学校|赛区|队员|指导教师|学号)\s*[:：]\s*\S+",
        r"[A-Za-z]:\\Users\\[^\\\s]+",
    )
    for pattern in identity_patterns:
        found = find_first(sources, pattern, re.I)
        if found:
            source, match = found
            issues.append(
                Issue(
                    "取消资格风险",
                    "可能泄露身份信息",
                    evidence_at(source, match.start(), match.group(0)),
                    "人工确认并删除个人、学校、赛区、联系方式、学号和本机路径。",
                )
            )
    for environment in re.finditer(
        r"\\begin\{(figure|table)\*?\}(.*?)\\end\{\1\*?\}",
        text,
        re.S,
    ):
        if "\\label{" not in environment.group(2):
            issues.append(
                Issue(
                    "质量警告",
                    "图表环境缺少标签，无法验证正文引用",
                    f"{environment.group(1)} 环境",
                    "为承担论证作用的图表添加 fig:/tab: 标签并在正文首次出现前后引用和解释。",
                )
            )
    if "\\appendix" not in text:
        issues.append(Issue("取消资格风险", "缺少附录", str(main_tex), "在正文后加入附录。"))
    if "支撑材料文件列表" not in text:
        issues.append(
            Issue(
                "取消资格风险",
                "附录缺少支撑材料文件列表",
                str(main_tex),
                "列出全部代码、数据、中间结果和 AI 详情文件。",
            )
        )
    if "\\lstinputlisting" not in text and "本论文没有用到程序" not in text:
        issues.append(
            Issue(
                "取消资格风险",
                "附录没有完整代码或无程序声明",
                str(main_tex),
                "附上完整可运行代码；确无程序时使用官方规定声明。",
            )
        )
    margin_confirmed = bool(
        re.search(r"(?:top|margin)\s*=\s*(?:2\.5cm|25mm)", text, re.I)
        and re.search(r"(?:bottom|margin)\s*=\s*(?:2\.5cm|25mm)", text, re.I)
        and re.search(r"(?:left|margin)\s*=\s*(?:2\.5cm|25mm)", text, re.I)
        and re.search(r"(?:right|margin)\s*=\s*(?:2\.5cm|25mm)", text, re.I)
    ) or bool(re.search(r"margin\s*=\s*(?:2\.5cm|25mm)", text, re.I))
    if not margin_confirmed or "a4paper" not in text.lower():
        issues.append(
            Issue(
                "取消资格风险",
                "源码中未确认 A4 与 2.5 cm 页边距",
                str(main_tex.parent / "cumcmthesis.cls"),
                "显式设置 A4 和四边至少 2.5 cm，并在 PDF 中复核。",
            )
        )


def check_support_archive(path: Path | None, issues: list[Issue]) -> None:
    if path is None:
        issues.append(
            Issue(
                "优化建议",
                "未提供支撑材料压缩包",
                "",
                "正式提交前用 --support-archive 检查文件存在和 20 MB 限制。",
            )
        )
        return
    if not path.is_file():
        issues.append(Issue("硬错误", "支撑材料压缩包不存在", str(path), "修正路径或创建压缩包。"))
        return
    if path.suffix.lower() not in {".zip", ".rar"}:
        issues.append(
            Issue(
                "取消资格风险",
                "支撑材料压缩格式不正确",
                str(path),
                "使用 ZIP 或 RAR。",
            )
        )
    if path.stat().st_size > 20 * 1024 * 1024:
        issues.append(
            Issue(
                "取消资格风险",
                "支撑材料超过 20 MB",
                f"{path.stat().st_size / 1024 / 1024:.2f} MB",
                "清理缓存和重复文件，压缩到 20 MB 内。",
            )
        )
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                lowered = [name.lower() for name in names]
                if not any(name.endswith("ai工具使用详情.pdf") for name in lowered):
                    issues.append(
                        Issue(
                            "取消资格风险",
                            "支撑材料压缩包缺少 AI工具使用详情.pdf",
                            str(path),
                            "把完整 AI 使用详情 PDF 放入最终提交压缩包。",
                        )
                    )
                if not any(
                    Path(name).suffix.lower()
                    in {".py", ".m", ".r", ".jl", ".c", ".cpp", ".java", ".ipynb"}
                    for name in names
                ):
                    issues.append(
                        Issue(
                            "取消资格风险",
                            "支撑材料压缩包没有识别到源代码",
                            str(path),
                            "加入与论文结果一致的完整可运行源程序。",
                        )
                    )
                identity_name = next(
                    (
                        name
                        for name in names
                        if re.search(r"学校|赛区|队员|指导教师|学号|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", name)
                    ),
                    None,
                )
                if identity_name:
                    issues.append(
                        Issue(
                            "取消资格风险",
                            "支撑材料文件名可能泄露身份",
                            identity_name,
                            "匿名化压缩包内的目录名和文件名，并继续检查文件内容与元数据。",
                        )
                    )
        except zipfile.BadZipFile:
            issues.append(Issue("硬错误", "支撑材料 ZIP 无法读取", str(path), "重新创建有效 ZIP。"))


def report_markdown(main_tex: Path, issues: list[Issue]) -> str:
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for issue in issues:
        counts[issue.severity] += 1
    blocking = counts["取消资格风险"] + counts["硬错误"]
    status = "不通过" if blocking else "通过（仍需人工复核模型与数值）"
    lines = [
        "# CUMCM 论文质量报告",
        "",
        f"- 入口：`{main_tex}`",
        f"- 结论：**{status}**",
        f"- 取消资格风险：{counts['取消资格风险']}",
        f"- 硬错误：{counts['硬错误']}",
        f"- 质量警告：{counts['质量警告']}",
        f"- 优化建议：{counts['优化建议']}",
        "",
        "> 自动审查不能证明模型、代码、引用或结果正确。必须人工回代约束、复现实验并逐页检查最终 PDF。",
        "",
    ]
    if not issues:
        lines.extend(["未发现自动规则问题。", ""])
        return "\n".join(lines)
    for severity in SEVERITY_ORDER:
        group = [issue for issue in issues if issue.severity == severity]
        if not group:
            continue
        lines.extend([f"## {severity}", ""])
        for index, issue in enumerate(group, 1):
            lines.extend(
                [
                    f"### {index}. {issue.title}",
                    "",
                    f"- 证据：{issue.evidence or '未提供'}",
                    f"- 修复：{issue.fix}",
                    "",
                ]
            )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    main_tex = args.main_tex.expanduser().resolve()
    issues: list[Issue] = []
    if not main_tex.is_file():
        print(f"错误：入口文件不存在：{main_tex}", file=sys.stderr)
        return 2

    sources, source_issues = load_sources(main_tex)
    issues.extend(source_issues)
    check_writeability_reports(main_tex, issues)
    check_static_rules(main_tex, sources, issues)
    check_assets(main_tex, sources, issues)
    check_references(main_tex, sources, issues)
    check_log(main_tex, issues)
    check_pdf(main_tex, args.pdf, issues)
    check_ai_manifest(main_tex, sources, args.ai_manifest, issues)
    check_results(args.results, sources, issues)
    check_support_archive(args.support_archive, issues)
    check_quality(sources, issues)

    issues = sorted(
        set(issues),
        key=lambda issue: (SEVERITY_ORDER[issue.severity], issue.title, issue.evidence),
    )
    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_markdown(main_tex, issues), encoding="utf-8")

    blocking = sum(issue.severity in {"取消资格风险", "硬错误"} for issue in issues)
    print(f"报告：{report_path}")
    print(f"问题总数：{len(issues)}；阻断问题：{blocking}")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
