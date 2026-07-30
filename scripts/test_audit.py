#!/usr/bin/env python3
"""Regression tests for the CUMCM paper auditor."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("audit_cumcm", SCRIPT_DIR / "audit_cumcm.py")
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class FakeMediaBox:
    width = 595.3
    height = 841.9


class FakePage:
    mediabox = FakeMediaBox()

    def __init__(self, text: str = "") -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class FakeReader:
    def __init__(self, page_count: int = 6) -> None:
        self.pages = [FakePage("摘要 关键词")] + [FakePage("正文")] * (page_count - 1)
        if page_count >= 3:
            self.pages[-2] = FakePage("参考文献\n[1] 中文文献\n[2] English reference")
        self.metadata = {"/Author": ""}


def source(path: Path, text: str) -> audit.SourceFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return audit.SourceFile(path.resolve(), text)


@contextmanager
def fake_pypdf(page_count: int):
    previous = sys.modules.get("pypdf")
    sys.modules["pypdf"] = types.SimpleNamespace(PdfReader=lambda _: FakeReader(page_count))
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop("pypdf", None)
        else:
            sys.modules["pypdf"] = previous


class AuditTests(unittest.TestCase):
    def test_bibtex_bibliography_resource_is_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paper = Path(temp)
            main = paper / "main.tex"
            sources = [
                source(
                    main,
                    r"\cite{one}\bibliography{references}",
                )
            ]
            (paper / "references.bib").write_text(
                "@misc{one,title={A}}\n@misc{two,title={B}}\n@misc{three,title={C}}\n",
                encoding="utf-8",
            )
            issues: list[audit.Issue] = []
            audit.check_references(main, sources, issues)
            self.assertFalse(any("引用键" in item.title or "数量偏少" in item.title for item in issues))

    def test_english_academic_references_cannot_outnumber_chinese(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paper = Path(temp)
            main = paper / "document.tex"
            sources = [
                source(
                    main,
                    r"\cite{zh,en1,en2,ai}\clearpage\bibliography{book}",
                )
            ]
            (paper / "book.bib").write_text(
                "@article{zh,author={张三},title={中文研究},journal={系统工程},year={2024}}\n"
                "@article{en1,author={A},title={Study One},journal={J},year={2023}}\n"
                "@article{en2,author={B},title={Study Two},journal={J},year={2022}}\n"
                "@misc{ai,author={OpenAI},title={Codex},year={2026}}\n",
                encoding="utf-8",
            )
            issues: list[audit.Issue] = []
            audit.check_references(main, sources, issues)
            self.assertIn("中文学术文献少于英文学术文献", {item.title for item in issues})

    def test_ai_reference_is_excluded_from_language_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paper = Path(temp)
            main = paper / "document.tex"
            sources = [
                source(
                    main,
                    r"\cite{zh,en,ai}\clearpage\bibliography{book}",
                )
            ]
            (paper / "book.bib").write_text(
                "@article{zh,author={张三},title={中文研究},journal={系统工程},year={2024}}\n"
                "@article{en,author={A},title={English Study},journal={Journal},year={2023}}\n"
                "@misc{ai,author={OpenAI},title={Codex},year={2026}}\n",
                encoding="utf-8",
            )
            issues: list[audit.Issue] = []
            audit.check_references(main, sources, issues)
            self.assertNotIn("中文学术文献少于英文学术文献", {item.title for item in issues})

    def test_reference_page_break_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paper = Path(temp)
            main = paper / "document.tex"
            sources = [source(main, r"\cite{zh}\bibliography{book}")]
            (paper / "book.bib").write_text(
                "@article{zh,author={张三},title={中文研究},journal={系统工程},year={2024}}\n",
                encoding="utf-8",
            )
            issues: list[audit.Issue] = []
            audit.check_references(main, sources, issues)
            self.assertIn("参考文献没有单独起页", {item.title for item in issues})

    def test_placeholder_code_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main = root / "paper" / "main.tex"
            code = root / "support" / "code" / "main.py"
            code.parent.mkdir(parents=True)
            code.write_text("raise NotImplementedError('replace')\n", encoding="utf-8")
            sources = [source(main, r"\lstinputlisting{../support/code/main.py}")]
            issues: list[audit.Issue] = []
            audit.check_assets(main, sources, issues)
            self.assertIn("附录代码仍为占位程序", {item.title for item in issues})

    def test_missing_cross_reference_and_citation_are_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paper = Path(temp)
            main = paper / "main.tex"
            sources = [source(main, r"\autoref{fig:missing}\citep{missing}\bibliography{references}")]
            (paper / "references.bib").write_text(
                "@misc{one,title={A}}\n@misc{two,title={B}}\n@misc{three,title={C}}\n",
                encoding="utf-8",
            )
            issues: list[audit.Issue] = []
            audit.check_references(main, sources, issues)
            titles = {item.title for item in issues}
            self.assertIn("交叉引用没有对应标签", titles)
            self.assertIn("引用键不在参考文献数据库中", titles)

    def test_graphicspath_asset_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main = root / "document.tex"
            figure = root / "texfile" / "figures" / "chart.pdf"
            figure.parent.mkdir(parents=True)
            figure.write_bytes(b"%PDF-test")
            sources = [
                source(main, r"\includegraphics{chart.pdf}"),
                source(root / "cumcmthesis.cls", r"\graphicspath{{texfile/figures/}}"),
            ]
            issues: list[audit.Issue] = []
            audit.check_assets(main, sources, issues)
            self.assertNotIn("图片不存在", {item.title for item in issues})

    def test_reference_mention_is_not_mistaken_for_page_heading(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            main.write_text("", encoding="utf-8")
            main.with_suffix(".aux").write_text(
                "\\newlabel{cumcm:abstract-end}{{}{1}}\n\\newlabel{cumcm:body-end}{{}{3}}\n",
                encoding="utf-8",
            )
            pdf = main.with_suffix(".pdf")
            pdf.write_bytes(b"%PDF-test")
            class Reader:
                pages = [
                    FakePage("摘要\n关键词"),
                    FakePage("正文中提到参考文献但不是标题"),
                    FakePage("参考文献\n[1] 中文文献"),
                ]
                metadata = {"/Author": ""}
            previous = sys.modules.get("pypdf")
            sys.modules["pypdf"] = types.SimpleNamespace(PdfReader=lambda _: Reader())
            try:
                issues: list[audit.Issue] = []
                audit.check_pdf(main, pdf, issues)
            finally:
                if previous is None:
                    sys.modules.pop("pypdf", None)
                else:
                    sys.modules["pypdf"] = previous
            self.assertNotIn("参考文献未从独立页面开始", {item.title for item in issues})

    def test_unlabelled_figure_is_warned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "main.tex"
            sources = [
                source(
                    main,
                    r"\begin{figure}\centering\rule{1cm}{1cm}\caption{测试图}\end{figure}\appendix 支撑材料文件列表",
                )
            ]
            issues: list[audit.Issue] = []
            audit.check_static_rules(main, sources, issues)
            self.assertIn("图表环境缺少标签，无法验证正文引用", {item.title for item in issues})

    def test_missing_result_source_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main = root / "main.tex"
            sources = [source(main, "核心结果为 12.3。")]
            results = root / "result-claims.json"
            results.write_text(
                json.dumps(
                    {
                        "claims": [
                            {
                                "id": "q1",
                                "value": "12.3",
                                "unit": "mg/m3",
                                "source": "results/missing.csv",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            issues: list[audit.Issue] = []
            audit.check_results(results, sources, issues)
            self.assertIn("结果声明的来源文件不存在", {item.title for item in issues})

    def test_static_disqualification_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "main.tex"
            sources = [source(main, r"\documentclass{article}\author{某大学队伍}\tableofcontents")]
            issues: list[audit.Issue] = []
            audit.check_static_rules(main, sources, issues)
            titles = {item.title for item in issues}
            self.assertIn("论文包含目录", titles)
            self.assertIn("LaTeX 作者字段非空", titles)
            self.assertIn("缺少附录", titles)

    def test_abstract_and_body_page_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paper = Path(temp)
            main = paper / "main.tex"
            main.write_text("", encoding="utf-8")
            main.with_suffix(".aux").write_text(
                "\\newlabel{cumcm:abstract-end}{{}{2}}\n"
                "\\newlabel{cumcm:body-end}{{}{31}}\n",
                encoding="utf-8",
            )
            pdf = paper / "main.pdf"
            pdf.write_bytes(b"%PDF-test")
            issues: list[audit.Issue] = []
            with fake_pypdf(31):
                audit.check_pdf(main, pdf, issues)
            titles = {item.title for item in issues}
            self.assertIn("摘要超过一页", titles)
            self.assertIn("正文超过 30 页", titles)

    def test_ai_false_declaration_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main = root / "paper" / "main.tex"
            sources = [source(main, "人工智能工具使用说明 ai-tool 未使用任何")]
            manifest = root / "support" / "ai-use.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({"used": False, "tools": [], "purposes": [], "key_interactions": []}),
                encoding="utf-8",
            )
            issues: list[audit.Issue] = []
            audit.check_ai_manifest(main, sources, manifest, issues)
            titles = {item.title for item in issues}
            self.assertIn("使用本 skill 却声明未使用 AI", titles)
            self.assertIn("论文同时声称未使用 AI", titles)
            self.assertIn("缺少 AI工具使用详情.pdf", titles)

    def test_unsupported_innovation_and_algorithm_name_are_warned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "main.tex"
            sources = [source(main, "本文首次提出一项创新方法，并使用 XGBoost 求解。")]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            titles = {item.title for item in issues}
            self.assertIn("创新主张缺少定量证据", titles)
            self.assertIn("算法说明缺少复现要素", titles)

    def test_credit_rating_style_overclaim_and_subjective_rules_are_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            sources = [
                source(
                    main,
                    "采用 AHP 判断矩阵、CRITIC、TOPSIS 和 K-means，"
                    "并预先设定阈值实行一票否决，因此证明银行评级不准确。",
                )
            ]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            titles = {item.title for item in issues}
            self.assertIn("主观参数或判定规则缺少来源", titles)
            self.assertIn("强结论缺少证据边界说明", titles)
            self.assertIn("疑似模型或算法堆叠", titles)

    def test_writeability_reports_block_drafting_until_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main = root / "document.tex"
            main.write_text("", encoding="utf-8")
            reports = root / "reports"
            reports.mkdir()
            (reports / "MODELING_WRITEABILITY_REPORT.md").write_text(
                "# 可写性\n- 状态：BLOCK\n- [填写]\n",
                encoding="utf-8",
            )
            (reports / "CLAIM_EVIDENCE_MATRIX.md").write_text(
                "# 矩阵\n| [填写] |\n",
                encoding="utf-8",
            )
            issues: list[audit.Issue] = []
            audit.check_writeability_reports(main, issues)
            titles = {item.title for item in issues}
            self.assertIn("建模成果可写性审查未通过", titles)
            self.assertIn("建模成果可写性审查仍含占位内容", titles)
            self.assertIn("主张—证据矩阵仍含占位内容", titles)

    def test_keywords_require_five_chinese_semicolon_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            sources = [source(main, r"""
