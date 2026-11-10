"""Module 3: discrete-event simulation engine, cash state machine, S0/S1 strategies.

Public entry points (consistent with integration_contract.md):

    run_simulation(payments, inflows, config, strategy_id)
        -> (payment_results, event_log, balances)

    run_dataset(dataset, strategy_id) -> RunResult (including fx_trades and summary)

The fixed same-second event order is EVENT_ORDER. That ordering is Module 3's interpretation
and must be confirmed by Module 1/6 and recorded in interface_spec.md.
"""

from __future__ import annotations

import copy
import heapq
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from . import adapters
from .models import (INELIGIBLE, NOT_ARRIVED, PENDING, QUEUED, SETTLED,
                     ZERO, fmt, money)

# Same-second event order (lower number is processed first)
EVENT_ORDER = {
    "PAYMENT_ARRIVAL": 0,       # a new instruction arrives
    "INFLOW_NOTICE": 1,         # inflow announced (no cash yet)
    "INFLOW_ARRIVAL": 2,        # inflow actually credited
    "FX_COMPLETION": 3,         # FX completes, RMB credited
    "PAYMENT_CONFIRMATION": 4,  # payment confirmation result
    "FUNDING_REVIEW": 5,        # periodic re-review
}

# Engine-owned parameters. Module 2's config has no such fields: Module 3 owns them and the team must confirm.
ENGINE_DEFAULTS = {
    "payment_confirmation_delay_sec": 10,   # baseline value from proposal 4.1.4
    "payment_confirmation_fail_prob": 0.0,  # no confirmation failures in the baseline
    "observation_drain_sec": 0,             # observation endpoint = simulation_window_sec + drain
    "fx_buffer_rmb": 0.0,                   # TopUp = gap + buffer
    # per_trade: each trade <= capacity; cumulative: total quota for the run
    "fx_counterparty_capacity_mode": "per_trade",
}


def resolve_engine_params(config: dict) -> dict:
    params = dict(ENGINE_DEFAULTS)
    for key in ENGINE_DEFAULTS:
        if key in config:
            params[key] = config[key]
    params.update({k: v for k, v in (config.get("engine_parameters") or {}).items()
                   if k in ENGINE_DEFAULTS})
    return params


@dataclass
class State:
    t: int
    rmb_total: Decimal
    rmb_reserved: Decimal
    rmb_locked: Decimal
    hkd_total: Decimal
    hkd_committed: Decimal
    hkd_buffer: Decimal
    cpty_remaining: Decimal
    pending_fx: list[dict] = field(default_factory=list)

    @property
    def rmb_available(self) -> Decimal:
        value = self.rmb_total - self.rmb_locked - self.rmb_reserved
        return value if value > ZERO() else ZERO()

    @property
    def hkd_available(self) -> Decimal:
        value = self.hkd_total - self.hkd_committed - self.hkd_buffer
        return value if value > ZERO() else ZERO()


@dataclass
class RunResult:
    run_id: str
    strategy_id: str
    payments: list[dict]
    events: list[dict]
    balances: list[dict]
    fx_trades: list[dict]
    summary: dict


