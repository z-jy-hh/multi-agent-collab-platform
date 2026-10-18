"""R17-P4 review by zcode: P1 three-way gate + P2 health + P3 mutex + P4 counters."""
import os, sys
os.environ["AGENT_REGISTRY"] = "1"
sys.path.insert(0, ".")

from agent_bridge.dispatch_hooks import pre_run_gate, release_run_gate
from agent_bridge.errors import SpiError
from agent_bridge.dispatch import DispatchGate

FLAGS = ["protocol_version","transport","permission_has_path","permission_options",
         "usage_shape","cancel_is_notification","server_times_out","supports_resume",
         "supports_background","paid","single_prompt_per_session","single_root_cwd",
         "single_handler_slot","audit_hash_chain","sandbox_semantics",
         "supports_elicitation","egress"]

def _caps():
    return {n: {"value": n in ("cancel_is_notification","single_prompt_per_session",
                               "single_root_cwd","single_handler_slot","supports_resume"),
                "evidence": "test"} for n in FLAGS}

def _entry():
    return {"adapter": "acp", "enabled": True,
            "command": ["{node}", "{dsh_bin}", "--profile", "acp"],
            "cwd": {"template": "runs/dsh_dispatch/{run_id}", "must_contain": True},
            "permission": {"mode_default": "workspace-write",
                           "mode_allowed": ["read-only", "workspace-write"]},
            "capabilities": _caps(),
            "version_lock": {"agent_version": None}}

def _reg(daily=600):
    return {"platform_limits": {"consent": "required", "daily_tool_calls": daily,
                                "hourly_tool_calls": 120, "daily_wall_s": 14400,
                                "max_concurrent_runs_per_backend": 2},
            "bindings": {"dsh-exec": "dsh:"},
            "backends": {"dsh": _entry()},
            "defaults": {"deadline_permission_s": 5, "deadline_prompt_s": 5,
                         "close_grace_s": 5}}

os.environ["DSH_DISPATCH_ROOT"] = "./runs/p4review"
os.makedirs("./runs/p4review", exist_ok=True)
DispatchGate._shared_freshness.clear()

# --- P1a: gate ON + daily_tool_calls=0 => REFUSED
os.environ["DISPATCH_HETERO"] = "1"
try:
    pre_run_gate("supervisor", run_id="rev-1a", registry_data=_reg(daily=0))
    print("P1a-GATE-ON-BAD-LIMITS: PASSED (should have been REJECTED!)")
except SpiError as e:
    print(f"P1a-GATE-ON-BAD-LIMITS: REJECTED => {e}")
except Exception as e:
    print(f"P1a-GATE-ON-BAD-LIMITS: UNEXPECTED {type(e).__name__}: {e}")

# --- P1b: gate OFF + same bad registry => not consulted
os.environ["DISPATCH_HETERO"] = "0"
try:
    pre_run_gate("supervisor", run_id="rev-1b", registry_data=_reg(daily=0))
    print("P1b-GATE-OFF: NOT-CONSULTED (correct)")
except Exception as e:
    print(f"1b-GATE-OFF: UNEXPECTED {e}")

# --- P1c: gate ON + good registry => satisfiable (freshness is auto-read)
os.environ["DISPATCH_HETERO"] = "1"
DispatchGate._shared_freshness.clear()
try:
    token = pre_run_gate("supervisor", run_id="rev-1c", registry_data=_reg())
    print(f"P1c-GATE-ON-GOOD: PASSED (locks={token.get('locks')}, backends={token.get('backends')}, reads={token.get('reads')})")
    release_run_gate(token)
    print("P1c-RELEASE: ok")
except Exception as e:
    print(f"P1c-GATE-ON-GOOD: {type(e).__name__}: {e}")

# --- P2: health_read result
from agent_bridge.health import health_read
ok, detail = health_read("dsh")
print(f"P2-HEALTH-READ: ok={ok} detail={detail[:80]}")

# --- P2b: failed health read not treated as fresh
DispatchGate._shared_freshness.clear()
DispatchGate._shared_freshness["dsh"] = {"result": False, "ts": __import__("time").monotonic(), "detail": "failed"}
gate = DispatchGate()
rec = gate._check_freshness("dsh")
print(f"P2b-FAILED-READ: fresh={rec['fresh']} result={rec['result']} (must be fresh=True but result=False => caller rejects)")

# --- P3: workspace mutex cross-instance + release
cwd1 = os.path.join(os.getcwd(), "runs", "p4review", "dsh_dispatch")
gate1 = DispatchGate()
gate2 = DispatchGate()
gate1.acquire_workspace(cwd1)
try:
    gate2.acquire_workspace(cwd1)
    print("P3a-CROSS-INSTANCE: PASSED (should have been REJECTED!)")
except SpiError as e:
    print(f"P3a-CROSS-INSTANCE: REJECTED => {e}")
gate1.release_workspace(cwd1)
gate2.acquire_workspace(cwd1)
print("P3b-RELEASE-THEN-ACQUIRE: ok")
gate2.release_workspace(cwd1)

# --- P4: counters
from agent_bridge import gate_counters as gc
gc.reset()
gc.record_dispatch("dsh")
gc.register_active("dsh")
snap = gc.snapshot()
print(f"P4-COUNTERS: daily={snap['daily']} hourly={snap['hourly']} active={snap['active']}")
gc.unregister_active("dsh")
snap2 = gc.snapshot()
print(f"P4-AFTER-UNREGISTER: active={snap2['active']}")
gc.reset()

# --- P5: dispatch_bound_leaves with fake backend
from agent_bridge.hetero_dispatch import dispatch_bound_leaves
from unittest.mock import patch
from agent_bridge.spi import TaskResult

class FakeBackend:
    backend_id = "dsh"
    def __init__(self): self.calls = []
    def execute_task(self, task, *, budget, authz):
        self.calls.append(task)
        return TaskResult(stop_reason="end_turn", final_text="leaf done",
                          audit_refs={"backend": "dsh", "session_key": "test", "backend_session_id": "fake-sess"})

fake = FakeBackend()
class FakeBus:
    def __init__(self): self.events = []
    def emit(self, t, r, p, **kw): self.events.append((t, p))

with patch("agent_bridge.factory.build_backend", return_value=fake):
    bus = FakeBus()
    results = dispatch_bound_leaves(run_id="p5-run", session_id=None, task_text="leaf task",
                                     bus=bus, registry_data=_reg())
    assert "dsh-exec" in results, f"expected result, got {results}"
    assert fake.calls and fake.calls[0].task_text == "leaf task"
    started = [p for t,p in bus.events if t == "hetero_dispatch_started"]
    res_ev = [p for t,p in bus.events if t == "hetero_dispatch_result"]
    print(f"P5-DISPATCH-REAL: executed={len(fake.calls)} started={len(started)} result={len(res_ev)}")
    print(f"P5-AUDIT-REFS: {res_ev[0].get('audit_refs') if res_ev else 'N/A'}")

print("=== P1-P5 DONE ===")