\begin{cumcmabstract}{模型；检验；结果}摘要\end{cumcmabstract}
\section{问题分析}任务、数据、模型、求解和验证。
\section{模型假设}一条必要假设。
\section{定义与符号说明}\toprule 符号定义 & 符号说明 & 单位 \\ \midrule $x$ & 变量 & -- \\ \bottomrule
""")]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            self.assertIn("摘要关键词格式不符合五项中文分号规范", {item.title for item in issues})

    def test_pure_english_keyword_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            sources = [source(main, r"""
\begin{cumcmabstract}{XGBoost；稳健性检验；结果分析；误差评估；适用边界}摘要\end{cumcmabstract}
""")]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            self.assertIn("摘要关键词格式不符合五项中文分号规范", {item.title for item in issues})

    def test_abstract_bold_and_parenthetical_english_are_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            sources = [source(main, r"""
\begin{cumcmabstract}{回归模型；稳健性检验；结果分析；误差评估；适用边界}
\textbf{针对问题一，}采用主成分分析（PCA）。
\end{cumcmabstract}
""")]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            titles = {item.title for item in issues}
            self.assertIn("摘要结构提示被错误加粗", titles)
            self.assertIn("中文学术术语后附有英文解释或缩写", titles)

    def test_formula_numbering_environments_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            invalid = [source(main, r"""
