## Titolo candidato

1. **Show HN: Claude Code Router – Switch between Claude and MiniMax with one endpoint (MIT)**
2. **Show HN: I built a proxy that routes Claude Code between multiple AI backends**
3. **Show HN: Self-hosted multi-backend router for Claude Code, MIT licensed**

## Body

Hey HN,

I built Claude Code Router because I was tired of managing separate configurations when switching between Claude and MiniMax for different tasks.

It's a self-hosted proxy that lets you route Claude Code requests through a single endpoint while seamlessly switching backends. Just point `ANTHROPIC_BASE_URL` to the proxy, set your backend preference via config or headers, and the router handles the rest. Supports both streaming and non-streaming responses, with automatic model mapping between providers.

Stack: Python 3.11 + aiohttp + systemd. MIT licensed, ~700 LOC.

What's different from a simple reverse proxy:
- Backend-agnostic routing with header-based switching
- Automatic model name translation between providers
- Minimal configuration, no external dependencies beyond aiohttp
- Designed specifically for Claude Code's request patterns

This is my first time building a proxy layer, so the approach might be naive. Happy to hear if there's a cleaner way to handle the translation between API formats.

Happy to answer questions. Constructive feedback especially welcome on the routing design and error handling approach.

## Link da includere
- Repository: https://github.com/eroslifestyle/ai-router-switch
- Discussion: (HN submission URL after posting)

## Posting time suggestions

- **Best**: Tuesday-Thursday, EST 8-10am (11am-1pm UTC) or PST 8-10am
- **Alternative**: Tuesday-Thursday evening EST 6-8pm can work but lower visibility
- **Avoid**: Mondays, Fridays, weekends, major holidays

## Se floppa

1. **Wait 2+ weeks** before reposting with adjusted title
2. **Try lobste.rs** (lobste.rs/t/ai first, then programming)
3. **Post on r/ClaudeAI** or relevant subreddits
4. **Gated**: Post on AI/tech Discord servers with Show channel
5. **Twitter/X**: Share without the "Show HN" prefix, link to HN post for discussion
6. **Iterate title**: Focus on the specific problem solved, avoid generic "AI router" phrasing
