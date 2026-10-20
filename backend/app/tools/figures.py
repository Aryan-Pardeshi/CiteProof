"""Figure rendering tools for academic research reports.

Generates standard PRISMA 2020 flow diagrams and structured evidence mapping tables
with Swiss Modernism 2.0 aesthetics and high-DPI rasterization.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from backend.app.models.evidence import PaperRecord, PRISMATracker


def render_prisma_diagram(
    tracker_or_stats: PRISMATracker | dict[str, Any],
    output_path: str
) -> str:
    """Render an accurate, publication-quality PRISMA 2020 flow diagram.

    Reads directly from PRISMATracker data models guaranteeing mathematical consistency.
    """
    if isinstance(tracker_or_stats, PRISMATracker):
        tr = tracker_or_stats
    elif isinstance(tracker_or_stats, dict):
        tr = PRISMATracker(**{k: v for k, v in tracker_or_stats.items() if k in PRISMATracker.model_fields})
    else:
        tr = PRISMATracker()

    retrieved = tr.records_identified
    after_dedup = tr.records_after_dedup
    screened = tr.records_screened
    excluded_screen = tr.excluded_title_abstract
    full_text_assessed = tr.full_text_assessed
    excluded_ft = tr.excluded_full_text
    included = tr.studies_included
    dup_removed = tr.duplicates_removed

    # Format database source breakdown if available
    source_breakdown = ""
    if tr.records_by_source:
        parts = [f"{src.replace('_', ' ').title()} (n={cnt})" for src, cnt in tr.records_by_source.items() if cnt > 0]
        if parts:
            source_breakdown = "\n[" + ", ".join(parts[:4]) + "]"

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(9.5, 8.0))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")

    # Swiss Modernism color palette
    box_style = {"boxstyle": "round,pad=0.5", "facecolor": "#F8FAFC", "edgecolor": "#1E3A5F", "linewidth": 1.5}
    side_box_style = {"boxstyle": "round,pad=0.4", "facecolor": "#F1F5F9", "edgecolor": "#64748B", "linewidth": 1.2}
    arrow_props = {"arrowstyle": "->", "color": "#1E3A5F", "lw": 1.5, "mutation_scale": 15}
    side_arrow_props = {"arrowstyle": "->", "color": "#64748B", "lw": 1.2, "mutation_scale": 12}

    boxes = [
        (4.2, 9.0, f"Identification\nRecords identified from databases{source_breakdown}\n(n = {retrieved})"),
        (4.2, 7.0, f"Deduplication\nRecords retained after duplicate removal\n(n = {after_dedup})"),
        (4.2, 5.0, f"Screening\nRecords screened for relevance\n(n = {screened})"),
        (4.2, 3.0, f"Eligibility\nFull-text articles assessed for eligibility\n(n = {full_text_assessed})"),
        (4.2, 1.0, f"Included\nStudies included in synthesis & analysis\n(n = {included})"),
    ]

    # Draw vertical flow boxes
    for x, y, text in boxes:
        ax.text(
            x, y, text,
            ha="center", va="center", fontsize=9.5, fontweight="bold", color="#0F172A",
            bbox=box_style
        )

    # Downward connection arrows
    ax.annotate("", xy=(4.2, 7.7), xytext=(4.2, 8.3), arrowprops=arrow_props)
    ax.annotate("", xy=(4.2, 5.7), xytext=(4.2, 6.3), arrowprops=arrow_props)
    ax.annotate("", xy=(4.2, 3.7), xytext=(4.2, 4.3), arrowprops=arrow_props)
    ax.annotate("", xy=(4.2, 1.7), xytext=(4.2, 2.3), arrowprops=arrow_props)

    # Side Exclusion Box 1: Duplicates
    ax.annotate("", xy=(7.8, 8.0), xytext=(4.2, 8.0), arrowprops=side_arrow_props)
    ax.text(
        7.9, 8.0, f"Duplicates excluded\n(n = {dup_removed})",
        ha="left", va="center", fontsize=9.0, color="#334155",
        bbox=side_box_style
    )

    # Side Exclusion Box 2: Title/Abstract Excluded
    ax.annotate("", xy=(7.8, 4.0), xytext=(4.2, 4.0), arrowprops=side_arrow_props)
    ax.text(
        7.9, 4.0, f"Records excluded by title/abstract\n(n = {excluded_screen})\n[Irrelevant to research objectives]",
        ha="left", va="center", fontsize=8.5, color="#334155",
        bbox=side_box_style
    )

    # Side Exclusion Box 3: Full-text Excluded
    if excluded_ft > 0:
        ax.annotate("", xy=(7.8, 2.0), xytext=(4.2, 2.0), arrowprops=side_arrow_props)
        ax.text(
            7.9, 2.0, f"Full-text excluded with reasons\n(n = {excluded_ft})\n[Methodological divergence]",
            ha="left", va="center", fontsize=8.5, color="#334155",
            bbox=side_box_style
        )

    try:
        ax.set_title("PRISMA 2020 Flow Diagram of Study Selection", fontsize=12, fontweight="bold", pad=15, color="#0F172A")
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
    finally:
        plt.close(fig)
    return output_path


def render_evidence_table(
    papers_or_records: list[PaperRecord | dict[str, Any]],
    hypotheses: list[str],
    output_path: str
) -> str:
    """Render an evidence mapping table figure mapping papers to hypothesis support."""
    papers: list[dict[str, Any]] = [
        p.model_dump() if isinstance(p, PaperRecord) else dict(p)
        for p in papers_or_records
    ]

    has_support = any("hypothesis_support" in p and p["hypothesis_support"] for p in papers)
    if not has_support:
        return ""

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    sorted_papers = sorted(
        papers,
        key=lambda p: (float(p.get("relevance_score") or 0), int(p.get("citation_count") or 0)),
        reverse=True
    )[:15]

    h_labels = [f"H{i+1}" for i in range(len(hypotheses))] if hypotheses else ["H1"]
    col_labels = ["Paper Title (Top 15 Included Studies)"] + h_labels
    n_hyp_cols = len(h_labels)

    cell_text = []
    for p in sorted_papers:
        raw_title = str(p.get("title") or "")
        title = (raw_title[:54] + "...") if len(raw_title) > 57 else raw_title
        sup_map = p.get("hypothesis_support") or {}
        row = [title]
        for idx, h in enumerate(hypotheses or ["H1"]):
            h_key = f"H{idx+1}"
            raw_val = str(sup_map.get(h_key) or sup_map.get(h) or "Neutral")
            val = (raw_val[:22] + "...") if len(raw_val) > 25 else raw_val
            row.append(val)
        cell_text.append(row)

    title_width = 0.48
    hyp_width = (1.0 - title_width) / max(n_hyp_cols, 1)
    col_widths = [title_width] + [hyp_width] * n_hyp_cols

    fig, ax = plt.subplots(figsize=(11.5, max(2.5, 0.42 * len(cell_text) + 1.2)))
    try:
        ax.axis("off")

        table = ax.table(
            cellText=cell_text,
            colLabels=col_labels,
            colWidths=col_widths,
            loc="center",
            cellLoc="left"
        )
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1.2, 1.4)

        # Style header row and cells
        for (row_idx, col_idx), cell in table.get_celld().items():
            if row_idx == 0:
                cell.set_facecolor("#1E3A5F")
                cell.get_text().set_color("#FFFFFF")
                cell.get_text().set_weight("bold")
                cell.get_text().set_ha("center" if col_idx > 0 else "left")
            else:
                if col_idx > 0:
                    cell.get_text().set_ha("center")
                    text_val = cell.get_text().get_text().lower()
                    if any(w in text_val for w in ("unsupported", "not support", "no support", "refute", "reject")):
                        cell.set_facecolor("#FEF2F2")  # Soft rose
                    elif any(w in text_val for w in ("partial", "inconclusive", "mixed")):
                        cell.set_facecolor("#FEFCE8")  # Soft amber
                    elif any(w in text_val for w in ("support", "yes", "confirm")):
                        cell.set_facecolor("#ECFDF5")  # Soft emerald

        ax.set_title("Evidence Mapping by Formulated Hypothesis", fontsize=11, fontweight="bold", pad=12, color="#0F172A")
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
    finally:
        plt.close(fig)
    return output_path
