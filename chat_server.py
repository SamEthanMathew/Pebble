#!/usr/bin/env python3
"""Pebble chat window — runs as a standalone subprocess using pywebview (Edge/WebView2).
Spawned by main.py when the user left-clicks the crab.
"""

from __future__ import annotations
import json
import os
import sys
import threading

# ensure V2 directory is on path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview

# ── HTML ───────────────────────────────────────────────────────────────────────

CHAT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Pebble</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  :root {
    --bg-deep:#050913; --app-bg:#0b1424; --surface:#111c30;
    --surface-2:#182640; --surface-3:#1f2f4d; --surface-hover:#1a2842;
    --border:#1e2f4d; --border-soft:rgba(40,60,95,.55); --border-strong:#2b4470;
    --text:#e6ecf5; --text-mid:#9aa9c2; --text-dim:#6b7a93;
    --accent:#3b82f6; --accent-2:#2563eb; --accent-glow:rgba(59,130,246,.18);
    --ok:#10b981; --warn:#f59e0b; --crit:#ef4444;
    --pink:#ec4899; --purple:#8b5cf6;
    --mono:'JetBrains Mono',ui-monospace,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  html,body{height:100%;font-family:'Inter',system-ui,sans-serif;color:var(--text);
    -webkit-font-smoothing:antialiased;overflow:hidden;background:#0b1424;}

  .app-window{
    width:100%;height:100vh;
    background:linear-gradient(180deg,#0c1628 0%,#0a1322 100%);
    border:1px solid rgba(80,110,160,.18);
    display:grid;grid-template-rows:56px 1fr auto;overflow:hidden;position:relative;
  }
  .drag-handle{
    position:absolute;top:4px;left:50%;transform:translateX(-50%);
    width:36px;height:4px;border-radius:2px;background:rgba(255,255,255,.1);z-index:10;
    -webkit-app-region:drag;app-region:drag;
  }

  /* HEADER */
  .header{
    display:grid;grid-template-columns:auto 1fr auto;align-items:center;
    padding:0 14px 0 18px;gap:18px;
    border-bottom:1px solid var(--border-soft);background:rgba(8,14,26,.6);
    -webkit-app-region:drag;app-region:drag;
  }
  .header *{-webkit-app-region:no-drag;app-region:no-drag;}
  .brand{display:flex;align-items:center;gap:10px;}
  .brand-avatar{
    width:34px;height:34px;border-radius:50%;
    background:radial-gradient(circle at 30% 30%,#ff6b4a,#c0392b 70%);
    display:grid;place-items:center;font-size:19px;
    box-shadow:0 4px 14px rgba(239,68,68,.35);flex-shrink:0;
  }
  .brand-text{line-height:1.2;}
  .brand-name{display:flex;align-items:center;gap:6px;font-size:15px;font-weight:700;letter-spacing:-.01em;}
  .verified{width:14px;height:14px;background:var(--accent);border-radius:50%;display:grid;place-items:center;color:white;}
  .verified svg{width:9px;height:9px;stroke-width:3.5;}
  .brand-status{font-size:11.5px;color:var(--text-mid);display:flex;align-items:center;gap:6px;margin-top:1px;}

  .agent-state{
    justify-self:center;display:inline-flex;align-items:center;gap:8px;
    padding:6px 14px;border-radius:999px;
    background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.22);
    font-size:12px;color:#c5d8f5;font-weight:500;transition:all .3s;
  }
  .agent-state.idle{background:rgba(16,185,129,.06);border-color:rgba(16,185,129,.2);color:#b8e0d2;}
  .agent-state .pulse{
    width:8px;height:8px;border-radius:50%;background:var(--accent);
    box-shadow:0 0 8px var(--accent);animation:pulse 1.6s ease-in-out infinite;
  }
  .agent-state.idle .pulse{background:var(--ok);box-shadow:0 0 6px var(--ok);animation:none;}
  @keyframes pulse{0%,100%{opacity:1;transform:scale(1);}50%{opacity:.4;transform:scale(.8);}}
  .dots::after{content:'';animation:thinkDots 1.2s steps(4,end) infinite;}
  @keyframes thinkDots{0%{content:'';}25%{content:'.';}50%{content:'..';}75%{content:'...';}100%{content:'';}}

  .hctl-row{display:flex;align-items:center;gap:8px;}
  .model-badge{
    display:inline-flex;align-items:center;gap:6px;padding:5px 10px;
    background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);
    border-radius:8px;color:var(--text-mid);font-size:11px;cursor:pointer;
    transition:all .15s;white-space:nowrap;max-width:160px;overflow:hidden;text-overflow:ellipsis;
  }
  .model-badge:hover{background:rgba(255,255,255,.08);color:var(--text);}
  .win-btn{
    width:32px;height:32px;border-radius:8px;background:transparent;
    border:1px solid rgba(255,255,255,.06);color:var(--text-mid);
    display:grid;place-items:center;cursor:pointer;transition:all .15s;
  }
  .win-btn:hover{background:rgba(255,255,255,.06);color:var(--text);}
  .win-btn.close:hover{background:var(--crit);color:white;border-color:var(--crit);}
  .win-btn svg,.win-btn i{width:15px;height:15px;pointer-events:none;}

  /* MAIN GRID */
  .main{display:grid;grid-template-columns:64px 1fr 280px;overflow:hidden;min-height:0;}

  /* SIDEBAR */
  .sidebar{
    background:rgba(7,12,22,.5);border-right:1px solid var(--border-soft);
    display:flex;flex-direction:column;padding:12px 8px;gap:4px;
    width:64px;transition:width .22s ease;overflow:hidden;position:relative;z-index:5;
  }
  .sidebar:hover{width:200px;box-shadow:6px 0 24px rgba(0,0,0,.35);}
  .nav-item{
    display:flex;align-items:center;gap:14px;padding:10px 14px;border-radius:10px;
    color:var(--text-mid);cursor:pointer;font-size:13.5px;font-weight:500;
    transition:all .15s;border:1px solid transparent;white-space:nowrap;
  }
  .nav-item:hover{background:rgba(255,255,255,.04);color:var(--text);}
  .nav-item.active{background:linear-gradient(135deg,rgba(59,130,246,.18),rgba(59,130,246,.06));border-color:rgba(59,130,246,.35);color:var(--text);}
  .nav-item svg,.nav-item i{width:18px;height:18px;flex-shrink:0;pointer-events:none;}
  .nav-label{opacity:0;transition:opacity .18s;}
  .sidebar:hover .nav-label{opacity:1;}
  .nav-badge{
    margin-left:auto;background:var(--accent);color:white;font-size:10px;font-weight:700;
    padding:1px 6px;border-radius:8px;opacity:0;transition:opacity .18s;
  }
  .sidebar:hover .nav-badge{opacity:1;}
  .sidebar-spacer{flex:1;}
  .pet-card{
    background:linear-gradient(135deg,rgba(236,72,153,.1),rgba(239,68,68,.06));
    border:1px solid rgba(236,72,153,.2);border-radius:10px;
    padding:8px;display:flex;align-items:center;gap:10px;cursor:pointer;
    transition:all .2s;overflow:hidden;
  }
  .pet-card:hover{transform:translateY(-1px);}
  .pet-emoji{font-size:22px;flex-shrink:0;}
  .pet-text{line-height:1.25;opacity:0;transition:opacity .18s;min-width:0;}
  .sidebar:hover .pet-text{opacity:1;}
  .pet-title{font-size:12.5px;font-weight:600;}
  .pet-sub{font-size:10.5px;color:var(--text-mid);}

  /* CHAT */
  .chat{display:flex;flex-direction:column;min-height:0;overflow:hidden;}
  .chat-scroll{
    flex:1;overflow-y:auto;padding:22px 28px 8px;
    display:flex;flex-direction:column;gap:18px;
  }
  .chat-scroll::-webkit-scrollbar{width:6px;}
  .chat-scroll::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px;}

  .date-divider{
    display:flex;align-items:center;gap:12px;color:var(--text-dim);
    font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;margin:4px 0;
  }
  .date-divider::before,.date-divider::after{content:'';flex:1;height:1px;background:var(--border-soft);}

  .msg-row{display:flex;gap:12px;align-items:flex-end;max-width:82%;}
  .msg-row.me{margin-left:auto;flex-direction:row-reverse;}
  .msg-avatar{
    width:32px;height:32px;border-radius:50%;
    background:radial-gradient(circle at 30% 30%,#ff6b4a,#c0392b 70%);
    display:grid;place-items:center;font-size:17px;flex-shrink:0;
    box-shadow:0 2px 8px rgba(239,68,68,.25);
  }
  .msg-stack{display:flex;flex-direction:column;gap:4px;min-width:0;}
  .me .msg-stack{align-items:flex-end;}
  .bubble{
    padding:11px 15px;border-radius:14px;font-size:14px;line-height:1.6;
    background:var(--surface);border:1px solid var(--border);color:var(--text);
    max-width:100%;word-wrap:break-word;white-space:pre-wrap;
  }
  .bubble.crab{border-top-left-radius:4px;}
  .bubble.me{background:linear-gradient(135deg,#1e3a72,#1a3367);border-color:rgba(59,130,246,.3);border-top-right-radius:4px;}
  .bubble.thinking{color:var(--text-dim);font-style:italic;}
  .bubble.error-bubble{background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.25);color:#fca5a5;}
  .timestamp{font-size:10.5px;color:var(--text-dim);padding:0 4px;display:flex;align-items:center;gap:4px;}
  .timestamp .read{color:var(--accent);}
  .timestamp .read i{width:12px;height:12px;}

  /* Tool card */
  .tool-card{
    background:rgba(8,14,26,.55);border:1px solid var(--border);border-radius:12px;
    padding:10px 12px;display:flex;flex-direction:column;gap:8px;max-width:420px;font-size:12.5px;
  }
  .tool-card-head{
    display:flex;align-items:center;gap:8px;color:var(--text-mid);font-weight:600;
    font-size:11px;letter-spacing:.06em;text-transform:uppercase;
  }
  .tool-card-head i{width:13px;height:13px;}
  .tool-card-head .tag{
    margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--ok);
    background:rgba(16,185,129,.1);padding:1px 7px;border-radius:6px;
    letter-spacing:0;text-transform:none;
  }
  .tool-row{
    display:grid;grid-template-columns:1fr auto;gap:8px;padding:4px 0;
    font-size:12.5px;border-bottom:1px dashed var(--border-soft);align-items:center;
  }
  .tool-row:last-child{border-bottom:none;}
  .tool-row .src{color:var(--text-mid);font-family:var(--mono);font-size:11px;}

  /* COMPOSER */
  .composer{padding:6px 28px 16px;position:relative;}
  .composer-inner{
    display:flex;align-items:center;background:var(--surface);
    border:1px solid var(--border);border-radius:24px;padding:4px 4px 4px 18px;
    gap:6px;transition:border-color .15s,box-shadow .15s;
  }
  .composer-inner:focus-within{border-color:rgba(59,130,246,.5);box-shadow:0 0 0 4px rgba(59,130,246,.08);}
  .composer-inner input{
    flex:1;background:transparent;border:none;outline:none;
    color:var(--text);font-family:inherit;font-size:14px;padding:9px 0;
  }
  .composer-inner input::placeholder{color:var(--text-dim);}
  .icon-btn{
    width:34px;height:34px;background:transparent;border:none;border-radius:50%;
    color:var(--text-mid);cursor:pointer;display:grid;place-items:center;transition:all .15s;
  }
  .icon-btn:hover{background:rgba(255,255,255,.05);color:var(--text);}
  .icon-btn i{width:16px;height:16px;pointer-events:none;}
  .screen-btn{
    width:34px;height:34px;background:transparent;border:none;border-radius:50%;
    color:var(--text-mid);cursor:pointer;display:grid;place-items:center;transition:all .15s;
    font-size:17px;line-height:1;
  }
  .screen-btn:hover{background:rgba(255,255,255,.05);color:var(--text);}
  .send-btn{
    width:36px;height:36px;background:linear-gradient(135deg,#2563eb,#3b82f6);
    border:none;border-radius:50%;color:white;cursor:pointer;
    display:grid;place-items:center;box-shadow:0 4px 12px rgba(59,130,246,.35);
    transition:transform .15s,opacity .15s;
  }
  .send-btn:hover{transform:translateY(-1px);}
  .send-btn i{width:16px;height:16px;pointer-events:none;}
  .composer-hint{
    display:flex;align-items:center;gap:10px;padding:4px 22px 0;
    font-size:11px;color:var(--text-dim);
  }
  .composer-hint kbd{
    background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);
    border-radius:3px;padding:1px 5px;font-family:var(--mono);font-size:10px;color:var(--text-mid);
  }

  /* RIGHT PANEL */
  .right-panel{
    background:rgba(7,12,22,.4);border-left:1px solid var(--border-soft);
    padding:14px;display:flex;flex-direction:column;gap:10px;overflow-y:auto;
  }
  .right-panel::-webkit-scrollbar{width:4px;}
  .right-panel::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
  .panel-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 12px 10px;flex-shrink:0;}
  .panel-title{
    font-size:10.5px;font-weight:700;letter-spacing:.08em;color:var(--text-mid);
    margin-bottom:10px;display:flex;align-items:center;gap:8px;
  }
  .panel-count{background:rgba(59,130,246,.18);color:#60a5fa;font-size:10px;padding:1px 7px;border-radius:10px;font-weight:700;letter-spacing:0;}
  .panel-action{
    margin-left:auto;background:transparent;border:none;color:var(--text-dim);
    cursor:pointer;width:22px;height:22px;border-radius:5px;display:grid;place-items:center;
  }
  .panel-action:hover{background:rgba(255,255,255,.06);color:var(--text);}
  .panel-action i{width:14px;height:14px;pointer-events:none;}

  .schedule-row{
    display:grid;grid-template-columns:58px 1fr auto;gap:10px;
    padding:7px 8px 7px 10px;font-size:12.5px;border-left:2px solid;
    margin-bottom:3px;border-radius:0 6px 6px 0;transition:background .12s;align-items:center;
  }
  .schedule-row:hover{background:rgba(59,130,246,.06);}
  .schedule-row .time{color:var(--text-mid);font-weight:500;font-size:11.5px;}
  .schedule-row .ev{color:var(--text);font-weight:500;}
  .row-actions{display:flex;gap:2px;opacity:0;transition:opacity .15s;}
  .schedule-row:hover .row-actions{opacity:1;}
  .row-act{
    background:transparent;border:none;color:var(--text-dim);cursor:pointer;
    width:22px;height:22px;border-radius:5px;display:grid;place-items:center;
  }
  .row-act:hover{background:rgba(255,255,255,.08);color:var(--text);}
  .row-act i{width:13px;height:13px;pointer-events:none;}

  .email-row{
    display:grid;grid-template-columns:22px 1fr auto;gap:10px;align-items:center;
    padding:7px 6px;font-size:12.5px;border-radius:7px;cursor:pointer;
    position:relative;transition:background .12s;
  }
  .email-row:hover{background:rgba(255,255,255,.03);}
  .email-icon{width:22px;height:22px;background:var(--surface-2);border-radius:6px;display:grid;place-items:center;flex-shrink:0;}
  .email-icon i{width:13px;height:13px;pointer-events:none;}
  .email-meta{min-width:0;line-height:1.3;}
  .email-from{font-weight:600;font-size:12.5px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .email-subj{color:var(--text-mid);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .email-time{color:var(--text-dim);font-size:10.5px;white-space:nowrap;}

  .task-row{
    display:grid;grid-template-columns:18px 1fr auto;gap:10px;align-items:center;
    padding:7px 6px;font-size:13px;border-radius:7px;transition:background .12s;cursor:pointer;
  }
  .task-row:hover{background:rgba(255,255,255,.03);}
  .checkbox{width:16px;height:16px;border:1.5px solid var(--text-dim);border-radius:4px;cursor:pointer;transition:all .12s;}
  .checkbox:hover{border-color:var(--accent);background:rgba(59,130,246,.1);}
  .task-due{font-size:11px;color:var(--warn);font-weight:500;}
  .task-due.soon{color:var(--text-mid);}

  .connections{display:flex;gap:8px;flex-wrap:wrap;}
  .conn-chip{
    display:flex;align-items:center;gap:5px;padding:4px 8px;
    background:var(--surface-2);border:1px solid var(--border);border-radius:999px;
    font-size:11px;color:var(--text-mid);
  }
  .conn-chip .dot{width:6px;height:6px;border-radius:50%;background:var(--ok);}
  .conn-chip.off .dot{background:var(--text-dim);}

  .panel-link{
    display:inline-flex;align-items:center;gap:4px;color:var(--accent);
    font-size:12px;font-weight:500;margin-top:8px;cursor:pointer;
    background:transparent;border:none;padding:0;font-family:inherit;
  }
  .panel-link:hover{color:#60a5fa;}
  .panel-link i{width:12px;height:12px;pointer-events:none;}

  /* Model picker popup */
  .model-menu{
    position:absolute;top:56px;right:120px;
    background:var(--surface-2);border:1px solid var(--border-strong);
    border-radius:10px;box-shadow:0 16px 40px rgba(0,0,0,.5);
    padding:6px;z-index:200;min-width:220px;display:none;
  }
  .model-menu.open{display:block;}
  .model-item{
    display:flex;align-items:center;gap:10px;padding:8px 10px;
    border-radius:7px;cursor:pointer;font-size:13px;color:var(--text);
    transition:background .1s;
  }
  .model-item:hover{background:rgba(59,130,246,.12);}
  .model-item.active-model{color:var(--accent);}
  .model-item .check{width:14px;height:14px;flex-shrink:0;}

  /* Ctx chips */
  .ctx-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;max-width:480px;}
  .ctx-chip{
    background:rgba(59,130,246,.07);border:1px solid rgba(59,130,246,.25);color:#c5d8f5;
    border-radius:999px;padding:6px 12px;font-size:12.5px;cursor:pointer;
    font-family:inherit;transition:all .15s;
  }
  .ctx-chip:hover{background:rgba(59,130,246,.18);border-color:rgba(59,130,246,.5);color:white;transform:translateY(-1px);}

  /* Mic / voice input */
  @keyframes recording-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
  .recording-pulse {
    animation: recording-pulse 0.8s ease-in-out infinite;
  }

  /* Markdown rendered content */
  .bubble.crab .md-body { white-space: normal; }
  .bubble.crab .md-body p { margin: 0 0 8px; }
  .bubble.crab .md-body p:last-child { margin-bottom: 0; }
  .bubble.crab .md-body h1,.bubble.crab .md-body h2,.bubble.crab .md-body h3 {
    font-weight: 700; margin: 10px 0 4px; line-height: 1.3;
  }
  .bubble.crab .md-body h1 { font-size: 16px; }
  .bubble.crab .md-body h2 { font-size: 14.5px; }
  .bubble.crab .md-body h3 { font-size: 13.5px; color: var(--text-mid); }
  .bubble.crab .md-body ul,.bubble.crab .md-body ol {
    margin: 4px 0 8px; padding-left: 20px;
  }
  .bubble.crab .md-body li { margin-bottom: 3px; }
  .bubble.crab .md-body code {
    background: rgba(59,130,246,.12); border: 1px solid rgba(59,130,246,.25);
    border-radius: 4px; padding: 1px 5px;
    font-family: var(--mono); font-size: 12.5px; color: #93c5fd;
  }
  .bubble.crab .md-body pre {
    background: rgba(0,0,0,.35); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px; margin: 8px 0; overflow-x: auto;
  }
  .bubble.crab .md-body pre code {
    background: none; border: none; padding: 0; color: #e2e8f0;
    font-size: 12px; line-height: 1.6;
  }
  .bubble.crab .md-body strong { font-weight: 700; }
  .bubble.crab .md-body em { font-style: italic; color: var(--text-mid); }
  .bubble.crab .md-body a { color: var(--accent); text-decoration: none; }
  .bubble.crab .md-body a:hover { text-decoration: underline; }
  .bubble.crab .md-body blockquote {
    border-left: 3px solid var(--accent); padding-left: 10px;
    margin: 6px 0; color: var(--text-mid);
  }
  .bubble.crab .md-body hr {
    border: none; border-top: 1px solid var(--border); margin: 10px 0;
  }
  .bubble.crab .md-body table {
    width: 100%; border-collapse: collapse; font-size: 12.5px; margin: 8px 0;
  }
  .bubble.crab .md-body th {
    background: var(--surface-2); padding: 6px 10px; text-align: left; font-weight: 600;
    border: 1px solid var(--border);
  }
  .bubble.crab .md-body td {
    padding: 5px 10px; border: 1px solid var(--border);
  }
  .bubble.crab .md-body tr:nth-child(even) td { background: rgba(255,255,255,.02); }
</style>
</head>
<body>
<div class="app-window">
  <div class="drag-handle"></div>

  <!-- HEADER -->
  <div class="header">
    <div class="brand">
      <div class="brand-avatar">🦀</div>
      <div class="brand-text">
        <div class="brand-name">
          Pebble
          <span class="verified"><i data-lucide="check"></i></span>
        </div>
        <div class="brand-status">v2.0 &middot; local-first</div>
      </div>
    </div>

    <div class="agent-state idle" id="agentState">
      <span class="pulse"></span>
      <span id="agentStateText">Ready</span>
    </div>

    <div class="hctl-row">
      <button class="model-badge" id="modelBadge" title="Switch model">
        <i data-lucide="cpu"></i>
        <span id="modelName">Loading…</span>
        <i data-lucide="chevron-down"></i>
      </button>
      <button class="win-btn close" id="closeBtn" title="Close"><i data-lucide="x"></i></button>
    </div>
  </div>

  <!-- model picker dropdown -->
  <div class="model-menu" id="modelMenu"></div>

  <!-- MAIN -->
  <div class="main">
    <!-- SIDEBAR -->
    <nav class="sidebar">
      <div class="nav-item active">
        <i data-lucide="message-circle"></i>
        <span class="nav-label">Chat</span>
      </div>
      <div class="nav-item" title="Coming soon">
        <i data-lucide="check-square"></i>
        <span class="nav-label">Tasks</span>
      </div>
      <div class="nav-item" title="Obsidian notes">
        <i data-lucide="file-text"></i>
        <span class="nav-label">Notes</span>
      </div>
      <div class="nav-item" title="Coming soon">
        <i data-lucide="mail"></i>
        <span class="nav-label">Gmail</span>
      </div>
      <div class="nav-item" title="Coming soon">
        <i data-lucide="calendar"></i>
        <span class="nav-label">Calendar</span>
      </div>
      <div class="nav-item" title="Coming soon">
        <i data-lucide="bell"></i>
        <span class="nav-label">Reminders</span>
      </div>
      <div class="sidebar-spacer"></div>
      <div class="pet-card">
        <span class="pet-emoji">🦀</span>
        <div class="pet-text">
          <div class="pet-title">Pebble</div>
          <div class="pet-sub">Always here</div>
        </div>
      </div>
    </nav>

    <!-- CHAT -->
    <section class="chat">
      <div class="chat-scroll" id="chatScroll">
        <div class="date-divider" id="dateDivider">Today</div>
      </div>

      <div class="composer">
        <div class="composer-inner">
          <input type="text" id="composerInput" placeholder="Ask Pebble anything…" autocomplete="off"/>
          <button id="mic-btn"
                  onclick="window.pywebview.api.start_voice_input()"
                  title="Voice input"
                  style="background:#2e2e48; border:none; color:#cdd6f4; font-size:16px;
                         width:38px; height:38px; border-radius:8px; cursor:pointer;
                         display:flex; align-items:center; justify-content:center;
                         flex-shrink:0; transition:background 0.2s;">
            🎤
          </button>
          <button class="screen-btn" id="screenBtn"
                  onclick="window.pywebview.api.show_screen()"
                  title="Share screen">
            📸
          </button>
          <button class="send-btn" id="sendBtn" title="Send"><i data-lucide="arrow-up"></i></button>
        </div>
        <div class="composer-hint">
          <span><kbd>Enter</kbd> send &nbsp;&middot;&nbsp; <kbd>Shift Enter</kbd> new line</span>
        </div>
      </div>
    </section>

    <!-- RIGHT PANEL -->
    <aside class="right-panel">
      <!-- Today -->
      <div class="panel-card">
        <div class="panel-title">
          TODAY
          <button class="panel-action" title="Refresh"><i data-lucide="refresh-cw"></i></button>
        </div>
        <div id="calendarRows" style="color:var(--text-dim);font-size:12px;padding:4px 0 6px;">
          Connect Google Calendar to see events
        </div>
        <button class="panel-link">Set up Calendar <i data-lucide="arrow-right"></i></button>
      </div>

      <!-- Active modules -->
      <div class="panel-card" id="modulesCard">
        <div class="panel-title">ACTIVE MODULES</div>
        <div class="connections" id="moduleChips"></div>
      </div>

      <!-- Quick tasks -->
      <div class="panel-card">
        <div class="panel-title">QUICK ACTIONS</div>
        <div class="ctx-chips" style="margin-top:0;">
          <button class="ctx-chip" data-msg="Give me a full morning briefing: check my calendar for today, any pending tasks, and what I should focus on first.">🌅 Morning briefing</button>
          <button class="ctx-chip" data-msg="What assignments or deadlines do I have coming up on Canvas?">🎓 Canvas</button>
          <button class="ctx-chip" data-msg="What are my current Kalshi positions and portfolio balance?">📊 Kalshi</button>
          <button class="ctx-chip" data-msg="What are the latest prices for Bitcoin, Ethereum, and Solana?">₿ Crypto</button>
          <button class="ctx-chip" data-msg="What tasks do I have pending today?">✅ Tasks</button>
          <button class="ctx-chip" data-msg="Search my recent emails for anything important I may have missed.">📧 Emails</button>
          <button class="ctx-chip" data-msg="What's the weather like today?">🌤 Weather</button>
          <button class="ctx-chip" data-msg="What's in my clipboard?">📋 Clipboard</button>
          <button class="ctx-chip" data-msg="Give me a productivity tip for today.">💡 Tip</button>
        </div>
      </div>

      <!-- Sources -->
      <div class="panel-card">
        <div class="panel-title">SOURCES</div>
        <div class="connections" id="sourcesChips">
          <div class="conn-chip off"><span class="dot"></span> Gmail</div>
          <div class="conn-chip off"><span class="dot"></span> Calendar</div>
          <div class="conn-chip off"><span class="dot"></span> Notion</div>
          <div class="conn-chip off"><span class="dot"></span> Linear</div>
        </div>
      </div>
    </aside>
  </div>
</div>

<script>
// ── Lucide icons ───────────────────────────────────────────────────────────────
if (window.lucide) lucide.createIcons();
function reicons() { if (window.lucide) lucide.createIcons(); }

// ── Marked.js config ──────────────────────────────────────────────────────────
if (window.marked) {
  marked.setOptions({ breaks: true, gfm: true });
}
function renderMd(text) {
  if (window.marked) {
    try { return '<div class="md-body">' + marked.parse(text) + '</div>'; } catch(e) {}
  }
  return '<span>' + escapeHtml(text) + '</span>';
}

// ── State ──────────────────────────────────────────────────────────────────────
let isBusy = false;
let activeModelId = '';

// ── Command history ────────────────────────────────────────────────────────────
const cmdHistory = [];
let histIdx = -1;
let histDraft = '';

const chatScroll    = document.getElementById('chatScroll');
const composerInput = document.getElementById('composerInput');
const sendBtn       = document.getElementById('sendBtn');
const agentState    = document.getElementById('agentState');
const agentText     = document.getElementById('agentStateText');
const modelBadge    = document.getElementById('modelBadge');
const modelNameEl   = document.getElementById('modelName');
const modelMenu     = document.getElementById('modelMenu');
const dateDivider   = document.getElementById('dateDivider');

// ── Date divider ───────────────────────────────────────────────────────────────
{
  const d = new Date();
  const days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  dateDivider.textContent = `Today · ${days[d.getDay()]}, ${months[d.getMonth()]} ${d.getDate()}`;
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function now12() {
  return new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function scrollBottom() {
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

// ── Message rendering ──────────────────────────────────────────────────────────
function appendBot(text) {
  const row = document.createElement('div');
  row.className = 'msg-row';
  row.innerHTML = `
    <div class="msg-avatar">🦀</div>
    <div class="msg-stack">
      <div class="bubble crab">${renderMd(text)}</div>
      <div class="timestamp">${now12()}</div>
    </div>`;
  chatScroll.appendChild(row);
  scrollBottom();
  reicons();
}

function appendUser(text) {
  const row = document.createElement('div');
  row.className = 'msg-row me';
  row.innerHTML = `
    <div class="msg-stack">
      <div class="bubble me">${escapeHtml(text)}</div>
      <div class="timestamp">${now12()} <span class="read"><i data-lucide="check-check"></i></span></div>
    </div>`;
  chatScroll.appendChild(row);
  scrollBottom();
  reicons();
}

function appendError(msg) {
  const row = document.createElement('div');
  row.className = 'msg-row';
  row.innerHTML = `
    <div class="msg-avatar">🦀</div>
    <div class="msg-stack">
      <div class="bubble error-bubble">${escapeHtml(msg)}</div>
      <div class="timestamp">${now12()}</div>
    </div>`;
  chatScroll.appendChild(row);
  scrollBottom();
}

let thinkingEl = null;
function _showThinkingBubble() {
  thinkingEl = document.createElement('div');
  thinkingEl.className = 'msg-row';
  thinkingEl.id = 'thinkingRow';
  thinkingEl.innerHTML = `
    <div class="msg-avatar">🦀</div>
    <div class="msg-stack">
      <div class="bubble crab thinking">Thinking<span class="dots"></span></div>
    </div>`;
  chatScroll.appendChild(thinkingEl);
  scrollBottom();
}
function _hideThinkingBubble() {
  const el = document.getElementById('thinkingRow');
  if (el) el.remove();
  thinkingEl = null;
}

// ── Busy state ─────────────────────────────────────────────────────────────────
function setBusy(busy) {
  isBusy = busy;
  sendBtn.style.opacity = busy ? '0.45' : '1';
  sendBtn.style.pointerEvents = busy ? 'none' : 'auto';
  composerInput.disabled = busy;
  if (busy) {
    agentState.classList.remove('idle');
    agentText.innerHTML = 'Thinking<span class="dots"></span>';
    _showThinkingBubble();
  } else {
    agentState.classList.add('idle');
    agentText.textContent = 'Ready · ' + (modelNameEl.textContent || '');
    _hideThinkingBubble();
    composerInput.focus();
  }
}

// ── Python API callbacks (called from Python via evaluate_js) ──────────────────
function receiveMessage(text) {
  setBusy(false);
  appendBot(text);
}

function receiveError(msg) {
  setBusy(false);
  appendError('Error: ' + msg);
}

function setModelInfo(info) {
  activeModelId = info.active;
  modelNameEl.textContent = info.name;
  agentText.textContent = 'Ready · ' + info.name;
  agentState.classList.add('idle');

  // Update module chips
  const chips = document.getElementById('moduleChips');
  chips.innerHTML = '';
  (info.modules || []).forEach(m => {
    const c = document.createElement('div');
    c.className = 'conn-chip';
    c.innerHTML = `<span class="dot"></span> ${m}`;
    chips.appendChild(c);
  });

  // Populate model menu
  buildModelMenu(info.all_models || [], info.active);
}

function buildModelMenu(models, activeMid) {
  modelMenu.innerHTML = '';
  models.forEach(m => {
    const item = document.createElement('div');
    item.className = 'model-item' + (m.id === activeMid ? ' active-model' : '');
    item.innerHTML = `<i data-lucide="${m.id === activeMid ? 'check' : 'cpu'}" class="check"></i>${escapeHtml(m.display_name)}`;
    item.addEventListener('click', () => {
      modelMenu.classList.remove('open');
      if (m.id !== activeModelId) {
        window.pywebview.api.set_model(m.id).then(() => {
          activeModelId = m.id;
          modelNameEl.textContent = m.display_name;
          agentText.textContent = 'Ready · ' + m.display_name;
          buildModelMenu(models, m.id);
        });
      }
    });
    modelMenu.appendChild(item);
  });
  reicons();
}

// ── Voice input ────────────────────────────────────────────────────────────────
function setMicState(state) {
  const btn = document.getElementById('mic-btn');
  if (!btn) return;
  if (state === 'recording') {
    btn.textContent = '⏹';
    btn.style.background = '#e05c7a';
    btn.title = 'Recording... click to cancel';
    btn.classList.add('recording-pulse');
  } else {
    btn.textContent = '🎤';
    btn.style.background = '#2e2e48';
    btn.title = 'Voice input';
    btn.classList.remove('recording-pulse');
  }
}

function setVoiceTranscript(text) {
  const input = document.getElementById('composerInput');
  if (input) {
    input.value = text;
    input.focus();
    // Auto-submit after 1.5 seconds if unchanged
    const snapshot = text;
    setTimeout(() => {
      if (input.value === snapshot && input.value.trim()) {
        doSend();
      }
    }, 1500);
  }
}

function setMicError(msg) {
  // Show a brief error in the thinking indicator area
  const el = document.getElementById('thinkingRow');
  if (el) {
    el.querySelector('.bubble').textContent = '🎤 ' + msg;
    el.style.display = '';
    setTimeout(() => { el.style.display = 'none'; }, 3000);
  } else {
    // Fallback: append as an error message if no thinking row
    appendError('🎤 ' + msg);
  }
}

// ── Send ───────────────────────────────────────────────────────────────────────
function doSend() {
  const text = composerInput.value.trim();
  if (!text || isBusy) return;
  // Save to history
  if (cmdHistory[cmdHistory.length - 1] !== text) cmdHistory.push(text);
  if (cmdHistory.length > 100) cmdHistory.shift();
  histIdx = -1;
  histDraft = '';
  composerInput.value = '';
  appendUser(text);
  setBusy(true);
  window.pywebview.api.send_message(text);
}

sendBtn.addEventListener('click', doSend);
composerInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(); return; }
  // Command history navigation
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (cmdHistory.length === 0) return;
    if (histIdx === -1) { histDraft = composerInput.value; histIdx = cmdHistory.length - 1; }
    else if (histIdx > 0) histIdx--;
    composerInput.value = cmdHistory[histIdx];
    setTimeout(() => composerInput.setSelectionRange(composerInput.value.length, composerInput.value.length), 0);
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (histIdx === -1) return;
    histIdx++;
    if (histIdx >= cmdHistory.length) { histIdx = -1; composerInput.value = histDraft; }
    else composerInput.value = cmdHistory[histIdx];
    setTimeout(() => composerInput.setSelectionRange(composerInput.value.length, composerInput.value.length), 0);
  }
});

// Quick action chips
document.querySelectorAll('.ctx-chip[data-msg]').forEach(btn => {
  btn.addEventListener('click', () => {
    if (isBusy) return;
    composerInput.value = btn.dataset.msg;
    doSend();
  });
});

// ── Model badge toggle ─────────────────────────────────────────────────────────
modelBadge.addEventListener('click', (e) => {
  e.stopPropagation();
  modelMenu.classList.toggle('open');
});
document.addEventListener('click', () => modelMenu.classList.remove('open'));

// ── Screenshot helpers (called from Python) ────────────────────────────────────
function appendUserMessage(text) { appendUser(text); }
function appendBotMessage(text)  { appendBot(text);  }
function showThinking(show) {
  if (show) { setBusy(true); } else { setBusy(false); }
}

// ── Close ──────────────────────────────────────────────────────────────────────
document.getElementById('closeBtn').addEventListener('click', () => {
  window.pywebview.api.close();
});

// ── Init ───────────────────────────────────────────────────────────────────────
window.addEventListener('pywebviewready', () => {
  window.pywebview.api.on_ready();
});
</script>
</body>
</html>"""


# ── Python API ─────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    base = (
        'You are Pebble, a friendly and highly capable AI desktop companion. '
        'Keep replies concise and direct unless asked to elaborate. '
        'Use markdown formatting (bold, lists, code blocks) when it improves clarity. '
        'Use tools proactively when they would genuinely help answer the question. '
        'When the user asks you to remember something, use the memory tool to save it. '
        'When context seems relevant, check memory first.'
    )
    # Inject any saved memories into the system prompt
    try:
        from modules.memory import _load
        items = _load()
        if items:
            mem_lines = ['', 'Relevant things I remember about the user:']
            for m in items[-15:]:
                mem_lines.append(f'  - [{m.get("category","fact")}] {m.get("text","")}')
            base += '\n' + '\n'.join(mem_lines)
    except Exception:
        pass
    return base


class ChatAPI:
    @property
    def _system_prompt(self) -> str:
        return _build_system_prompt()

    def __init__(self):
        self._msgs:      list[dict] = []
        self._window:    webview.Window | None = None
        self._recording: bool = False

    def on_ready(self):
        """Called by JS when the webview is initialized."""
        import crab_config
        from modules import get_active_modules, ALL_MODULES

        mid    = crab_config.get_active_model_id()
        models = crab_config.get_enabled_models()
        active = next((m for m in models if m['id'] == mid), None)
        name   = active['display_name'] if active else 'No model'

        active_mods = [m.display_name for m in get_active_modules()]

        info = json.dumps({
            'name':       name,
            'active':     mid,
            'all_models': models,
            'modules':    active_mods,
        })
        self._window.evaluate_js(f'setModelInfo({info})')

        greeting = 'Hey! What can I help you with?'
        self._msgs = []
        self._window.evaluate_js(f'receiveMessage({json.dumps(greeting)})')

    def send_message(self, text: str):
        """Called by JS when user sends a message."""
        self._msgs.append({'role': 'user', 'content': text})
        threading.Thread(target=self._process, daemon=True).start()

    def _process(self):
        try:
            from model_backend import ModelBackend
            from modules import get_active_modules
            from tool_orchestrator import ToolOrchestrator

            backend      = ModelBackend.active()
            modules      = get_active_modules()
            orchestrator = ToolOrchestrator(backend, modules)

            response = orchestrator.chat(
                self._msgs,
                system=self._system_prompt,
            )
            self._msgs.append({'role': 'assistant', 'content': response})
            self._window.evaluate_js(f'receiveMessage({json.dumps(response)})')
        except Exception as exc:
            self._window.evaluate_js(f'receiveError({json.dumps(str(exc))})')

    def start_voice_input(self):
        """Called from JS mic button. Starts recording in background thread."""
        if self._recording:
            return
        self._recording = True
        self._window.evaluate_js("setMicState('recording')")
        threading.Thread(target=self._do_record, daemon=True).start()

    def _do_record(self):
        try:
            from modules.stt import record_once
            import crab_config
            lang = crab_config.get_module_config('stt').get('language', 'en-US')
            ok, text = record_once(language=lang)
            if ok:
                # Escape text for JS string
                safe = text.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
                self._window.evaluate_js(f"setVoiceTranscript('{safe}')")
            else:
                safe_err = text.replace("'", "\\'")
                self._window.evaluate_js(f"setMicError('{safe_err}')")
        except Exception:
            self._window.evaluate_js("setMicError('Voice error: check mic settings')")
        finally:
            self._recording = False
            self._window.evaluate_js("setMicState('idle')")

    def show_screen(self):
        """Called from JS 'Show screen' button. Captures screen and sends to AI."""
        threading.Thread(target=self._process_screenshot, daemon=True).start()

    def _process_screenshot(self):
        try:
            from modules.screenshot import capture_screen
            _b64, path = capture_screen()

            # Add a user message asking about the screen
            screen_msg = "I've shared my screen with you. What do you see? Help me with what's on screen."
            self._msgs.append({'role': 'user', 'content': screen_msg})

            self._window.evaluate_js("appendUserMessage('📸 [Sharing screen...]')")
            self._window.evaluate_js("showThinking(true)")

            # Use vision-capable chat
            try:
                from model_backend import ModelBackend
                backend = ModelBackend.active()
                response = backend.chat_with_image(self._msgs, self._system_prompt, path)
            except Exception:
                try:
                    from model_backend import ModelBackend
                    backend = ModelBackend.active()
                    response = backend.chat(self._msgs, self._system_prompt)
                except Exception as inner:
                    response = f'Could not get a response: {inner}'

            self._msgs.append({'role': 'assistant', 'content': response})
            safe = response.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
            self._window.evaluate_js(f"appendBotMessage(`{safe}`)")
            self._window.evaluate_js("showThinking(false)")
        except Exception:
            self._window.evaluate_js("appendError('Screenshot failed: check PIL is installed')")
            self._window.evaluate_js("showThinking(false)")

    def set_model(self, model_id: str):
        """Called by JS when user picks a different model."""
        import crab_config
        crab_config.set_active_model(model_id)

    def close(self):
        self._window.destroy()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--x', type=int, default=None)
    parser.add_argument('--y', type=int, default=None)
    args = parser.parse_args()

    api    = ChatAPI()
    window = webview.create_window(
        'Pebble',
        html=CHAT_HTML,
        js_api=api,
        width=1100,
        height=740,
        x=args.x,
        y=args.y,
        frameless=True,
        easy_drag=True,
        background_color='#0b1424',
    )
    api._window = window
    webview.start(gui='edgechromium', private_mode=False)
