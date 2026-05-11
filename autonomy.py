"""Autonomy layer — routes Proposals through tiers, manages first-time gating.

Per docs/contracts.md §1, §6, §9. The ONLY component allowed to fire outbound
write-side `module.execute()` calls. Planners propose; autonomy decides.

Routing logic:
1. Resolve effective tier (config override → module._default_tiers → ASK).
2. If first-time-key has never been seen → force ASK (even for AUTO/NOTIFY).
3. Outbound sends (target_id present) also gate per-recipient.
4. AUTO    → execute now, audit.
5. NOTIFY  → execute now, audit, set notify=true so caller can show toast.
6. ASK     → queue for approval; do not execute. Caller drives approval UI.
7. Dry-run → NEVER call module.execute(); always write_preview + audit was_dry_run=true.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import audit
import dry_run
import metrics
import first_time_ledger as ledger
from modules.base import ActionTier, PebbleModule
from planners.base import Proposal


# ── Result shapes ─────────────────────────────────────────────────────────────

@dataclass
class RouteResult:
    status:        str        # 'executed_auto' | 'executed_notify' | 'awaits_approval' | 'denied' | 'dry_run' | 'error'
    proposal_id:   str
    module:        str
    action:        str
    tier:          str        # effective tier after first-time logic
    was_first_time: bool
    user_approved: bool | None = None
    result:        Any = None
    error:         str | None = None
    preview_path:  str | None = None


# ── Autonomy class ────────────────────────────────────────────────────────────

class Autonomy:
    """Routes Proposals from planners.

    `approval_handler(proposal) -> bool`:
        Called when the autonomy layer needs user approval (ASK tier or first-time).
        Return True to approve and execute; False to deny.
        If None, ASK-tier proposals are queued and the caller is expected to drive
        approvals via the pending queue + approve_pending() / deny_pending() methods.
    """

    def __init__(
        self, *,
        approval_handler: Callable[[Proposal], bool] | None = None,
        modules: list[PebbleModule] | None = None,
    ):
        self._approval_handler = approval_handler
        self._modules_override = modules
        self._pending: dict[str, Proposal] = {}
        self._lock = threading.Lock()

    # ── public API ────────────────────────────────────────────────────────────

    def route(self, proposal: Proposal) -> RouteResult:
        proposal_id = uuid.uuid4().hex[:12]
        first_key   = ledger.make_key(proposal.module, proposal.action, proposal.target_id)
        was_first   = ledger.is_first_time(first_key)

        # Resolve declared tier from the module
        mod = self._lookup_module(proposal.module)
        if mod is None:
            err = f'Unknown module: {proposal.module!r}'
            audit.append({
                'module':  proposal.module, 'action': proposal.action,
                'args':    proposal.args, 'result': {'error': err},
                'tier':    'ask', 'source': proposal.source,
            })
            return RouteResult(status='error', proposal_id=proposal_id,
                               module=proposal.module, action=proposal.action,
                               tier='ask', was_first_time=was_first, error=err)

        declared_tier = mod.action_tier(proposal.action)

        # First-time gate forces ASK
        effective_tier = ActionTier.ASK if was_first else declared_tier

        metrics.emit('proposal.received', {
            'module':         proposal.module, 'action': proposal.action,
            'declared_tier':  declared_tier.value,
            'effective_tier': effective_tier.value,
            'was_first_time': was_first,
            'source':         proposal.source,
        })

        # ── ASK path ──────────────────────────────────────────────────────────
        if effective_tier == ActionTier.ASK:
            if self._approval_handler is not None:
                metrics.emit('firsttime.asked' if was_first else 'proposal.asked',
                             {'module': proposal.module, 'action': proposal.action})
                try:
                    approved = bool(self._approval_handler(proposal))
                except Exception as exc:
                    audit.append({
                        'module':  proposal.module, 'action': proposal.action,
                        'args':    proposal.args,
                        'result':  {'error': f'approval_handler raised: {exc}'},
                        'tier':    effective_tier.value, 'source': proposal.source,
                        'was_first_time': was_first, 'first_time_key': first_key,
                    })
                    return RouteResult(status='error', proposal_id=proposal_id,
                                       module=proposal.module, action=proposal.action,
                                       tier=effective_tier.value,
                                       was_first_time=was_first,
                                       error=f'approval_handler raised: {exc}')
                if approved:
                    ledger.record(first_key)
                    return self._execute(proposal, proposal_id,
                                         tier=effective_tier, was_first=was_first,
                                         user_approved=True, mod=mod)
                metrics.emit('proposal.canceled', {'module': proposal.module, 'action': proposal.action})
                audit.append({
                    'module':  proposal.module, 'action': proposal.action,
                    'args':    proposal.args, 'result': {'denied': True},
                    'tier':    effective_tier.value, 'source': proposal.source,
                    'user_approved': False,
                    'was_first_time': was_first, 'first_time_key': first_key,
                })
                return RouteResult(status='denied', proposal_id=proposal_id,
                                   module=proposal.module, action=proposal.action,
                                   tier=effective_tier.value,
                                   was_first_time=was_first, user_approved=False)

            # No handler → enqueue for later approval
            with self._lock:
                self._pending[proposal_id] = proposal
            return RouteResult(status='awaits_approval', proposal_id=proposal_id,
                               module=proposal.module, action=proposal.action,
                               tier=effective_tier.value, was_first_time=was_first)

        # ── AUTO / NOTIFY path ────────────────────────────────────────────────
        ledger.record(first_key)
        return self._execute(proposal, proposal_id,
                             tier=effective_tier, was_first=was_first,
                             user_approved=None, mod=mod)

    def approve_pending(self, proposal_id: str) -> RouteResult:
        with self._lock:
            proposal = self._pending.pop(proposal_id, None)
        if proposal is None:
            return RouteResult(status='error', proposal_id=proposal_id,
                               module='', action='', tier='ask',
                               was_first_time=False, error='unknown proposal_id')
        first_key = ledger.make_key(proposal.module, proposal.action, proposal.target_id)
        was_first = ledger.is_first_time(first_key)
        ledger.record(first_key)
        mod = self._lookup_module(proposal.module)
        return self._execute(proposal, proposal_id, tier=ActionTier.ASK,
                             was_first=was_first, user_approved=True, mod=mod)

    def deny_pending(self, proposal_id: str) -> RouteResult:
        with self._lock:
            proposal = self._pending.pop(proposal_id, None)
        if proposal is None:
            return RouteResult(status='error', proposal_id=proposal_id,
                               module='', action='', tier='ask',
                               was_first_time=False, error='unknown proposal_id')
        metrics.emit('proposal.canceled', {'module': proposal.module, 'action': proposal.action})
        audit.append({
            'module':  proposal.module, 'action': proposal.action,
            'args':    proposal.args, 'result': {'denied': True},
            'tier':    'ask', 'source': proposal.source, 'user_approved': False,
        })
        return RouteResult(status='denied', proposal_id=proposal_id,
                           module=proposal.module, action=proposal.action,
                           tier='ask', was_first_time=False, user_approved=False)

    def pending_proposals(self) -> dict[str, Proposal]:
        with self._lock:
            return dict(self._pending)

    # ── internals ─────────────────────────────────────────────────────────────

    def _lookup_module(self, name: str) -> PebbleModule | None:
        if self._modules_override is not None:
            mods = self._modules_override
        else:
            try:
                from modules import get_active_modules
                mods = get_active_modules()
            except Exception:
                return None
        for m in mods:
            if m.tool_name() == name:
                return m
        return None

    def _execute(self, proposal: Proposal, proposal_id: str, *,
                 tier: ActionTier, was_first: bool,
                 user_approved: bool | None,
                 mod: PebbleModule | None) -> RouteResult:
        first_key = ledger.make_key(proposal.module, proposal.action, proposal.target_id)

        # Dry-run: write preview, do not actually execute
        if dry_run.is_enabled():
            preview = dry_run.write_preview({
                'module':  proposal.module,
                'action':  proposal.action,
                'args':    proposal.args,
                'source':  proposal.source,
                'tier':    tier.value,
                'note':    proposal.rationale or f'{proposal.module}.{proposal.action} (dry-run)',
            })
            audit.append({
                'module':         proposal.module, 'action': proposal.action,
                'args':           proposal.args,
                'result':         {'dry_run': True},
                'tier':           tier.value,
                'source':         proposal.source,
                'was_first_time': was_first, 'first_time_key': first_key,
                'reversible':     proposal.reversible,
                'user_approved':  user_approved,
                'was_dry_run':    True,
                'preview_path':   str(preview),
            })
            metrics.emit('proposal.approved' if user_approved else 'proposal.executed_dry',
                         {'module': proposal.module, 'action': proposal.action})
            return RouteResult(status='dry_run', proposal_id=proposal_id,
                               module=proposal.module, action=proposal.action,
                               tier=tier.value, was_first_time=was_first,
                               user_approved=user_approved,
                               preview_path=str(preview))

        # Live execution
        if mod is None:
            mod = self._lookup_module(proposal.module)
        if mod is None:
            audit.append({
                'module':  proposal.module, 'action': proposal.action,
                'args':    proposal.args, 'result': {'error': 'module not found at execute time'},
                'tier':    tier.value, 'source': proposal.source,
            })
            return RouteResult(status='error', proposal_id=proposal_id,
                               module=proposal.module, action=proposal.action,
                               tier=tier.value, was_first_time=was_first,
                               error='module not found')

        # Defensive: strip any duplicate 'action' key from args so we don't
        # TypeError on double-passing.
        safe_args = {k: v for k, v in (proposal.args or {}).items() if k != 'action'}
        try:
            result = mod.execute(action=proposal.action, **safe_args)
            ok = True
            err = None
        except Exception as e:
            result = None
            ok = False
            err = str(e)

        status = (
            'executed_auto'   if tier == ActionTier.AUTO else
            'executed_notify' if tier == ActionTier.NOTIFY else
            'executed_ask'
        ) if ok else 'error'

        audit.append({
            'module':         proposal.module, 'action': proposal.action,
            'args':           proposal.args,
            'result':         {'ok': ok, 'output_preview': (str(result)[:300] if result is not None else None),
                               **({'error': err} if err else {})},
            'tier':           tier.value,
            'source':         proposal.source,
            'was_first_time': was_first, 'first_time_key': first_key,
            'reversible':     proposal.reversible,
            'user_approved':  user_approved,
        })

        if ok:
            metrics.emit('proposal.approved' if user_approved else 'proposal.executed',
                         {'module': proposal.module, 'action': proposal.action})
        else:
            metrics.emit('proposal.failed',
                         {'module': proposal.module, 'action': proposal.action, 'error': err})

        return RouteResult(
            status=status, proposal_id=proposal_id,
            module=proposal.module, action=proposal.action,
            tier=tier.value, was_first_time=was_first,
            user_approved=user_approved, result=result, error=err,
        )