\begin{cumcmabstract}{回归模型；稳健性检验；结果分析；误差评估；适用边界}摘要\end{cumcmabstract}
行内关系为 \(y=ax+b\)。\begin{equation}y=ax+b\end{equation}
""")]
            issues: list[audit.Issue] = []
            audit.check_quality(invalid, issues)
            titles = {item.title for item in issues}
            self.assertIn("行内公式未使用章内编号命令", titles)
            self.assertIn("公式未使用规定编号环境", titles)

            valid = [source(main, r"""
\begin{cumcmabstract}{回归模型；稳健性检验；结果分析；误差评估；适用边界}摘要\end{cumcmabstract}
\section{定义与符号说明}\toprule 符号定义 & 符号说明 & 单位 \\ \midrule $x$ & 变量 & -- \\ $y$ & 结果 & -- \\ \bottomrule
\section{模型建立}
\CumcmInlineFormula{y=x+1}
\begin{CumcmReferencedEquation}y=2x\label{eq:core}\end{CumcmReferencedEquation}
由式\eqref{eq:core}继续计算。
\begin{CumcmDisplayFormula}y=3x\end{CumcmDisplayFormula}
""")]
            issues = []
            audit.check_quality(valid, issues)
            titles = {item.title for item in issues}
            self.assertNotIn("行内公式未使用章内编号命令", titles)
            self.assertNotIn("公式未使用规定编号环境", titles)
            self.assertNotIn("核心公式没有后文交叉引用", titles)

    def test_table_symbol_row_and_needspace_rules_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            sources = [source(main, r"""
