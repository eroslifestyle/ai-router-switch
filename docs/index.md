---
title: AI Router Proxy
layout: default
---

# AI Router Switch — Documentation

Self-hosted routing proxy for Claude Code. Switch between **Claude**, **MiniMax**, and **GLM/z.ai**
with automatic failover, per-chat isolation, and cross-model verification.

---

## Manuals

- 🇮🇹 **[Manuale in Italiano](MANUAL.md)** · [HTML](manual-it.html)
- 🇬🇧 **[English Manual](MANUAL.en.md)** · [HTML](manual-en.html)

---

## Quick Reference

**7 modes:** `anthropic` · `minimax` · `mixed` · `inverse` · `glm` · `glm-minimax` · `anthropic-glm`

**8 ports:** `8787` (dynamic) + `8771–8777` (forced per mode)

**Per-chat switch:** `!router minimax` · `!router status` · `!router reset`

**Global switch:** `ai-mode mixed`

---

## About

| | |
|---|---|
| **Repository** | https://github.com/eroslifestyle/ai-router-switch |
| **License** | MIT |
| **Version** | 2026-07-14 |

### Key features

- 7 routing modes across 3 backends (Claude, MiniMax, GLM/z.ai)
- 8 ports (1 dynamic + 7 forced)
- Automatic bidirectional failover on retryable errors
- Per-chat isolation via conversation fingerprint
- GLM tiering with peak-cost awareness (14:00–18:00 Shanghai)
- Triple defense: systemd + cron watchdog + SessionStart hook

---

## Community

- 💬 [GitHub Discussions](https://github.com/eroslifestyle/ai-router-switch/discussions)
- 📚 [Wiki](https://github.com/eroslifestyle/ai-router-switch/wiki)
- 🐛 [Issues](https://github.com/eroslifestyle/ai-router-switch/issues)
