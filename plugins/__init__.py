"""Plugin system for WhatsBot-Lite.

Plugins live in ``storages/plugins/<id>/`` and can extend the app with new
tools, prompt fragments, REST routes, screens, settings and DB migrations.

The core tool registry uses the same ``ToolContext`` contract as plugins, so
core tools and plugin tools are dispatched uniformly by ``AgentHandler``.
"""
