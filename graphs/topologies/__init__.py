"""Topology plugins. Each module exposes ``node_implementations(ctx) -> dict``
with optional ``kind::<kind>`` factories, ``node::<id>`` virtual nodes and
``route::<rule>`` routers consumed by graphs.builder.build_graph."""
