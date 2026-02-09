"""LangGraph-based data collection workflow (A -> B -> C).

This module provides the graph structure only.
Node implementations are placeholders and should be expanded by individual agents.
"""

from __future__ import annotations

from typing import Any, Dict, List

from langgraph.graph import StateGraph

from app.nodes.a_fetch_restaurants import node_a_fetch_restaurants
from app.nodes.b_fetch_menus import node_b_fetch_menus
from app.nodes.c_fetch_nutrition import node_c_fetch_nutrition

State = Dict[str, Any]


def build_graph():
    graph = StateGraph(dict)

    graph.add_node("A_fetch_restaurants", node_a_fetch_restaurants)
    graph.add_node("B_fetch_menus", node_b_fetch_menus)
    graph.add_node("C_fetch_nutrition", node_c_fetch_nutrition)

    graph.set_entry_point("A_fetch_restaurants")
    graph.add_edge("A_fetch_restaurants", "B_fetch_menus")
    graph.add_edge("B_fetch_menus", "C_fetch_nutrition")

    return graph.compile()


__all__: List[str] = [
    "build_graph",
    "node_a_fetch_restaurants",
    "node_b_fetch_menus",
    "node_c_fetch_nutrition",
]
