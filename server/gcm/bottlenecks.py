"""Craft bottlenecks: which machine a running job is waiting on and why,
for its live tree, and running totals of that per machine, for the
Crafts tab's statistics.

What a CPU's lists mean, from AE2's CraftingCPUCluster (gcm/job_tree.py
has how they reach us):
  - pending: the outputs of pattern runs not yet sent to a machine. It
    falls when a run is pushed to an interface.
  - active: outputs sent out and not back yet. A push moves an amount
    from pending to active, so pending + active only falls when a
    machine gives its output back.
  - stored: the CPU's own inventory, the next push's inputs among it.
So tracking keeps, per step (gcm/tracking.py's "flow"), when it was last
pushed and when a machine last gave some back, and a step is held up:
  - blocked: runs pending, a run's inputs in the CPU, and nothing sent
    for CRAFT_BLOCK_GRACE_SECONDS: the machine side won't take it (its
    inventory full, blocking mode, a full pattern hatch).
  - busy: output out and none back for the grace period, while the same
    machine gave another step (this job's or another CPU's) something in
    that time: it's working for someone else.
  - stalled: output out, none back for CRAFT_STALL_SECONDS, and nothing
    else on that machine moving: stopped (power, maintenance, a full
    output, output not routed back to ME) - or a long recipe. OC can't
    see the machine and the game data has no recipe durations, which is
    why this one waits longer.
The rest are done, active (output out, coming back) or waiting (for
their inputs: the hold-up is further down).

A step's machine is the provider of the pattern making it - the job's
own pattern, picked as the live tree picks it (job_tree.used_by_job).
With several, AE2 takes turns and OC doesn't say which has the items,
so each gets a share. Providers are told apart by coordinates and name
(patterns.provider_key). Two interfaces on one machine are two
providers; for "busy", ones with the same name that touch a common block
count as the same machine.

account() runs after every status report. It adds the time since the
last one to each held-up step's provider, and to `critical` for the
providers holding the job's items when nothing in the whole job has
moved for the grace period - the job was waiting on them. Totals are
kept per hour, in app.db (store.bottlenecks)."""

import threading
import time

from gcm import config, job_tree, patterns, planner, state, store

# A step held up at its machine, the worst first: where a step's numbers
# merge several of them, it shows the first.
HELD = ("stalled", "busy", "blocked")
# Most time one report adds: past this the game wasn't reporting (a
# restart, craft_monitor.lua down), not the job waiting.
MAX_STEP_SECONDS = 60
# Machines the Crafts tab's statistics list.
TOP_MACHINES = 15
BUCKET_SECONDS = 3600

_lock = threading.Lock()
_network = (None, None)  # (the pattern list it was built from, _Network)
_job_makers = {}  # cpu -> ((started_at, step count, _Network), {step: [pattern]})
_accounted = {}  # cpu -> {started_at, at, counted: provider keys}


class _Network:
    """The pattern list as this module uses it, built once per scan:
    makers - {item base key: [planner pattern]}, in AE2's order;
    providers - {provider key: provider};
    mates - {provider key: the provider keys taken for its machine}."""

    def __init__(self, pattern_list):
        self.makers = {}
        for key, candidates in planner.index(pattern_list).candidates.items():
            self.makers.setdefault(job_tree.base_key(key), []).extend(candidates)
        self.providers = {}
        for p in pattern_list:
            self.providers.setdefault(patterns.provider_key(p["provider"]), p["provider"])
        self.mates = mates(self.providers)


def mates(providers):
    """{provider key: keys of the providers on the same machine}: itself,
    and those with its name touching a block it touches too - one block
    apart diagonally, or two in a line (distance 2 along the axes). A
    guess: OC says where an interface is, not what it's on."""
    out = {k: {k} for k in providers}
    by_name = {}
    for k, p in providers.items():
        if all(c in p for c in ("x", "y", "z")):
            by_name.setdefault((p.get("dim"), p["name"]), []).append(k)
    for keys in by_name.values():
        for i, a in enumerate(keys):
            pa = providers[a]
            for b in keys[i + 1:]:
                pb = providers[b]
                if sum(abs(pa[c] - pb[c]) for c in ("x", "y", "z")) == 2:
                    out[a].add(b)
                    out[b].add(a)
    return out


def _current_network():
    global _network
    pattern_list, scanned_at = patterns.current()
    if scanned_at is None:
        return None
    with _lock:
        if _network[0] is pattern_list:
            return _network[1]
    built = _Network(pattern_list)
    with _lock:
        _network = (pattern_list, built)
    return built