class Simulation:
    def __init__(self, payments, inflows, config: dict, strategy_id: str,
                 rank_fn: Callable | None = None,
                 decide_fn: Callable | None = None,
                 using_fallback: tuple[bool, bool] | None = None):
        if strategy_id not in ("S0", "S1", "S2", "S3", "S4"):
            raise ValueError(f"unknown strategy {strategy_id!r}; expected S0-S4")

        self.config = config
        self.strategy = strategy_id
        self.dataset_id = str(config.get("dataset_id", "dataset"))
        self.run_id = f"{self.dataset_id}_{strategy_id}"
        self.params = resolve_engine_params(config)

        # S0/S1 are implemented entirely inside Module 3 and do not depend on Module 4/5;
        # S2/S4 need rank_payments, S3/S4 need decide_funding.
        fallback_flags = [False, False]
        if rank_fn is None:
            if strategy_id in ("S2", "S4"):
                rank_fn, fallback_flags[0] = adapters.load_rank_payments()
            else:
                rank_fn = adapters.fallback_rank_payments
        if decide_fn is None:
            if strategy_id in ("S3", "S4"):
                decide_fn, fallback_flags[1] = adapters.load_decide_funding()
            else:
                decide_fn = adapters.fallback_decide_funding
        self.rank_fn = rank_fn
        self.decide_fn = decide_fn
        self.using_fallback = using_fallback or tuple(fallback_flags)

        self.payments = copy.deepcopy(payments)
        self.inflows = copy.deepcopy(inflows)
        self.by_id = {p.payment_id: p for p in self.payments}

        self.window = int(config["simulation_window_sec"])
        self.obs_end = self.window + int(self.params["observation_drain_sec"])
        self.confirm_delay = int(self.params["payment_confirmation_delay_sec"])
        self.confirm_fail_prob = float(self.params["payment_confirmation_fail_prob"])

        self.fx_rate = Decimal(str(config["fx_rate"]))
        spread = Decimal(str(config.get("fx_spread_bps", 0))) / Decimal("10000")
        self.effective_rate = self.fx_rate * (Decimal("1") - spread)
        if self.effective_rate <= 0:
            raise ValueError("fx_rate combined with fx_spread_bps makes the effective rate non-positive")
        self.fx_fee_hkd = money(config.get("fx_fee_fixed_hkd", 0))
        self.fx_delay = int(config["fx_execution_delay_sec"])
        self.fx_min = money(config["fx_topup_min_rmb"])
        self.fx_max = money(config["fx_topup_max_rmb"])
        self.fx_capacity = money(config["fx_counterparty_capacity_rmb"])

        self.state = State(
            t=0,
            rmb_total=money(config["initial_rmb_balance"]),
            rmb_reserved=money(config["rmb_reserve"]),
            rmb_locked=ZERO(),
            hkd_total=money(config["initial_hkd_balance"]),
            hkd_committed=ZERO(),
            hkd_buffer=money(config.get("hkd_liquidity_buffer", 0)),
            cpty_remaining=self.fx_capacity,
        )

        # the same seed gives identical confirmation outcomes across strategies, so paired comparison holds
        self.rng = random.Random(int(config.get("seed", 0)))

        self.now = 0
        self.heap: list = []
        self.seq = 0
        self.events: list[dict] = []
        self.balances: list[dict] = []
        self.fx_trades: list[dict] = []
        self.fx_seq = 0
        self.fx_initiated = 0
        self.fx_completed = 0
        self.fx_infeasible = 0
        self.fx_cost_total = ZERO()
        self._decision_state: dict[str, tuple] = {}

    # ------------------------------------------------------------------
    # Event scheduling
    # ------------------------------------------------------------------
    def _schedule(self, when: int, kind: str, payload=None) -> None:
        self.seq += 1
        heapq.heappush(self.heap, (int(when), EVENT_ORDER[kind], self.seq, kind, payload))

    def _schedule_exogenous(self) -> None:
        for p in self.payments:
            if p.arrival_time <= self.obs_end:
                self._schedule(p.arrival_time, "PAYMENT_ARRIVAL", p.payment_id)
        for f in self.inflows:
            if f.notice_time is not None and f.notice_time <= self.obs_end:
                self._schedule(f.notice_time, "INFLOW_NOTICE", f.inflow_id)
            if f.arrival_time <= self.obs_end:
                self._schedule(f.arrival_time, "INFLOW_ARRIVAL", f.inflow_id)

    def _schedule_reviews(self) -> None:
        interval = max(1, int(self.config.get("funding_review_interval_sec", 60)))
        when = interval
        while when <= self.obs_end:
            self._schedule(when, "FUNDING_REVIEW", None)
            when += interval

    # ------------------------------------------------------------------
    # Logging and snapshots
    # ------------------------------------------------------------------
    def _log(self, event_type: str, payment_id: str = "", fx_id: str = "",
             action: str = "", reason: str = "") -> int:
        seq = len(self.events)
        self.events.append({
            "run_id": self.run_id, "event_seq": seq, "time": self.now,
            "event_type": event_type, "payment_id": payment_id,
            "fx_id": fx_id, "action": action, "reason": reason,
        })
        self._snapshot(seq)
        return seq

    def _snapshot(self, seq: int) -> None:
        state = self.state
        self.balances.append({
            "run_id": self.run_id, "event_seq": seq, "time": self.now,
            "rmb_total": fmt(state.rmb_total),
            "rmb_reserved": fmt(state.rmb_reserved),
            "rmb_locked": fmt(state.rmb_locked),
            "rmb_available": fmt(state.rmb_available),
            "hkd_total": fmt(state.hkd_total),
            "hkd_committed": fmt(state.hkd_committed),
            "hkd_available": fmt(state.hkd_available),
        })

    def _log_decision(self, payment, action: str, reason: str) -> None:
        """Log once per (payment, action, reason) so review ticks do not flood the event log."""
        signature = (action, reason)
        if self._decision_state.get(payment.payment_id) == signature:
            return
        self._decision_state[payment.payment_id] = signature
        self._log("FUNDING_DECISION", payment_id=payment.payment_id,
                  action=action, reason=reason)

    # ------------------------------------------------------------------
    # State views handed to Module 4/5 (observed information only)
    # ------------------------------------------------------------------
    def _payment_view(self, payment) -> dict:
        return {
            "payment_id": payment.payment_id,
            "arrival_time": payment.arrival_time,
            "amount_rmb": payment.amount_rmb,
            "priority": payment.priority,
            "deadline_time": payment.deadline_time,
            "submit_time": payment.submit_time,
            "waiting_time": self.now - payment.arrival_time,
        }

    def _state_view(self) -> dict:
        state = self.state
        return {
            "t": self.now,
            "rmb_total": state.rmb_total,
            "rmb_reserved": state.rmb_reserved,
            "rmb_locked": state.rmb_locked,
            "rmb_available": state.rmb_available,
            "hkd_total": state.hkd_total,
            "hkd_committed": state.hkd_committed,
            "hkd_available": state.hkd_available,
            "cpty_remaining": state.cpty_remaining,
            "pending_fx": [dict(record) for record in state.pending_fx],
        }

    def _queue_view(self) -> list[dict]:
        queued = [p for p in self.payments if p.status == QUEUED]
        queued.sort(key=lambda p: (p.arrival_time, p.payment_id))
        return [self._payment_view(p) for p in queued]

    def _known_inflows(self) -> list[dict]:
        """Return only announced, not-yet-credited inflows inside the prediction window.

        Module 5 receives this filtered list, never the complete inflows.csv.
        """
        prediction = self.config.get("prediction_parameters", {})
        lookahead = int(prediction.get("max_lookahead_sec", 10 ** 9))
        min_notice = int(prediction.get("minimum_advance_notice_sec", 0))
        now = self.now
        result = []
        for inflow in self.inflows:
            if inflow.arrived or inflow.notice_time is None:
                continue
            if inflow.notice_time > now or inflow.arrival_time < now:
                continue
            if inflow.arrival_time > now + lookahead:
                continue
            if inflow.arrival_time - inflow.notice_time < min_notice:
                continue
            result.append({
                "inflow_id": inflow.inflow_id,
                "arrival_time": inflow.arrival_time,
                "amount_rmb": inflow.amount_rmb,
            })
        result.sort(key=lambda row: row["arrival_time"])
        return result

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> RunResult:
        self._schedule_exogenous()
        self._schedule_reviews()
        self._log("SIMULATION_START", action="INIT",
                  reason=f"strategy={self.strategy};window={self.window}")

        while self.heap:
            when, _, _, kind, payload = heapq.heappop(self.heap)
            if when > self.obs_end:
                break
            self.now = when
            self.state.t = when
            self._dispatch(kind, payload)

        self.now = self.obs_end
        self.state.t = self.obs_end
        self._finalise()
        return self._build_result()

    def _dispatch(self, kind: str, payload) -> None:
        if kind == "PAYMENT_ARRIVAL":
            self._on_payment_arrival(payload)
        elif kind == "INFLOW_NOTICE":
            self._on_inflow_notice(payload)
        elif kind == "INFLOW_ARRIVAL":
            self._on_inflow_arrival(payload)
        elif kind == "FX_COMPLETION":
            self._on_fx_completion(payload)
        elif kind == "PAYMENT_CONFIRMATION":
            self._on_payment_confirmation(payload)
        # FUNDING_REVIEW itself needs no action; the shared re-review below covers it
        self._review()

    def _on_payment_arrival(self, payment_id: str) -> None:
        payment = self.by_id[payment_id]
        if payment.status == INELIGIBLE:
            self._log("PAYMENT_ARRIVAL", payment_id=payment_id, action="SKIP",
                      reason="eligible=false; engine neither submits nor reserves funds")
            return
        payment.status = QUEUED
        self._log("PAYMENT_ARRIVAL", payment_id=payment_id)

    def _on_inflow_notice(self, inflow_id: str) -> None:
        self._log("INFLOW_NOTICE", reason=f"inflow_id={inflow_id}")

    def _on_inflow_arrival(self, inflow_id: str) -> None:
        inflow = next(f for f in self.inflows if f.inflow_id == inflow_id)
        if inflow.arrived:
            return
        inflow.arrived = True
        self.state.rmb_total += inflow.amount_rmb
        self._log("INFLOW_ARRIVAL", reason=(
            f"inflow_id={inflow_id};amount_rmb={fmt(inflow.amount_rmb)}"))

    def _on_fx_completion(self, fx_id: str) -> None:
        record = next((r for r in self.state.pending_fx if r["fx_id"] == fx_id), None)
        if record is None:
            return
        self.state.pending_fx.remove(record)
        committed = record["hkd_committed"]
        self.state.hkd_committed -= committed
        self.state.hkd_total -= committed
        self.state.rmb_total += record["topup_rmb"]
        record["status"] = "COMPLETED"
        record["completed_at"] = self.now
        self.fx_trades.append(record)
        self.fx_completed += 1
        self._log("FX_COMPLETED", fx_id=fx_id, action="CREDIT",
                  reason=f"rmb_credited={fmt(record['topup_rmb'])}")

    def _on_payment_confirmation(self, payment_id: str) -> None:
        payment = self.by_id[payment_id]
        self.state.rmb_locked -= payment.amount_rmb
        if self.state.rmb_locked < ZERO():
            self.state.rmb_locked = ZERO()
        if self.rng.random() < self.confirm_fail_prob:
            payment.status = QUEUED
            payment.submit_time = None
            payment.retry_count += 1
            self._log("PAYMENT_CONFIRM_FAILED", payment_id=payment_id, action="RELEASE",
                      reason="confirmation failed; locked funds released and payment re-queued")
            return
        self.state.rmb_total -= payment.amount_rmb
        payment.status = SETTLED
        payment.settle_time = self.now
        self._log("PAYMENT_SETTLED", payment_id=payment_id)

    # ------------------------------------------------------------------
    # Queue processing and funding decisions
    # ------------------------------------------------------------------
    def _ordered_queue(self) -> list:
        queued = [p for p in self.payments if p.status == QUEUED]
        if not queued:
            return []
        if self.strategy in ("S2", "S4"):
            ranked = self.rank_fn([self._payment_view(p) for p in queued],
                                  self._state_view(), self.config)
            order = list(ranked.get("order") or [])
            known = {p.payment_id for p in queued}
            ordered = [self.by_id[pid] for pid in order
                       if pid in known and self.by_id[pid].status == QUEUED]
            # append instructions Module 4 omitted so the engine never drops a payment
            missing = [p for p in queued if p not in ordered]
            missing.sort(key=lambda p: (p.arrival_time, p.payment_id))
            return ordered + missing
        return sorted(queued, key=lambda p: (p.arrival_time, p.payment_id))

    def _review(self) -> None:
        skippable = self.strategy != "S0"
        for _ in range(len(self.payments) + 10):
            order = self._ordered_queue()
            if not order:
                return
            submitted = False
            blocked = None
            for payment in order:
                if payment.amount_rmb <= self.state.rmb_available:
                    self._submit(payment)
                    submitted = True
                    if not skippable:
                        break
                else:
                    if blocked is None:
                        blocked = payment
                    if not skippable:
                        break  # S0 only inspects the queue head
            if submitted:
                continue
            if blocked is None:
                return
            self._funding_decision(blocked)
            return

    def _submit(self, payment) -> None:
        payment.status = PENDING
        payment.submit_time = self.now
        self.state.rmb_locked += payment.amount_rmb
        self._schedule(self.now + self.confirm_delay, "PAYMENT_CONFIRMATION",
                       payment.payment_id)
        self._log("PAYMENT_SUBMITTED", payment_id=payment.payment_id, action="SUBMIT",
                  reason=f"amount_rmb={fmt(payment.amount_rmb)}")

    def _funding_decision(self, target) -> None:
        if self.strategy in ("S0", "S1", "S2"):
            self._execute_top_up(target, None)
            return

        decision = self.decide_fn(self._queue_view(), self._state_view(),
                                  self._known_inflows(), self.config, "wait_or_topup")
        action = str(decision.get("action", "NO_ACTION")).upper()
        reason = str(decision.get("reason", "module5"))
        target_id = decision.get("target_payment_id") or target.payment_id
        chosen = self.by_id.get(str(target_id), target)

        if action == "WAIT":
            self._log_decision(chosen, "WAIT", reason)
        elif action == "TOP_UP":
            self._decision_state[chosen.payment_id] = ("TOP_UP", reason)
            self._execute_top_up(chosen, decision.get("topup_rmb"), reason)
        else:
            self._log_decision(chosen, "NO_ACTION", reason)

    def _pending_fx_rmb(self) -> Decimal:
        total = ZERO()
        for record in self.state.pending_fx:
            total += record["topup_rmb"]
        return total

    def _execute_top_up(self, target, requested=None, reason: str = "immediate") -> bool:
        gap = target.amount_rmb - self.state.rmb_available
        if gap <= ZERO():
            return False

        if self._pending_fx_rmb() >= gap:
            self._log_decision(target, "NO_ACTION", "pending_fx_covers_gap")
            return False

        if requested is None:
            need = gap + money(self.params["fx_buffer_rmb"])
        else:
            need = money(requested)
            if need <= ZERO():
                need = gap
        if need < self.fx_min:
            need = self.fx_min

        if need > self.fx_max:
            self.fx_infeasible += 1
            self._log_decision(target, "NO_ACTION", "exceeds_fx_topup_max_rmb")
            return False

        limit = (self.state.cpty_remaining
                 if self.params["fx_counterparty_capacity_mode"] == "cumulative"
                 else self.fx_capacity)
        if need > limit:
            self.fx_infeasible += 1
            self._log_decision(target, "NO_ACTION", "exceeds_counterparty_capacity")
            return False

        hkd_principal = money(need / self.effective_rate)
        committed = hkd_principal + self.fx_fee_hkd
        if committed > self.state.hkd_available:
            self.fx_infeasible += 1
            self._log_decision(target, "NO_ACTION", "insufficient_hkd")
            return False

        mid_hkd = need / self.fx_rate
        spread_cost = money((hkd_principal - mid_hkd) * self.fx_rate)
        fee_cost = money(self.fx_fee_hkd * self.fx_rate)
        total_cost = spread_cost + fee_cost

        self.fx_seq += 1
        fx_id = f"FX{self.fx_seq:04d}"
        record = {
            "fx_id": fx_id, "target_payment_id": target.payment_id,
            "initiated_at": self.now, "completed_at": None,
            "topup_rmb": need, "hkd_principal": hkd_principal,
            "hkd_fee": self.fx_fee_hkd, "hkd_committed": committed,
            "spread_cost_rmb": spread_cost, "total_cost_rmb": total_cost,
            "status": "PENDING", "reason": reason,
        }
        self.state.hkd_committed += committed
        if self.params["fx_counterparty_capacity_mode"] == "cumulative":
            self.state.cpty_remaining -= need
        self.state.pending_fx.append(record)
        self.fx_initiated += 1
        self.fx_cost_total += total_cost
        self._schedule(self.now + self.fx_delay, "FX_COMPLETION", fx_id)
        self._log("FX_INITIATED", payment_id=target.payment_id, fx_id=fx_id,
                  action="TOP_UP", reason=(
                      f"topup_rmb={fmt(need)};hkd_principal={fmt(hkd_principal)};"
                      f"hkd_fee={fmt(self.fx_fee_hkd)};spread_cost_rmb={fmt(spread_cost)}"))
        return True

    # ------------------------------------------------------------------
    # Finalisation and output
    # ------------------------------------------------------------------
    def _finalise(self) -> None:
        for payment in self.payments:
            if payment.status == PENDING:
                continue
            if payment.status == INELIGIBLE:
                continue
            if payment.status != SETTLED:
                payment.status = QUEUED
        self._log("SIMULATION_END", action="END",
                  reason=f"queued={sum(p.status == QUEUED for p in self.payments)}")

    def _build_result(self) -> RunResult:
        payment_rows = []
        for payment in sorted(self.payments, key=lambda p: (p.arrival_time, p.payment_id)):
            payment_rows.append({
                "run_id": self.run_id,
                "strategy_id": self.strategy,
                "payment_id": payment.payment_id,
                "arrival_time": payment.arrival_time,
                "amount_rmb": fmt(payment.amount_rmb),
                "priority": payment.priority,
                "deadline_time": payment.deadline_time,
                "submit_time": "" if payment.submit_time is None else payment.submit_time,
                "settle_time": "" if payment.settle_time is None else payment.settle_time,
                "final_status": payment.status,
            })

        trade_rows = []
        for record in sorted(self.fx_trades + self.state.pending_fx,
                             key=lambda r: r["fx_id"]):
            trade_rows.append({
                "run_id": self.run_id,
                "fx_id": record["fx_id"],
                "target_payment_id": record["target_payment_id"],
                "initiated_at": record["initiated_at"],
                "completed_at": "" if record["completed_at"] is None else record["completed_at"],
                "topup_rmb": fmt(record["topup_rmb"]),
                "hkd_principal": fmt(record["hkd_principal"]),
                "hkd_fee": fmt(record["hkd_fee"]),
                "spread_cost_rmb": fmt(record["spread_cost_rmb"]),
                "total_cost_rmb": fmt(record["total_cost_rmb"]),
                "status": record["status"],
                "reason": record["reason"],
            })

        settled = sum(p.status == SETTLED for p in self.payments)
        pending = sum(p.status == PENDING for p in self.payments)
        queued = sum(p.status == QUEUED for p in self.payments)
        ineligible = sum(p.status == INELIGIBLE for p in self.payments)

        summary = {
            "run_id": self.run_id,
            "strategy_id": self.strategy,
            "dataset_id": self.dataset_id,
            "scenario_id": self.config.get("scenario_id"),
            "scenario_description": self.config.get("scenario_description"),
            "phase": self.config.get("phase"),
            "seed": self.config.get("seed"),
            "schema_version": self.config.get("schema_version"),
            "generator_version": self.config.get("generator_version"),
            "simulation_window_sec": self.window,
            "observation_end_sec": self.obs_end,
            "n_payments": len(self.payments),
            "n_inflows": len(self.inflows),
            "settled": settled,
            "pending": pending,
            "queued": queued,
            "ineligible": ineligible,
            "fx_initiated": self.fx_initiated,
            "fx_completed": self.fx_completed,
            "fx_infeasible": self.fx_infeasible,
            "fx_cost_rmb": fmt(self.fx_cost_total),
            "rmb_total_end": fmt(self.state.rmb_total),
            "rmb_locked_end": fmt(self.state.rmb_locked),
            "hkd_total_end": fmt(self.state.hkd_total),
            "hkd_committed_end": fmt(self.state.hkd_committed),
            "engine_parameters": {k: v for k, v in self.params.items()},
            "event_order": EVENT_ORDER,
            "using_fallback_module4": self.using_fallback[0],
            "using_fallback_module5": self.using_fallback[1],
        }

        return RunResult(run_id=self.run_id, strategy_id=self.strategy,
                         payments=payment_rows, events=self.events,
                         balances=self.balances, fx_trades=trade_rows,
                         summary=summary)


# ----------------------------------------------------------------------
# Public entry points
# ----------------------------------------------------------------------

def run_dataset(dataset, strategy_id: str, **kwargs) -> RunResult:
    simulation = Simulation(dataset.payments, dataset.inflows, dataset.config,
                            strategy_id, **kwargs)
    return simulation.run()


def run_simulation(payments, inflows, config: dict, strategy_id: str, **kwargs):
    """Contract signature: returns (payment_results, event_log, balances)."""
    simulation = Simulation(payments, inflows, config, strategy_id, **kwargs)
    result = simulation.run()
    return result.payments, result.events, result.balances
