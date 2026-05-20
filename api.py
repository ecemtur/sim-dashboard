import simpy
import random
import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="DO-160 Test Simulatoru")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ENV_TESTS_ALL  = ["Sec.4 Temp/Altitude", "Sec.5 Temp Variation", "Sec.6 Humidity"]
EMC_TESTS_ALL  = ["Sec.18 CS", "Sec.19 RS", "Sec.20 CS/RS", "Sec.21 CE/RE", "Sec.25 ESD"]
MECH_TESTS_ALL = ["Sec.7 Shock", "Sec.8 Vibration", "Sec.15 Magnetic", "Sec.16 Power Input", "Sec.17 Voltage Spike"]


class SimRequest(BaseModel):
    replications: int = 30
    sim_days: float = 120
    env_capacity: int = 3
    emc_capacity: int = 4
    mech_capacity: int = 2
    rework_capacity: int = 1
    env_fail_pct: float = 10
    emc_fail_pct: float = 20
    mech_fail_pct: float = 10
    qr_fail_pct: float = 5
    final_fail_pct: float = 10
    num_prototypes: int = 3
    num_equipment: int = 4
    selected_env: Optional[List[str]] = None
    selected_emc: Optional[List[str]] = None
    selected_mech: Optional[List[str]] = None


def tria(low, mode, high):
    return random.triangular(low, high, mode)


def run_replication(p):
    env = simpy.Environment()

    sel_env  = p.selected_env  if p.selected_env  is not None else ENV_TESTS_ALL
    sel_emc  = p.selected_emc  if p.selected_emc  is not None else EMC_TESTS_ALL
    sel_mech = p.selected_mech if p.selected_mech is not None else MECH_TESTS_ALL

    scale_env  = max(len(sel_env)  / len(ENV_TESTS_ALL),  0.1)
    scale_emc  = max(len(sel_emc)  / len(EMC_TESTS_ALL),  0.1)
    scale_mech = max(len(sel_mech) / len(MECH_TESTS_ALL), 0.1)

    res_env    = simpy.Resource(env, capacity=p.env_capacity)
    res_emc    = simpy.Resource(env, capacity=p.emc_capacity)
    res_mech   = simpy.Resource(env, capacity=p.mech_capacity)
    res_rework = simpy.Resource(env, capacity=p.rework_capacity)
    res_qa     = simpy.Resource(env, capacity=1)
    res_func   = simpy.Resource(env, capacity=1)

    eq_completed      = {i: 0 for i in range(p.num_equipment)}
    eq_review_started = {i: False for i in range(p.num_equipment)}
    eq_finish_time    = {}
    fail_log          = []

    def prototype_proc(eq_id, proto_id):
        group_idx = proto_id % 3

        if group_idx == 0:
            for attempt in range(3):
                with res_env.request() as req:
                    yield req
                    yield env.timeout(tria(7, 9.5, 15) * scale_env)
                if random.random() * 100 >= p.env_fail_pct:
                    break
                fail_log.append({"group": "ENV", "eq": eq_id, "proto": proto_id, "time": round(env.now, 2)})
                if attempt < 2:
                    with res_rework.request() as req:
                        yield req
                        yield env.timeout(tria(5, 10, 20))
                else:
                    return

        elif group_idx == 1:
            for attempt in range(4):
                with res_emc.request() as req:
                    yield req
                    yield env.timeout(tria(6, 9.5, 17) * scale_emc)
                if random.random() * 100 >= p.emc_fail_pct:
                    break
                fail_log.append({"group": "EMC", "eq": eq_id, "proto": proto_id, "time": round(env.now, 2)})
                if attempt < 3:
                    with res_rework.request() as req:
                        yield req
                        yield env.timeout(tria(5, 15, 30))
                else:
                    return

        elif group_idx == 2:
            for attempt in range(3):
                with res_mech.request() as req:
                    yield req
                    yield env.timeout(tria(4, 7.5, 14) * scale_mech)
                if random.random() * 100 >= p.mech_fail_pct:
                    break
                fail_log.append({"group": "MECH", "eq": eq_id, "proto": proto_id, "time": round(env.now, 2)})
                if attempt < 2:
                    with res_rework.request() as req:
                        yield req
                        yield env.timeout(tria(3, 8, 20))
                else:
                    return

        eq_completed[eq_id] += 1
        groups_needed = min(p.num_prototypes, 3)
        if eq_completed[eq_id] >= groups_needed and not eq_review_started[eq_id]:
            eq_review_started[eq_id] = True
            env.process(review_proc(eq_id))

    def review_proc(eq_id):
        while True:
            with res_qa.request() as req:
                yield req
                yield env.timeout(tria(1, 2, 5))
            if random.random() * 100 >= p.qr_fail_pct:
                break
            fail_log.append({"group": "REVIEW", "eq": eq_id, "proto": -1, "time": round(env.now, 2)})
            with res_rework.request() as req:
                yield req
                yield env.timeout(tria(2, 5, 10))

        while True:
            with res_func.request() as req:
                yield req
                yield env.timeout(tria(0.5, 1, 2))
            if random.random() * 100 >= p.final_fail_pct:
                break
            fail_log.append({"group": "FINAL", "eq": eq_id, "proto": -1, "time": round(env.now, 2)})
            with res_rework.request() as req:
                yield req
                yield env.timeout(tria(3, 7, 15))

        eq_finish_time[eq_id] = round(env.now, 2)

    for eq in range(p.num_equipment):
        for proto in range(p.num_prototypes):
            env.process(prototype_proc(eq, proto))

    env.run(until=p.sim_days)

    total_time = max(eq_finish_time.values()) if eq_finish_time else p.sim_days
    return round(total_time, 2), fail_log


@app.get("/")
def root():
    return {"status": "ok", "info": "IFE DO-160 Test Simulatoru API v2"}


@app.post("/run")
def run_sim(params: SimRequest):
    params.replications = max(1, min(params.replications, 500))

    times        = []
    fail_total   = {"ENV": 0, "EMC": 0, "MECH": 0, "REVIEW": 0, "FINAL": 0}
    fail_per_rep = []

    for _ in range(params.replications):
        t, fails = run_replication(params)
        times.append(t)
        rep_row = {"ENV": 0, "EMC": 0, "MECH": 0, "REVIEW": 0, "FINAL": 0}
        for f in fails:
            g = f["group"]
            fail_total[g] += 1
            rep_row[g]    += 1
        fail_per_rep.append(rep_row)

    times_arr = np.array(times)
    n = params.replications

    fail_rep_rates = {}
    for g in fail_total:
        count = sum(1 for r in fail_per_rep if r[g] > 0)
        fail_rep_rates[g] = round(count / n * 100, 1)

    p5, p25, p50, p75, p95 = np.percentile(times_arr, [5, 25, 50, 75, 95])

    return {
        "mean_time":      round(float(times_arr.mean()), 2),
        "std_time":       round(float(times_arr.std()),  2),
        "min_time":       round(float(times_arr.min()),  2),
        "max_time":       round(float(times_arr.max()),  2),
        "p5":             round(float(p5),  2),
        "p25":            round(float(p25), 2),
        "p50":            round(float(p50), 2),
        "p75":            round(float(p75), 2),
        "p95":            round(float(p95), 2),
        "replications":   n,
        "times":          [round(t, 2) for t in times],
        "fail_counts":    fail_total,
        "fail_rep_rates": fail_rep_rates,
        "fail_per_rep":   fail_per_rep,
    }
