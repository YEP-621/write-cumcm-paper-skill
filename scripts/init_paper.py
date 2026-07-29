#!/usr/bin/env python3
"""Initialize an independent CUMCM XeLaTeX project from bundled assets."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


CHINESE_NUMERALS = {
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
    10: "十",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="创建采用认可版式的 CUMCM 2026 分章节 XeLaTeX 工程。"
    )
    parser.add_argument("output_dir", type=Path, help="新工程输出目录")
    parser.add_argument(
        "--questions",
        type=int,
        default=3,
        choices=range(1, 11),
        metavar="N",
        help="赛题子问题数量，1--10，默认 3",
    )
    return parser.parse_args()


def ensure_safe_destination(destination: Path) -> None:
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"输出路径已存在且不是目录：{destination}")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(
            f"输出目录不是空目录：{destination}\n"
            "为保护已有文件，初始化器不会覆盖或清理该目录。"
        )


def problem_section(template: str, number: int) -> str:
    chinese = CHINESE_NUMERALS[number]
    return (
        template.replace("问题一", f"问题{chinese}")
        .replace("problem1", f"problem{number}")
    )


def replace_marker_block(text: str, start_marker: str, end_marker: str, content: str) -> str:
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker)
    return text[:start] + "\n" + content.rstrip() + "\n" + text[end:]


def question_restatement(number: int) -> str:
    chinese = CHINESE_NUMERALS[number]
    return (
        f"\\noindent\\textbf{{问题{chinese}：}}"
        "[说明本问给定信息、需要建立的模型和必须输出的结果。]"
    )


def question_analysis(number: int) -> str:
    chinese = CHINESE_NUMERALS[number]
    return (
        f"\\subsection{{问题{chinese}}}\n\n"
        "[说明本问的任务输出、数据及可识别性、所有关键疑点、异常与风险、"
        "可能路线及取舍、主模型选择、预处理与求解方向、验证方案、"
        "与其他问题的依赖及结论边界。]"
    )


def main() -> int:
    args = parse_args()
    skill_dir = Path(__file__).resolve().parent.parent
    template_dir = skill_dir / "assets" / "latex-template"
    destination = args.output_dir.expanduser().resolve()

    if not template_dir.is_dir():
        print(f"错误：找不到 skill 内置模板：{template_dir}", file=sys.stderr)
        return 2
    try:
        ensure_safe_destination(destination)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, destination, dirs_exist_ok=True)

    texfile = destination / "texfile"
    question_numbers = range(1, args.questions + 1)

    description_path = texfile / "2ProblemDescription.tex"
    description_text = description_path.read_text(encoding="utf-8")
    description_text = replace_marker_block(
        description_text,
        "% <CUMCM:QUESTION_RESTATEMENTS>",
        "% </CUMCM:QUESTION_RESTATEMENTS>",
        "\n\n".join(question_restatement(number) for number in question_numbers),
    )
    description_path.write_text(description_text, encoding="utf-8")

    analysis_path = texfile / "3ProblemAnalysis.tex"
    analysis_text = analysis_path.read_text(encoding="utf-8")
    analysis_text = replace_marker_block(
        analysis_text,
        "% <CUMCM:QUESTION_ANALYSES>",
        "% </CUMCM:QUESTION_ANALYSES>",
        "\n\n".join(question_analysis(number) for number in question_numbers),
    )
    analysis_path.write_text(analysis_text, encoding="utf-8")

    base_problem = texfile / "5Problem1.tex"
    base_text = base_problem.read_text(encoding="utf-8")
    include_lines: list[str] = []

    for number in question_numbers:
        problem_path = texfile / f"5Problem{number}.tex"
        problem_path.write_text(problem_section(base_text, number), encoding="utf-8")
        include_lines.append(f"\\input{{texfile/{problem_path.stem}}}")

    for problem_path in texfile.glob("5Problem*.tex"):
        suffix = problem_path.stem.removeprefix("5Problem")
        if suffix.isdigit() and int(suffix) > args.questions:
            problem_path.unlink()

    main_tex = destination / "document.tex"
    main_text = main_tex.read_text(encoding="utf-8")
    main_text = replace_marker_block(
        main_text,
        "% <CUMCM:PROBLEM_INPUTS>",
        "% </CUMCM:PROBLEM_INPUTS>",
        "\n".join(include_lines),
    )
    main_tex.write_text(main_text, encoding="utf-8")

    (texfile / "figures").mkdir(exist_ok=True)
    (destination / "reports").mkdir(exist_ok=True)

    print(f"已创建 CUMCM XeLaTeX 工程：{destination}")
    print(f"子问题数量：{args.questions}")
    print(f"论文入口：{main_tex}")
    print("先完成 reports 中的两份写作门槛报告；状态未通过时不得生成正式正文。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
