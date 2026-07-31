\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{cumcmthesis}[2026/07/26 clean CUMCM XeLaTeX class]

\newif\ifcumcm@bwprint
\DeclareOption{withoutpreface}{}
\DeclareOption{bwprint}{\cumcm@bwprinttrue}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{ctexart}}
\ProcessOptions\relax
\LoadClass[UTF8,a4paper,12pt]{ctexart}

\RequirePackage{iftex}
\ifXeTeX\else
  \ClassError{cumcmthesis}{This class requires XeLaTeX}{Compile with xelatex.}
\fi

\RequirePackage[a4paper,top=25mm,bottom=25mm,left=25mm,right=25mm]{geometry}
\RequirePackage{fontspec}
\RequirePackage{amsmath,amssymb,mathtools,bm}
\RequirePackage{graphicx}
\RequirePackage{booktabs,tabularx,array,longtable,threeparttable}
\RequirePackage{siunitx}
\RequirePackage{caption,subcaption}
\RequirePackage{float,placeins,needspace}
\RequirePackage{xcolor}
\RequirePackage{enumitem}
\RequirePackage{listings}
\RequirePackage{fancyhdr}
\RequirePackage[numbers,sort&compress]{natbib}
\RequirePackage{hyperref}

\IfFontExistsTF{Times New Roman}
  {\setmainfont{Times New Roman}}
  {\setmainfont{TeX Gyre Termes}}
\IfFontExistsTF{Arial}
  {\setsansfont{Arial}}
  {\setsansfont{TeX Gyre Heros}}
\IfFontExistsTF{SimSun}
  {\setCJKmainfont[AutoFakeBold=2.5]{SimSun}}
  {\setCJKmainfont[AutoFakeBold=2.5]{FandolSong-Regular}}
\IfFontExistsTF{SimHei}
  {\setCJKsansfont[AutoFakeBold=2.5]{SimHei}
   \newCJKfontfamily\cumcmhei[AutoFakeBold=2.5]{SimHei}}
  {\setCJKsansfont[AutoFakeBold=2.5]{FandolHei-Regular}
   \newCJKfontfamily\cumcmhei[AutoFakeBold=2.5]{FandolHei-Regular}}
\IfFontExistsTF{KaiTi}
  {\newCJKfontfamily\cumcmkai[AutoFakeBold=2.5]{KaiTi}}
  {\newCJKfontfamily\cumcmkai[AutoFakeBold=2.5]{FandolKai-Regular}}

\definecolor{cumcmblue}{HTML}{3B6FB6}
\definecolor{cumcmorange}{HTML}{D97732}
\definecolor{cumcmteal}{HTML}{2A9D8F}
\definecolor{cumcmpurple}{HTML}{7A6FAC}
\definecolor{cumcmgray}{HTML}{6B7280}
\definecolor{cumcmlight}{HTML}{F4F6F8}

\hypersetup{
  hidelinks,
  colorlinks=true,
  allcolors=black,
  pdfauthor={},
  pdfcreator={XeLaTeX},
  pdfsubject={全国大学生数学建模竞赛论文},
  pdfstartview=Fit
}

\pagestyle{fancy}
\fancyhf{}
\fancyfoot[C]{\zihao{-5}\thepage}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

\makeatletter
\renewcommand\normalsize{%
  \@setfontsize\normalsize{12.05pt}{14.45pt}%
  \abovedisplayskip 12pt plus 3pt minus 7pt%
  \abovedisplayshortskip 0pt plus 3pt%
  \belowdisplayshortskip 6.5pt plus 3.5pt minus 3pt%
  \belowdisplayskip \abovedisplayskip
}
\makeatother
\linespread{1.38}
\AtBeginDocument{\normalsize}
\setlength{\parindent}{2em}
\setlength{\parskip}{0pt}
\setlist{topsep=.3em,itemsep=.1em,parsep=0pt,leftmargin=2em}
\setcounter{secnumdepth}{3}