def _jobs():
    """What tracking knows of every busy CPU's job, copied."""
    with state.tracking_lock:
        return {
            cpu: {
                "started_at": e["started_at"],
                "peaks": dict(e["peaks"]),
                "first_stored": dict(e["first_stored"] or {}),
                "flow": {k: dict(f) for k, f in e["flow"].items()},
                "stored": dict(e["stored"]),
                "ready_since": dict(e["ready_since"]),
            }
            for cpu, e in state.cpu_last_known.items()
            if state.cpu_last_busy.get(cpu)
        }


def _makers(cpu, job, net):
    """{step: [planner pattern]}: what makes each of the job's steps - the
    patterns the job could be using, else every one making it."""
    key = (job["started_at"], len(job["peaks"]), net)
    with _lock:
        hit = _job_makers.get(cpu)
        if hit is not None and hit[0] == key:
            return hit[1]
    _, items = job_tree.job_items(job)
    out = {}
    for step in job["peaks"]:
        candidates = net.makers.get(job_tree.step_item_key(step), [])
        out[step] = [p for p in candidates if job_tree.used_by_job(p, items)] or candidates
    with _lock:
        _job_makers[cpu] = (key, out)
    return out


def _has_inputs(p, stored):
    """Whether `stored` ({item base key: amount}) holds one run of
    pattern p's inputs."""
    return all(stored.get(job_tree.base_key(k), 0) >= size for k, _, size in p.inputs + p.once)


def _note_ready(jobs, makers, now):
    """Keeps each step's ready_since - since when the CPU has held a run
    of its inputs - in tracking's entries, and in `jobs`, their copies."""
    with state.tracking_lock:
        for cpu, job in jobs.items():
            entry = state.cpu_last_known.get(cpu)
            if entry is None or entry["started_at"] != job["started_at"]:
                continue
            stored = {}
            for step, amount in job["stored"].items():
                k = job_tree.step_item_key(step)
                stored[k] = stored.get(k, 0) + amount
            for step, f in job["flow"].items():
                if f["pending"] > 0 and any(_has_inputs(p, stored) for p in makers[cpu].get(step, ())):
                    entry["ready_since"].setdefault(step, now)
                else:
                    entry["ready_since"].pop(step, None)
            job["ready_since"] = dict(entry["ready_since"])


def _classify(jobs, makers, net, now, only=None):
    """{cpu: {step: {state, since, providers, out, with}}} for the busy
    CPUs (or just `only`). since: when the state's clock started (held
    states); providers: the step's provider keys; out: whether it has
    output out at a machine; with: (cpu, step) a busy machine gave to."""
    grace, stall = config.CRAFT_BLOCK_GRACE_SECONDS, config.CRAFT_STALL_SECONDS

    def providers(cpu, step):
        keys = {patterns.provider_key(p.pattern["provider"]) for p in makers[cpu].get(step, ())}
        return sorted(keys, key=lambda k: tuple(str(v) for v in k))

    # Which machines gave something back lately, and to which step.
    gave = {}
    for cpu, job in jobs.items():
        for step, f in job["flow"].items():
            if f["gave_at"] is not None and now - f["gave_at"] < grace:
                for k in providers(cpu, step):
                    for mate in net.mates.get(k, (k,)) if net else (k,):
                        gave.setdefault(mate, []).append((cpu, step))

    def gave_other(keys, cpu, step):
        for k in keys:
            for other in gave.get(k, ()):
                if other != (cpu, step):
                    return other
        return None

    out = {}
    for cpu, job in jobs.items():
        if only is not None and cpu != only:
            continue
        results = out[cpu] = {}
        for step, f in job["flow"].items():
            keys = providers(cpu, step)
            r = results[step] = {"state": "waiting", "since": None, "providers": keys,
                                 "out": f["active"] > 0, "with": None}
            out_for = now - f["out_since"]
            ready_since = job["ready_since"].get(step)
            if f["pending"] + f["active"] <= 0:
                r["state"] = "done"
            elif f["active"] > 0 and out_for >= grace and (other := gave_other(keys, cpu, step)):
                r.update({"state": "busy", "since": f["out_since"], "with": other})
            elif f["active"] > 0 and out_for >= stall:
                r.update(state="stalled", since=f["out_since"])
            elif f["pending"] > 0 and ready_since is not None and now - max(f["pushed_at"], ready_since) >= grace:
                r.update(state="blocked", since=max(f["pushed_at"], ready_since))
            elif f["active"] > 0:
                r["state"] = "active"
    return out


def _gather():
    """(busy jobs, {cpu: their steps' makers}, the _Network or None)."""
    jobs = _jobs()
    net = _current_network()
    makers = {cpu: _makers(cpu, job, net) if net else {} for cpu, job in jobs.items()}
    return jobs, makers, net


