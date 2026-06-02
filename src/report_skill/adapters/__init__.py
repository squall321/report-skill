"""Per-widget adapters — normalize anything-ish input to widget content dict.

Registry covers all 33 ReportArchive widget types (text, chart, matrix,
proportion, graph, layout, media). When ReportArchive adds a new widget,
`catalog sync` will flag it via the diff, and the corresponding adapter
file should be added here.
"""
from __future__ import annotations

from typing import Optional

from report_skill.adapters.attachment import AttachmentAdapter
from report_skill.adapters.base import WidgetAdapter
from report_skill.adapters.box import BoxAdapter
from report_skill.adapters.bulleted_list import BulletedListAdapter
from report_skill.adapters.cad_3d import Cad3dAdapter
from report_skill.adapters.chart import ChartAdapter
from report_skill.adapters.comparison import ComparisonAdapter
from report_skill.adapters.contour import ContourAdapter
from report_skill.adapters.density import DensityAdapter
from report_skill.adapters.equation import EquationAdapter
from report_skill.adapters.flowchart import FlowchartAdapter
from report_skill.adapters.heading import HeadingAdapter
from report_skill.adapters.heatmap import HeatmapAdapter
from report_skill.adapters.html_embed import HtmlEmbedAdapter
from report_skill.adapters.image import ImageAdapter
from report_skill.adapters.key_value import KeyValueAdapter
from report_skill.adapters.milestone import MilestoneAdapter
from report_skill.adapters.mind_map import MindMapAdapter
from report_skill.adapters.network import NetworkAdapter
from report_skill.adapters.packing import PackingAdapter
from report_skill.adapters.pie import PieAdapter
from report_skill.adapters.progress_bar import ProgressBarAdapter
from report_skill.adapters.quadrant import QuadrantAdapter
from report_skill.adapters.raci_matrix import RaciMatrixAdapter
from report_skill.adapters.radar import RadarAdapter
from report_skill.adapters.rich_text import RichTextAdapter
from report_skill.adapters.sankey import SankeyAdapter
from report_skill.adapters.scatter import ScatterAdapter
from report_skill.adapters.scatter3d import Scatter3dAdapter
from report_skill.adapters.table import TableAdapter
from report_skill.adapters.tree import TreeAdapter
from report_skill.adapters.treemap import TreemapAdapter
from report_skill.adapters.video import VideoAdapter
from report_skill.adapters.waffle import WaffleAdapter

ADAPTERS: dict[str, WidgetAdapter] = {
    # text
    "heading": HeadingAdapter(),
    "rich_text": RichTextAdapter(),
    "bulleted_list": BulletedListAdapter(),
    "key_value": KeyValueAdapter(),
    "table": TableAdapter(),
    "comparison": ComparisonAdapter(),
    # chart-family
    "chart": ChartAdapter(),
    "scatter": ScatterAdapter(),
    "scatter3d": Scatter3dAdapter(),
    # matrix / distribution
    "heatmap": HeatmapAdapter(),
    "contour": ContourAdapter(),
    "radar": RadarAdapter(),
    "density": DensityAdapter(),
    "box": BoxAdapter(),
    # proportion
    "pie": PieAdapter(),
    "waffle": WaffleAdapter(),
    "progress_bar": ProgressBarAdapter(),
    "treemap": TreemapAdapter(),
    "packing": PackingAdapter(),
    # graph / hierarchy
    "tree": TreeAdapter(),
    "network": NetworkAdapter(),
    "mind_map": MindMapAdapter(),
    "sankey": SankeyAdapter(),
    # layout / diagram
    "milestone": MilestoneAdapter(),
    "flowchart": FlowchartAdapter(),
    "raci_matrix": RaciMatrixAdapter(),
    "quadrant": QuadrantAdapter(),
    "equation": EquationAdapter(),
    "html_embed": HtmlEmbedAdapter(),
    # media (file_id passthrough only)
    "image": ImageAdapter(),
    "video": VideoAdapter(),
    "attachment": AttachmentAdapter(),
    "cad_3d": Cad3dAdapter(),
}


def for_type(widget_type: str) -> Optional[WidgetAdapter]:
    return ADAPTERS.get(widget_type)


def all_types() -> list[str]:
    return list(ADAPTERS.keys())