\begin{cumcmabstract}{回归模型；稳健性检验；结果分析；误差评估；适用边界}摘要\end{cumcmabstract}
\Needspace{0.30\textheight}
\section{定义与符号说明}
\begin{tabularx}{0.70\textwidth}{CCC}\toprule 符号定义 & 符号说明 & 单位 \\ \midrule $x,y$ & 两个变量 & -- \\ \bottomrule\end{tabularx}
""")]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            titles = {item.title for item in issues}
            self.assertIn("正文表格宽度不是 0.92 倍版心", titles)
            self.assertIn("符号说明表一行包含多个符号", titles)
            self.assertIn("标题前预留空间超过四分之一页", titles)
    def test_formula_symbols_may_be_global_or_locally_explained(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            sources = [source(main, r"""
\begin{cumcmabstract}{模型；检验；结果；稳健性；边界}摘要\end{cumcmabstract}
\section{问题分析}任务输出、附件数据与可识别性、难点风险、备选方案取舍、预处理求解、验证方案、前后问依赖和结论边界。
\section{模型假设}假设只改变一条约束。
\section{定义与符号说明}\toprule 符号定义 & 符号说明 & 单位 \\ \midrule $x$ & 结果 & -- \\ \bottomrule
\section{模型建立}
\begin{CumcmDisplayFormula}x=y+z\end{CumcmDisplayFormula}
其中，\(y\) 为观测量，\(z\) 为修正量。
""")]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            self.assertNotIn("展示公式存在未说明符号", {item.title for item in issues})

    def test_unexplained_formula_symbol_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            sources = [source(main, r"""
