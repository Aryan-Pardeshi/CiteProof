import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from fpdf import FPDF

logger = logging.getLogger(__name__)

# Serif families first: academic papers are set in a serif face. Each entry is
# (regular, bold, italic); missing faces fall back to the regular file.
UNICODE_FONT_CANDIDATES = [
    (os.getenv("PDF_FONT_PATH"), os.getenv("PDF_FONT_PATH_BOLD"), os.getenv("PDF_FONT_PATH_ITALIC")),
    ("C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf", "C:/Windows/Fonts/timesi.ttf"),
    ("C:/Windows/Fonts/georgia.ttf", "C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/georgiai.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
]

# Fallback transliterations, used only when no Unicode TTF is available on the host
LATIN1_SUBSTITUTIONS = {
    **{chr(0x2080 + d): str(d) for d in range(10)},
    "\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3",
    **{chr(0x2074 + d): str(d + 4) for d in range(6)},
    "\u2212": "-", "\u2013": "-", "\u2014": "-", "\u2010": "-", "\u2011": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2026": "...", "\u2192": "->", "\u2190": "<-", "\u2194": "<->",
    "\u21d0": "<=", "\u21d2": "=>", "\u21d4": "<=>", "\u21d1": "^", "\u21d3": "v",
    "\u2248": "~", "\u2264": "<=", "\u2265": ">=", "\u2260": "!=", "\u2261": "==",
    "\u2200": "forall", "\u2203": "exists", "\u2208": "in", "\u2209": "notin",
    "\u220f": "prod", "\u2211": "sum", "\u221a": "sqrt",
    "\u00d7": "x", "\u2022": "-", "\u2032": "'", "\u2033": '"',
    "\u03b1": "alpha", "\u03b2": "beta", "\u03b3": "gamma", "\u03b4": "delta",
    "\u03b5": "epsilon", "\u03b8": "theta", "\u03bb": "lambda", "\u03bc": "mu",
    "\u03c0": "pi", "\u03c1": "rho", "\u03c3": "sigma", "\u03c4": "tau",
    "\u03c6": "phi", "\u03c7": "chi", "\u03c9": "omega", "\u03a9": "Ohm",
    "\u0394": "Delta", "\u03a3": "Sigma", "\u212b": "Angstrom",
}


# Page geometry follows the single-column arXiv/NeurIPS preprint style: US Letter,
# 1 inch margins, 10pt body. Measurements are mm (1 in = 25.4 mm).
MARGIN = 25.4
BODY_SIZE = 10
BODY_LEADING = 4.9
ABSTRACT_INSET = 10      # extra indent each side of the abstract block
TITLE_SIZE = 17
SECTION_SIZE = 12
SUBSECTION_SIZE = 11
REFERENCE_SIZE = 9


def _register_fonts(pdf: FPDF) -> str | None:
    """Registers the first available serif TTF with bold and italic faces."""
    for regular, bold, italic in UNICODE_FONT_CANDIDATES:
        if not regular or not Path(regular).is_file():
            continue
        family = "PaperSerif"
        try:
            pdf.add_font(family, "", regular)
            # fpdf2 needs every style it may be asked for; reuse the regular file
            # rather than raising when a face is missing.
            bold_file = bold if bold and Path(bold).is_file() else regular
            italic_file = italic if italic and Path(italic).is_file() else regular
            pdf.add_font(family, "B", bold_file)
            pdf.add_font(family, "I", italic_file)
            # Bold-italic is requested by subsection headings; without it fpdf2 raises
            pdf.add_font(family, "BI", bold_file)
            return family
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not register PDF font {regular}: {e}")
    return None


def _split_markdown_blocks(text: str) -> list[tuple[str, str, int]]:
    """Parses agent Markdown into (kind, text, level) blocks for the renderer.

    Kinds: heading, bullet, numbered, paragraph. Horizontal rules are dropped.
    """
    blocks: list[tuple[str, str, int]] = []
    paragraph: list[str] = []

    def flush():
        if paragraph:
            blocks.append(("paragraph", " ".join(paragraph).strip(), 0))
            paragraph.clear()

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush()
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush()
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush()
            blocks.append(("heading", heading.group(2).strip(), len(heading.group(1))))
            continue

        bullet = re.match(r"^[-*\u2022]\s+(.*)$", stripped)
        if bullet:
            flush()
            blocks.append(("bullet", bullet.group(1).strip(), 0))
            continue

        numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if numbered:
            flush()
            blocks.append(("numbered", f"{numbered.group(1)}. {numbered.group(2).strip()}", 0))
            continue

        paragraph.append(stripped)

    flush()
    return blocks


def generate_paper_pdf(state: dict[str, Any], output_path: str) -> str:
    """Renders a completed ResearchMode paper as a formatted academic PDF."""
    paper_title = (state.get("title") or "Academic Research Paper").strip()

    class AcademicPDF(FPDF):
        body_family = "Helvetica"

        # Preprints in this style carry no running head; a centred folio on every
        # page, first page included, is the whole of the page furniture.
        def footer(self):
            self.set_y(-15)
            self.set_font(self.body_family, "", 9)
            self.set_text_color(60, 60, 60)
            self.cell(0, 6, f"{self.page_no()}", border=0, align="C")

    pdf = AcademicPDF(format="Letter")
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(auto=True, margin=MARGIN)
    pdf.alias_nb_pages()

    family = _register_fonts(pdf)
    unicode_font = family is not None
    body_family = family or "Helvetica"
    pdf.body_family = body_family
    bullet_char = "\u2022" if unicode_font else "-"

    def clean(text: Any) -> str:
        if not text:
            return ""
        text = str(text)
        if unicode_font:
            return text
        for char, replacement in LATIN1_SUBSTITUTIONS.items():
            if char in text:
                text = text.replace(char, replacement)
        return text.encode("latin-1", "replace").decode("latin-1")

    def escape_markdown(text: str) -> str:
        """Neutralizes stray markers so fpdf2's markdown parser cannot mis-pair them."""
        return text.replace("__", "").replace("--", "-")

    def mc(w, h, txt, **kwargs):
        """multi_cell that always returns the cursor to the left margin.

        fpdf2 leaves x at the right edge by default, which starves the next
        full-width cell of horizontal space.
        """
        kwargs.setdefault("new_x", "LMARGIN")
        kwargs.setdefault("new_y", "NEXT")
        return pdf.multi_cell(w, h, txt, **kwargs)

    def body_text(text: str, size: float = BODY_SIZE, align: str = "J", indent: float = 0.0):
        pdf.set_font(body_family, "", size)
        pdf.set_text_color(25, 25, 25)
        if indent:
            pdf.set_x(MARGIN + indent)
        mc(
            pdf.w - 2 * MARGIN - indent, BODY_LEADING, clean(escape_markdown(text)),
            align=align, markdown=True
        )

    def render_markdown(text: str):
        """Renders agent Markdown: headings, lists, and inline bold become real styling."""
        for kind, content, level in _split_markdown_blocks(text):
            if kind == "heading":
                pdf.ln(2)
                pdf.set_font(body_family, "B", BODY_SIZE if level >= 3 else BODY_SIZE + 0.5)
                pdf.set_text_color(30, 30, 30)
                mc(0, BODY_LEADING, clean(re.sub(r"\*+", "", content)))
                pdf.ln(1)
            elif kind == "bullet":
                body_text(f"{bullet_char}  {content}", align="L", indent=6)
            elif kind == "numbered":
                body_text(content, align="L", indent=6)
            else:
                body_text(content)
                pdf.ln(1.5)

    def section(number: int | None, heading: str, body: Any, markdown: bool = True):
        if not body:
            return
        pdf.ln(4)
        pdf.set_font(body_family, "B", SECTION_SIZE)
        pdf.set_text_color(15, 15, 15)
        label = f"{number}. {heading}" if number is not None else heading
        mc(0, 6.5, clean(label))
        pdf.ln(1)
        if isinstance(body, list):
            for item in body:
                body_text(f"{bullet_char}  {item}", align="L", indent=6)
        elif markdown:
            render_markdown(str(body))
        else:
            body_text(str(body))
        pdf.ln(2)

    def subsection(label: str, body: Any):
        if not body:
            return
        pdf.ln(1)
        pdf.set_font(body_family, "B", SUBSECTION_SIZE)
        pdf.set_text_color(30, 30, 30)
        mc(0, 5.6, clean(label))
        render_markdown(str(body))
        pdf.ln(1)

    # --- Title block --------------------------------------------------------
    # Preprint style: no separate title page. Title, abstract, and the opening
    # section all share page one, exactly as an arXiv submission does.
    pdf.add_page()
    pdf.ln(8)
    pdf.set_font(body_family, "B", TITLE_SIZE)
    pdf.set_text_color(0, 0, 0)
    mc(0, 8, clean(paper_title), align="C")
    pdf.ln(4)

    pdf.set_font(body_family, "", BODY_SIZE + 1)
    pdf.set_text_color(40, 40, 40)
    mc(0, 5.5, clean("Autonomous Research Synthesis"), align="C")
    pdf.set_font(body_family, "", BODY_SIZE)
    pdf.set_text_color(90, 90, 90)
    mc(0, 5.5, clean(date.today().strftime("%d %B %Y")), align="C")
    pdf.ln(6)

    abstract = state.get("abstract")
    if abstract:
        pdf.set_font(body_family, "B", BODY_SIZE + 1)
        pdf.set_text_color(0, 0, 0)
        mc(0, 5.5, clean("Abstract"), align="C")
        pdf.ln(1.5)
        # The abstract sits in a narrower measure than the body column
        pdf.set_left_margin(MARGIN + ABSTRACT_INSET)
        pdf.set_right_margin(MARGIN + ABSTRACT_INSET)
        pdf.set_x(MARGIN + ABSTRACT_INSET)
        render_markdown(str(abstract))
        pdf.set_left_margin(MARGIN)
        pdf.set_right_margin(MARGIN)
        pdf.set_x(MARGIN)

    keywords = state.get("keywords") or []
    if keywords:
        pdf.ln(1)
        pdf.set_left_margin(MARGIN + ABSTRACT_INSET)
        pdf.set_right_margin(MARGIN + ABSTRACT_INSET)
        pdf.set_x(MARGIN + ABSTRACT_INSET)
        pdf.set_font(body_family, "B", BODY_SIZE - 0.5)
        pdf.set_text_color(40, 40, 40)
        mc(0, 5, clean("Keywords: " + "; ".join(keywords)), align="L")
        pdf.set_left_margin(MARGIN)
        pdf.set_right_margin(MARGIN)
        pdf.set_x(MARGIN)

    figures = state.get("figures") or {}

    def embed_figure(image_path: str | None, caption: str):
        if not image_path or not Path(image_path).is_file():
            return
        try:
            col_width = pdf.w - 2 * MARGIN
            try:
                from PIL import Image
                with Image.open(image_path) as img:
                    w_px, h_px = img.size
                aspect = h_px / w_px
            except Exception:  # noqa: BLE001
                aspect = 0.7

            img_w = col_width
            img_h = col_width * aspect
            max_h = pdf.h - 2 * MARGIN - 20
            if img_h > max_h:
                img_h = max_h
                img_w = img_h / aspect

            space_left = pdf.h - pdf.get_y() - MARGIN
            if space_left < img_h + 15:
                pdf.add_page()

            x = MARGIN + (col_width - img_w) / 2
            pdf.image(image_path, x=x, y=pdf.get_y(), w=img_w, h=img_h)
            pdf.set_y(pdf.get_y() + img_h + 2)

            pdf.set_font(body_family, "I", BODY_SIZE - 1)
            pdf.set_text_color(60, 60, 60)
            mc(0, 4.5, clean(caption), align="C")
            pdf.ln(4)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not embed figure '{caption}' from {image_path}: {e}")

    # --- Body flows on from the abstract, no page break ----------------------
    pdf.ln(4)
    section(1, "Introduction", state.get("introduction"))
    section(2, "Literature Review", state.get("literature_review"))
    section(3, "Research Gap", state.get("research_gap"))
    section(4, "Research Objectives", state.get("research_objectives"))
    section(5, "Research Questions", state.get("research_questions"))
    section(6, "Conceptual Framework", state.get("conceptual_framework"))
    section(7, "Hypotheses", state.get("hypotheses"))

    if any(state.get(k) for k in ("research_design", "data_collection_plan", "data_analysis_plan")):
        pdf.ln(3)
        pdf.set_font(body_family, "B", BODY_SIZE + 2)
        pdf.set_text_color(15, 15, 15)
        mc(0, 7, clean("8. Methodology"))
        pdf.ln(1)
        subsection("8.1 Research Design", state.get("research_design"))
        subsection("8.2 Data Collection", state.get("data_collection_plan"))
        subsection("8.3 Data Analysis", state.get("data_analysis_plan"))
        pdf.ln(2)

    embed_figure(figures.get("prisma"), "Figure 1: Study selection flow")

    section(9, "Results", state.get("results"))
    embed_figure(figures.get("evidence"), "Figure 2: Evidence mapping by hypothesis")

    if state.get("discussion"):
        section(10, "Discussion", state.get("discussion"))
        if state.get("implications"):
            subsection("10.1 Implications", state.get("implications"))

    section(11, "Limitations", state.get("limitations"))
    section(12, "Conclusion", state.get("conclusion"))
    section(13, "Future Scope", state.get("future_scope"))


    # --- References ---------------------------------------------------------
    references = state.get("references") or []
    if references:
        pdf.add_page()
        pdf.set_font(body_family, "B", SECTION_SIZE)
        pdf.set_text_color(15, 15, 15)
        mc(0, 6.5, clean("References"))
        pdf.ln(2)
        pdf.set_font(body_family, "", REFERENCE_SIZE)
        pdf.set_text_color(25, 25, 25)
        # APA orders the list alphabetically with a hanging indent: the first line
        # sits flush left while turnover lines are indented. Setting the left margin
        # past MARGIN and then starting the first line at MARGIN produces exactly that.
        hang = 8
        pdf.set_left_margin(MARGIN + hang)
        for ref in sorted(references, key=lambda r: str(r).lower()):
            pdf.set_x(MARGIN)
            mc(pdf.w - MARGIN - (MARGIN + hang) + hang, 4.4,
                           clean(escape_markdown(str(ref))), align="L")
            pdf.ln(1.0)
        pdf.set_left_margin(MARGIN)

    # --- Appendices ---------------------------------------------------------
    if state.get("appendices"):
        pdf.add_page()
        pdf.set_font(body_family, "B", SECTION_SIZE)
        pdf.set_text_color(15, 15, 15)
        mc(0, 6.5, clean("Appendices"))
        pdf.ln(2)
        render_markdown(str(state.get("appendices")))

    pdf.output(output_path)
    return output_path