\ctexset{
  section = {
    format = \centering\cumcmhei\bfseries\zihao{3},
    name = {,、},
    aftername = {},
    number = \chinese{section},
    beforeskip = 3.5ex plus 1ex minus .2ex,
    afterskip = 2.3ex plus .2ex
  },
  subsection = {
    format = \cumcmhei\bfseries\zihao{-4},
    beforeskip = 3.25ex plus 1ex minus .2ex,
    afterskip = 1.5ex plus .2ex
  },
  subsubsection = {
    format = \cumcmhei\bfseries\zihao{-4},
    beforeskip = 3.25ex plus 1ex minus .2ex,
    afterskip = 1.5ex plus .2ex
  }
}

\DeclareCaptionFont{cumcmcaption}{\zihao{5}}
\captionsetup{
  format=hang,
  font=cumcmcaption,
  labelsep=quad,
  justification=centering,
  singlelinecheck=true
}
\captionsetup[figure]{position=bottom,skip=6pt}
\captionsetup[table]{position=top,skip=6pt}

\sisetup{
  detect-all,
  group-separator={\,},
  group-minimum-digits=4,
  table-number-alignment=center
}

\lstdefinestyle{cumcmcode}{
  basicstyle=\ttfamily\zihao{-5},
  numbers=left,
  numberstyle=\tiny\color{cumcmgray},
  numbersep=8pt,
  frame=single,
  rulecolor=\color{cumcmgray},
  backgroundcolor=\color{cumcmlight},
  breaklines=true,
  showstringspaces=false,
  columns=fullflexible,
  keepspaces=true,
  tabsize=4
}
\lstset{style=cumcmcode}

\makeatletter
\newenvironment{cumcmabstract}[1]{%
  \begin{center}
    {\cumcmhei\zihao{3}\bfseries\@title\par}
    \vspace{1ex}
    {\cumcmhei\zihao{4}\bfseries 摘要\par}
  \end{center}
  \def\cumcm@keywords{#1}%
  \begin{quotation}
}{%
  \par\medskip
  \noindent{\heiti 关键词：}\hspace{0.5em}\cumcm@keywords
  \end{quotation}
  \label{cumcm:abstract-end}
}
\makeatother

\newcommand{\CumcmAISectionNote}[1]{%
  \footnote{本节使用人工智能工具辅助#1，核心建模、结果核验与最终表述由参赛队独立完成并负责。}%
}
\newcommand{\CumcmBodyEnd}{%
  \FloatBarrier
  \phantomsection
  \label{cumcm:body-end}
}

\newcolumntype{Y}{>{\raggedright\arraybackslash}X}
\newcolumntype{C}{>{\centering\arraybackslash}X}
\newcolumntype{R}{>{\raggedleft\arraybackslash}X}
\newcommand{\CumcmFigureWidth}{0.82\textwidth}
\newcommand{\CumcmWideFigureWidth}{0.92\textwidth}
\newcommand{\CumcmTableWidth}{0.92\textwidth}

% Only formulae explicitly cited later receive chapter-local numbers.
% The two legacy ordinary-formula helpers remain as unnumbered wrappers.
\newcounter{cumcmformula}[section]
\renewcommand{\thecumcmformula}{\thesection-\arabic{cumcmformula}}
\newcommand{\CumcmInlineFormula}[1]{%
  \(#1\)%
}
\newenvironment{CumcmDisplayFormula}{%
  \begin{equation*}%
}{%
  \end{equation*}%
}
\newenvironment{CumcmReferencedEquation}{%
  \refstepcounter{cumcmformula}%
  \begin{equation*}\tag{\thecumcmformula}%
}{%
  \end{equation*}%
}

\renewcommand{\refname}{参考文献}
\renewcommand{\bibsection}{\section*{\cumcmhei\zihao{4}\bfseries\refname}}
\graphicspath{{texfile/figures/}}

\endinput
