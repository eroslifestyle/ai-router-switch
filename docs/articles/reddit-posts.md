===FILE_START===
# Reddit Posts - AI Router Proxy

## r/LocalLLaMA

**Title:** Built a self-hosted AI Router Proxy for Claude Code with multi-backend switching (MIT)

**Body:**

Hey everyone. I built a proxy service that sits between Claude Code and multiple AI backends, letting you mix proprietary and open-weight models depending on what the task needs.

The core use case: Claude Code as primary, automatic fallback to Ollama/local models when you want to keep data in-house or save costs on simple refactors.

### Architecture

Three systemd services + one aiohttp reverse proxy:

```
┌─────────────────────────────────────────────────────────┐
│                    aiohttp Proxy                        │
│              (handles routing + failover)               │
└─────────────────┬───────────────────────┬────────────────┘
                  │                       │
        ┌─────────▼─────────┐   ┌─────────▼─────────┐
        │   Claude API      │   │   Ollama          │
        │ (claude-3-5-sonnet)│   │ (llama3.1:8b)    │
        └───────────────────┘   └───────────────────┘
```

### 4 Routing Modes

| Mode | Behavior | Best For |
|------|----------|----------|
| `claude-only` | All requests to Claude | Complex tasks, debugging |
| `claude-cheap` | Simple tasks → Ollama, complex → Claude | Cost optimization |
| `local-first` | Ollama primary, Claude fallback | Data-sensitive tasks |
| `round-robin` | Distributes across backends | Load testing |

### Code Snippet (proxy/router.py)

```python
async def route_request(request: Request) -> Response:
    mode = await get_routing_mode()
    task_complexity = await estimate_complexity(request)
    
    if mode == "claude-only" or task_complexity == "high":
        return await proxy_to_claude(request)
    
    if mode == "local-first":
        try:
            return await proxy_to_ollama(request, timeout=30)
        except TimeoutError:
            return await proxy_to_claude(request)
    
    if mode == "claude-cheap" and task_complexity == "low":
        return await proxy_to_ollama(request)
    
    return await proxy_to_claude(request)
```

### Why I Open-Sourced It

Two reasons:

1. **No vendor lock-in**: If Anthropic changes pricing or availability, my workflow shouldn't break.
2. **Learning project**: Built this to understand aiohttp routing, systemd service management, and API proxy patterns. Sharing in case it's useful to others doing similar experiments.

### Repo

[github.com/eroslifestyle/ai-router-switch](https://github.com/eroslifestyle/ai-router-switch)

MIT license, contributions welcome.

---

AMA in comments. Happy to discuss the routing logic, systemd setup, or trade-offs I made choosing aiohttp over nginx.

---

## r/ClaudeAI

**Title:** AI Router Proxy: instant failover + 90% cost savings on simple tasks

**Body:**

Quick background: I was tired of Claude Code being unresponsive during peak hours, so I built a lightweight proxy that handles automatic failover and routes simple tasks to cheaper local models.

### The Problem (my use case)

- **Downtime frustration**: Claude Code unavailable during high-traffic periods
- **Cost on routine tasks**: Paying Claude rates for simple file renames and doc comments
- **No control**: Zero ability to prioritize or fallback when things break

### What It Does

A self-hosted proxy that:
- Routes requests based on task complexity
- Falls back to local Ollama models when Claude is slow/unavailable
- Saves you money on boring tasks while keeping Claude for real work

### 3 Use Cases

**Developer working on sensitive code:**
Keep proprietary business logic local. The proxy routes data-sensitive requests to Ollama by default, Claude only for complex refactoring tasks.

**Student learning to code:**
90% of "help me understand this error" questions are answered fine by llama3.1. Route those locally, save your Claude quota for when you actually need the heavy lifting.

**Solo dev/marketer:**
Writing 10 variations of cold emails? Local model. Debugging a gnarly recursion bug? Claude. The proxy decides, you just code.

### Try It In 5 Minutes

I wrote a setup guide here: https://eroslifestyle.github.io/ai-router-switch/
Single command install, works on any machine with Docker.

### Feedback Wanted

I've been using this for 2 weeks. What features would make this actually useful for your workflow? Any red flags with the approach?

Constructive criticism welcome — genuinely trying to build something that solves real problems.

---

## Posting Strategy

### Order of Posting

1. **r/LocalLLaMA first** (Tuesday or Wednesday)
   - More forgiving audience for technical self-promotion
   - Build credibility with detailed technical response
   
2. **r/ClaudeAI second** (same week, Friday)
   - After seeing reception on r/LocalLLaMA
   - Adjust messaging based on feedback received

### Timing (US Eastern)

- **r/LocalLLaMA**: Tuesday 9am EST (high traffic, dev-focused)
- **r/ClaudeAI**: Friday 7pm EST (people browsing, more casual)

### Notes

- Wait 48 hours between posts to avoid spam detection
- Comment on own post within first 30 minutes to boost visibility
- Monitor for critical comments and respond within 2 hours
- Do NOT post the same content to both; customize per audience

---

## Come Rispondere a Commenti Critici

### "Why not just use X instead?"

> Fair point. X does handle this well for single-model setups. The proxy shines when you want mixed backends + automatic routing based on task type. Different tradeoffs. Check the README for the specific use cases I built it for.

### "This is overengineered / not needed"

> I hear you. For simple use cases, yeah, it's overkill. I built it because I wanted the routing logic + failover in one place for my specific workflow. YMMV depending on what you're optimizing for.

### "Self-hosted? Sounds like a pain to maintain"

> Valid concern. The systemd services are designed to auto-restart on failure. For my use case (home lab setup), it's been solid for 2 weeks. If you want zero-maintenance, managed services make more sense.

### "This will break when Claude changes their API"

> Already handled in the architecture. The proxy catches API errors and routes to fallback. If the API changes significantly, I'll update the adapter. It's a known trade-off of not being fully dependent on one provider.

### "Why MIT license? Should be AGPL / proprietary"

> Personal choice. MIT lets anyone use it, modify it, and integrate it anywhere. If you need AGPL for your compliance requirements, fork it. No judgment either way.

### "Is this safe? Feeding data to random proxies?"

> Legitimate question. The proxy runs locally on your machine. Data only leaves your network if you explicitly configure it to route to Claude. Local Ollama requests never leave your machine. Review the code yourself — it's 300 lines.

### "You're just shilling your own product"

> Guilty as charged, I built it. Posting here because I thought the community might find it useful. No affiliate links, no upsell. If it's not useful for you, totally fair to skip.

---

*Last updated: documentation purposes only. Actual posting dates to be recorded post-publication.*
