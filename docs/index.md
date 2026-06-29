---
title: AI Router Proxy
layout: default
---

# AI Router Proxy - Guida Operativa Completa v1.0

Benvenuto nella documentazione ufficiale del progetto.

## Apri il manuale

- 🇮🇹 **[Manuale in Italiano](manual-it.html)**
- 🇬🇧 **[English Manual](manual-en.html)**

## About

AI Router Proxy è un proxy self-hosted per Claude Code che permette di
switchare istantaneamente tra Claude (Anthropic) e MiniMax con fallback
automatico, context compression e cross-model verification. Un solo
endpoint, zero restart IDE.

- **Repository**: https://github.com/eroslifestyle/ai-router-switch
- **Licenza**: MIT
- **Versione**: v1.0
- **Data**: 2026-06-23

## Caratteristiche principali

- 4 modalità di routing (anthropic, minimax, mixed, interactive)
- 3 servizi systemd + 1 proxy = 5 porte
- Fallback automatico su errori retryable
- Circuit breaker (3 fail -> 120s cooldown)
- Tripla difesa: systemd + cron + hook
- Self-hosted, MIT, open source

## Discussioni e Wiki

Hai domande o vuoi condividere la tua esperienza?

- 💬 [GitHub Discussions](https://github.com/eroslifestyle/ai-router-switch/discussions)
- 📚 [Wiki](https://github.com/eroslifestyle/ai-router-switch/wiki)
- 🐛 [Issues](https://github.com/eroslifestyle/ai-router-switch/issues)
