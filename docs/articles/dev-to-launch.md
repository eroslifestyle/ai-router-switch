---
title: How I Built a Multi-Backend AI Router for Claude Code (and Saved My Workflow When My Subscription Glitched)
published: false
description: A self-hosted Python proxy that switches between Claude and MiniMax with automatic failover, context compression, and a smart cost-saving mode.
tags: ai, python, opensource, devops, linux
cover_image: https://raw.githubusercontent.com/eroslifestyle/ai-router-switch/main/assets/banner.png
canonical_url: https://github.com/eroslifestyle/ai-router-switch
---

## The Hook

Last month, mid-sprint, my Claude subscription decided to take a vacation. Three days of "Service Unavailable" while I had a feature to ship. That's when I decided: no more single points of failure in my AI stack.

## Background: Why Claude Code Alone Isn't Enough

Claude Code is excellent. The context window, the reasoning, the tool use. But it's a subscription service with its own limits: rate caps, availability windows, and that annoying moment when your plan doesn't cover what you need right now.

MiniMax offers a solid alternative, but their API behaves differently. Different endpoints, different parameters, different quirks. Switching manually between them is a workflow killer.

I needed a proxy that could sit in front of both, route intelligently based on what I'm doing, and fail over automatically when something breaks.

## What I Built: Four Routing Modes

The router operates in four distinct modes:

1. **Forced Claude** - Everything goes to Claude. Use when you need maximum reasoning capability.
2. **Forced MiniMax** - Everything goes to MiniMax. Cheaper tasks, bulk processing.
3. **Automatic** - The proxy decides based on task complexity. Simple queries to MiniMax, complex ones to Claude.
4. **Interactive (Smart Mode)** - The proxy categorizes tasks in real-time and routes accordingly, with manual override available.

The interactive mode is where it gets interesting. I built a lightweight classifier that distinguishes between:

- **T0/T1 tasks**: Quick edits, single-file changes, documentation updates
- **T2 tasks**: Multi-file refactors, architecture decisions, complex debugging

## Architecture: Three Services + One Proxy

The system runs as:

```
ai-router-proxy.py    # The HTTP proxy (port 8080)
ai-router-switcher.sh # Service switcher script
ai-health-checker.sh  # Health monitoring daemon
```

Each runs as a systemd service:

```
~/.config/systemd/user/ai-router-proxy.service
~/.config/systemd/user/ai-router-switcher.service
~/.config/user/ai-router-health-checker.service
```

The proxy listens locally, forwards to whichever backend is active, and handles the translation between API formats.

## Smart Mode: How It Categorizes Tasks

The routing logic examines the incoming request to determine complexity:

- Request length and structure
- Presence of code patterns (imports, function definitions, class declarations)
- Explicit mode hints in the request
- Token count estimates

Simple prompts with minimal context go to MiniMax. Anything resembling a full refactor or complex reasoning task gets routed to Claude.

## Triple Defense: Service Resilience

One service failing shouldn't break your workflow. The resilience stack:

1. **systemd restart policies** - Each service auto-restarts on failure with backoff
2. **cron health checks** - Every 5 minutes, a script verifies both backends and switches if needed
3. **pre-request hooks** - The proxy checks backend health before each request

If Claude goes down, the system detects it within 5 minutes (cron interval) or immediately (hook). If MiniMax fails, same story. You keep working.

## The Core Proxy Logic

Here's the essential routing function from `ai-router-proxy.py`:

```python
def handle_request(req_data, mode):
    if mode == "claude":
        return forward(req_data, CLAUDE_ENDPOINT)
    elif mode == "minimax":
        return forward(req_data, MINIMAX_ENDPOINT)
    elif mode == "auto":
        complexity = classify(req_data)
        target = CLAUDE_ENDPOINT if complexity > 0.7 else MINIMAX_ENDPOINT
        return forward(req_data, target)
    elif mode == "interactive":
        task_type = detect_task_type(req_data)
        if task_type in ["edit", "doc", "refactor"]:
            return forward(req_data, MINIMAX_ENDPOINT)
        return forward(req_data, CLAUDE_ENDPOINT)
```

Ten lines that handle four modes, with the complexity classifier being the real intelligence.

## Real-World Use Cases

1. **Documentation writing** - MiniMax handles the bulk. When I need to explain an architecture decision, Claude takes over.
2. **Bug hunting** - Automatic mode detects the complexity and routes to Claude for the heavy lifting.
3. **Code review** - Interactive mode identifies review patterns and routes accordingly.
4. **Bulk refactoring** - Forced MiniMax mode for repetitive changes where I just need speed.

## Performance and Results

After a month of daily use:

- Switchover time when a backend fails: under 10 seconds
- Routing accuracy in auto mode: approximately 85% (based on manual overrides)
- Cost savings from MiniMax for simple tasks: roughly 40% compared to Claude-only
- Zero workflow interruptions from backend issues: 31 days and counting

## What I Learned

Building a resilient AI proxy taught me three things:

1. **Failover is easy; failback is hard** - Detecting when a service comes back and switching back requires careful state management.
2. **Classification is the hard part** - The routing logic is simple; knowing when to route is the actual problem.
3. **Local proxies are underrated** - A simple HTTP proxy solved what I thought would require a complex service mesh.

## Links and Call to Action

The full project is open source:

- **Repository**: https://github.com/eroslifestyle/ai-router-switch
- **Documentation**: https://eroslifestyle.github.io/ai-router-switch/
- **Banner/Assets**: https://github.com/eroslifestyle/ai-router-switch/tree/main/assets
- **Discussion**: https://github.com/eroslifestyle/ai-router-switch/discussions/2

If you've been burned by AI service downtime, or if you want to optimize costs without sacrificing capability, clone the repo and run the installer. The setup script handles the systemd units, the proxy configuration, and the health checks.

Questions, contributions, and feature requests welcome in the discussions.