def account(now=None):
    """Adds the time since each busy job's last report to the providers
    holding it up (see the module docstring), and drops what's kept of
    jobs that ended."""
    now = time.time() if now is None else now
    jobs, makers, net = _gather()
    _note_ready(jobs, makers, now)
    results = _classify(jobs, makers, net, now)
    grace = config.CRAFT_BLOCK_GRACE_SECONDS
    after = {"blocked": grace, "busy": grace, "stalled": config.CRAFT_STALL_SECONDS}
    rows = {}

    def add(key, field, seconds):
        row = rows.setdefault(key, {"blocked": 0.0, "busy": 0.0, "stalled": 0.0, "critical": 0.0, "jobs": 0})
        row[field] += seconds

    def held_for(dt, started):
        # The part of the last dt seconds after `started`: a state that
        # began since the last report only counts from then.
        return max(0.0, min(dt, now - started))

    with _lock:
        for cpu in [c for c in _accounted if c not in jobs]:
            del _accounted[cpu]
        for cpu in [c for c in _job_makers if c not in jobs]:
            del _job_makers[cpu]
        for cpu, job in jobs.items():
            last = _accounted.get(cpu)
            if last is None or last["started_at"] != job["started_at"]:
                _accounted[cpu] = {"started_at": job["started_at"], "at": now, "counted": set()}
                continue
            dt = min(now - last["at"], MAX_STEP_SECONDS)
            last["at"] = now
            if dt <= 0:
                continue
            touched = set()
            for r in results[cpu].values():
                seconds = held_for(dt, r["since"] + after[r["state"]]) if r["state"] in HELD else 0
                if seconds and r["providers"]:
                    for k in r["providers"]:
                        add(k, r["state"], seconds / len(r["providers"]))
                        touched.add(k)
            flows = job["flow"].values()
            moved = max((max(f["pushed_at"], f["gave_at"] or 0) for f in flows), default=now)
            seconds = held_for(dt, moved + grace)
            waited_on = {k for r in results[cpu].values() if r["state"] in HELD or r["out"]
                         for k in r["providers"]}
            if seconds and waited_on:
                for k in waited_on:
                    add(k, "critical", seconds / len(waited_on))
                touched |= waited_on
            for k in touched - last["counted"]:
                rows[k]["jobs"] += 1
            last["counted"] |= touched
    if rows:
        providers = net.providers if net else {}
        store.bottlenecks.add(now // BUCKET_SECONDS * BUCKET_SECONDS, [
            (key_text(k), providers.get(k, {"name": k[4]}), row) for k, row in rows.items()
        ])


def key_text(key):
    """A provider key as app.db keeps it."""
    return "|".join("" if v is None else str(v) for v in key)


def _where(key, net):
    provider = (net.providers.get(key) if net else None) or {"name": key[4]}
    return {k: provider.get(k) for k in ("name", "x", "y", "z", "dim")}


_RANK = {state: i for i, state in enumerate(HELD + ("active", "waiting", "done"))}


def steps(cpu, now=None):
    """job_tree.steps() for the CPU's job, each with its state, and for a
    held-up one since (when it started), machines ([{name, x, y, z, dim}])
    and with ({cpu, name}: who a busy machine is working for). None when
    the CPU is idle."""
    out = job_tree.steps(cpu)
    if out is None:
        return None
    now = time.time() if now is None else now
    jobs, makers, net = _gather()
    results = _classify(jobs, makers, net, now, only=cpu).get(cpu, {})
    for s in out.values():
        s["state"] = "done" if s["left"] <= 0 else "active" if s["crafting"] > 0 else "waiting"
    picked = {}
    for step, r in results.items():
        k = job_tree.step_item_key(step)
        if k not in out or (k in picked and _RANK[picked[k]["state"]] <= _RANK[r["state"]]):
            continue
        picked[k] = r
    for k, r in picked.items():
        s = out[k]
        s["state"] = r["state"]
        if r["state"] in HELD:
            s["since"] = r["since"]
            s["machines"] = [_where(key, net) for key in r["providers"]]
            if r["with"]:
                other_cpu, other_step = r["with"]
                s["with"] = {"cpu": other_cpu, "name": other_step[3]}
    return out


def summary(since):
    """The providers that held jobs up the longest since `since` (None:
    ever), the longest the whole job waited on first: [{name, x, y, z,
    dim, blocked, busy, stalled, critical, jobs}]."""
    start = None if since is None else since // BUCKET_SECONDS * BUCKET_SECONDS
    return store.bottlenecks.totals(start, TOP_MACHINES)


def reset():
    """Forgets everything kept in memory (tests)."""
    global _network
    with _lock:
        _network = (None, None)
        _job_makers.clear()
        _accounted.clear()