\begin{cumcmabstract}{模型；检验；结果；稳健性；边界}摘要\end{cumcmabstract}
\section{问题分析}任务输出、附件数据、模型求解与验证。
\section{模型假设}假设只改变一条约束。
\section{定义与符号说明}\toprule 符号定义 & 符号说明 & 单位 \\ \midrule $x$ & 结果 & -- \\ \bottomrule
\section{模型建立}\begin{CumcmDisplayFormula}x=y+z\end{CumcmDisplayFormula}
""")]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            self.assertIn("展示公式存在未说明符号", {item.title for item in issues})

    def test_assumption_pile_and_figure_width_variation_are_warned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            assumptions = "\\begin{enumerate}" + "\\item 必要假设" * 7 + "\\end{enumerate}"
            tex = (
                r"\begin{cumcmabstract}{模型；检验；结果；稳健性；边界}摘要\end{cumcmabstract}"
                r"\section{问题分析}任务输出、数据识别、难点风险、备选取舍、预处理求解、验证、依赖和边界。"
                r"\section{模型假设}" + assumptions +
                r"\section{定义与符号说明}\toprule 符号定义 & 符号说明 & 单位 \\ \midrule $x$ & 变量 & -- \\ \bottomrule"
                r"\includegraphics[width=.6\textwidth]{a.pdf}"
                r"\includegraphics[width=.7\textwidth]{b.pdf}"
                r"\includegraphics[width=.8\textwidth]{c.pdf}"
            )
            sources = [source(main, tex)]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            titles = {item.title for item in issues}
            self.assertIn("模型假设数量偏多", titles)
            self.assertIn("图片宽度规格过多", titles)

    def test_incomplete_problem_analysis_is_warned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            sources = [source(main, r"""
\begin{cumcmabstract}{模型；检验；结果；稳健性；边界}摘要\end{cumcmabstract}
\section{问题分析}选择一个模型求解。
\section{模型假设}必要假设。
\section{定义与符号说明}\toprule 符号定义 & 符号说明 & 单位 \\ \midrule $x$ & 变量 & -- \\ \bottomrule
""")]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            self.assertIn("问题分析没有形成完整建模决策链", {item.title for item in issues})

    def test_fixed_framework_requires_matching_question_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "document.tex"
            sources = [source(main, r"""
\begin{cumcmabstract}{模型；求解；结果；验证；边界}针对问题一给出结果。\end{cumcmabstract}
\section{问题描述}
\subsection{问题背景}背景。
\subsection{问题重述}\textbf{问题一：}完成任务。
\section{问题分析}\subsection{问题一}仅作简短分析。
\section{模型假设}必要假设。
\section{定义与符号说明}\toprule 符号定义 & 符号说明 & 单位 \\ \midrule $x$ & 变量 & -- \\ \bottomrule
\section{模型的建立与求解}
\subsection{问题二的模型建立与求解}
\subsubsection{模型的建立}模型。
\section{模型的评价}
""")]
            issues: list[audit.Issue] = []
            audit.check_quality(sources, issues)
            titles = {item.title for item in issues}
            self.assertIn("问题重述、问题分析与逐问建模没有一一对应", titles)
            self.assertIn("问题一分析不够完整", titles)
            self.assertIn("模型建立与求解章缺少总数据预处理", titles)
            self.assertIn("问题二的建模求解层级不完整或顺序错误", titles)
            self.assertIn("模型评价缺少固定二级结构或顺序错误", titles)

    def test_clean_fixture_has_no_blocking_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paper = root / "paper"
            support = root / "support"
            main = paper / "main.tex"
            tex = r"""
\documentclass[a4paper]{ctexart}
\usepackage[margin=2.5cm]{geometry}
\begin{document}
\begin{cumcmabstract}{稳健性；检验；模型；结果；边界}针对问题一，模型得到 12.3，并完成误差检验。\end{cumcmabstract}
\label{cumcm:abstract-end}
\section{问题描述}
\subsection{问题背景}说明研究对象与现实任务。
\subsection{问题重述}\textbf{问题一：}根据附件数据建立模型并输出结果。
\section{问题分析}
\subsection{问题一}
任务输出基于附件数据；说明数据字段与可识别性、关键疑点、异常缺失和偏差风险；比较备选方案并解释取舍，选择主模型；说明预处理、特征构造、求解算法和验证方案；交代前后问依赖、结论边界与适用范围。进一步讨论输入输出、模型条件、对照检验和失败情形，使分析能够对应后续建模。
\section{模型假设}
仅保留会改变约束的一条必要假设。
\section{定义与符号说明}
便于后续模型建立与求解，本文基于上述问题分析与模型假设给出以下若干符号的定义与说明。
\begin{tabularx}{\CumcmTableWidth}{CCC}\toprule 符号定义 & 符号说明 & 单位 \\ \midrule $x$ & 变量 & -- \\ \bottomrule\end{tabularx}
\section{模型的建立与求解}
\textbf{数据预处理：}检查缺失与异常并构造变量。
\subsection{问题一的模型建立与求解}
先总览目标、主模型、输入输出、求解与验证路线。
\subsubsection{模型的建立}定义变量、目标和约束。
\subsubsection{模型的求解}记录参数与停止条件。
\subsubsection{模型的计算结果}核心结果为 12.3 单位。
\subsubsection{问题一结果验证与解释}完成误差检验并说明边界。
人工智能工具使用说明见文献\cite{ai-tool}。模型依据中文研究\cite{model-zh}
与 English study\cite{model-en}，参数与停止条件均已记录。
\section{模型的评价}
\subsection{模型的优点}与正文证据对应。
\subsection{模型的缺点}说明原因和影响。
\subsection{模型的推广}说明需要替换的数据与约束。
\clearpage
\bibliography{references}
\appendix
\section{支撑材料文件列表}
\lstinputlisting{../support/code/main.py}
\end{document}
"""
            sources = [source(main, tex)]
            (paper / "cumcm-paper.sty").write_text("", encoding="utf-8")
            (paper / "references.bib").write_text(
                "@misc{ai-tool,title={AI assistant}}\n"
                "@article{model-zh,author={张三},title={中文模型},journal={系统工程},year={2024}}\n"
                "@article{model-en,author={A},title={Model},journal={Journal},year={2023}}\n",
                encoding="utf-8",
            )
            code = support / "code" / "main.py"
            code.parent.mkdir(parents=True)
            code.write_text("print(12.3)\n", encoding="utf-8")
            manifest = support / "ai-use.json"
            manifest.write_text(
                json.dumps(
                    {
                        "used": True,
                        "tools": [{"name": "AI assistant", "version": "2026-07"}],
                        "purposes": ["语言润色与检查"],
                        "key_interactions": [{"purpose": "检查摘要", "human_revision": "逐项核验"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (support / "AI工具使用详情.pdf").write_bytes(b"%PDF-test")
            results = support / "result-claims.json"
            results.write_text(
                json.dumps({"claims": [{"id": "q1", "value": "12.3", "unit": "单位", "source": "code/main.py"}]}),
                encoding="utf-8",
            )
            archive = root / "support.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.write(code, "code/main.py")
                bundle.write(support / "AI工具使用详情.pdf", "AI工具使用详情.pdf")
            main.with_suffix(".log").write_text("Output written on main.pdf", encoding="utf-8")
            main.with_suffix(".aux").write_text(
                "\\newlabel{cumcm:abstract-end}{{}{1}}\n"
                "\\newlabel{cumcm:body-end}{{}{5}}\n",
                encoding="utf-8",
            )
            pdf = main.with_suffix(".pdf")
            pdf.write_bytes(b"%PDF-test")

            issues: list[audit.Issue] = []
            audit.check_static_rules(main, sources, issues)
            audit.check_assets(main, sources, issues)
            audit.check_references(main, sources, issues)
            audit.check_log(main, issues)
            with fake_pypdf(6):
                audit.check_pdf(main, pdf, issues)
            audit.check_ai_manifest(main, sources, manifest, issues)
            audit.check_results(results, sources, issues)
            audit.check_support_archive(archive, issues)
            audit.check_quality(sources, issues)
            blocking = [item for item in issues if item.severity in {"取消资格风险", "硬错误"}]
            self.assertEqual([], blocking, "\n".join(f"{item.title}: {item.evidence}" for item in blocking))


if __name__ == "__main__":
    unittest.main(verbosity=2)
