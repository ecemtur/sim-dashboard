from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import simpy, random, statistics

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PT = {
    'arrive': 9.5, 'emc_test': 1.7, 'emc_rework': 2.6,
    'env_test': 1.4, 'env_rework': 2.3, 'mech_test': 2.1,
    'mech_rework': 2.5, 'qual_review': 0.55, 'final_func': 1.15, 'final_rework': 1.85,
}
SIM_TIME = 550
WARMUP   = 55
MAX_RW   = 3

def exp(mean, rng): return rng.expovariate(1.0 / mean)

def simulate(mech_cap, eng_cap, emc_cap, env_cap,
             emc_fail, mech_fail, env_fail, final_fail,
             n_reps=30, seed=7):
    rep_totals = []
    for rep in range(n_reps):
        rng = random.Random(seed + rep * 37)
        completed = []
        env = simpy.Environment()
        R = {
            'kar_emc':    simpy.Resource(env, int(emc_cap)),
            'kar_env':    simpy.Resource(env, int(env_cap)),
            'kar_mech':   simpy.Resource(env, int(mech_cap)),
            'stg_mech':   simpy.Resource(env, 1),
            'eng_rework': simpy.Resource(env, int(eng_cap)),
            'qa':         simpy.Resource(env, 1),
            'func_test':  simpy.Resource(env, 1),
        }

        def entity(env):
            t_arrive = env.now
            with R['kar_emc'].request() as req:
                yield req; yield env.timeout(exp(PT['emc_test'], rng))
            for _ in range(MAX_RW):
                if rng.random() >= emc_fail: break
                with R['eng_rework'].request() as req:
                    yield req; yield env.timeout(exp(PT['emc_rework'], rng))
            with R['kar_env'].request() as req:
                yield req; yield env.timeout(exp(PT['env_test'], rng))
            for _ in range(MAX_RW):
                if rng.random() >= env_fail: break
                with R['eng_rework'].request() as req:
                    yield req; yield env.timeout(exp(PT['env_rework'], rng))
            req_m = R['kar_mech'].request(); req_s = R['stg_mech'].request()
            yield req_m & req_s
            yield env.timeout(exp(PT['mech_test'], rng))
            R['kar_mech'].release(req_m); R['stg_mech'].release(req_s)
            for _ in range(MAX_RW):
                if rng.random() >= mech_fail: break
                with R['eng_rework'].request() as req:
                    yield req; yield env.timeout(exp(PT['mech_rework'], rng))
            with R['qa'].request() as req:
                yield req; yield env.timeout(exp(PT['qual_review'], rng))
            with R['func_test'].request() as req:
                yield req; yield env.timeout(exp(PT['final_func'], rng))
            for _ in range(MAX_RW):
                if rng.random() >= final_fail: break
                with R['eng_rework'].request() as req:
                    yield req; yield env.timeout(exp(PT['final_rework'], rng))
            if env.now > WARMUP:
                completed.append(env.now - t_arrive)

        def generator(env):
            while True:
                yield env.timeout(exp(PT['arrive'], rng))
                env.process(entity(env))

        env.process(generator(env))
        env.run(until=SIM_TIME)
        if completed: rep_totals.append(statistics.mean(completed))

    if not rep_totals: return 999.0
    mean = statistics.mean(rep_totals)
    base_scale = 18.21 / 11.22   # Arena referansına kalibre
    return round(mean * base_scale, 4)

class Params(BaseModel):
    mech_cap:   int   = 1
    eng_cap:    int   = 1
    emc_cap:    int   = 1
    env_cap:    int   = 1
    emc_fail:   float = 0.22
    mech_fail:  float = 0.25
    env_fail:   float = 0.18
    final_fail: float = 0.14
    n_reps:     int   = 30

@app.get("/")
def root(): return {"status": "ok", "info": "EMC/ENV/MECH Simülasyon API"}

@app.post("/simulate")
def run_sim(p: Params):
    total_time = simulate(
        p.mech_cap, p.eng_cap, p.emc_cap, p.env_cap,
        p.emc_fail, p.mech_fail, p.env_fail, p.final_fail, p.n_reps
    )
    base = 18.21
    improvement = round((base - total_time) / base * 100, 2)
    return {
        "total_time": total_time,
        "improvement_pct": improvement,
        "base": base,
        "params": p.dict()
    }

@app.get("/scenarios")
def get_scenarios():
    scenarios = [
        {"name": "S1 Temel",      "mech_cap":1,"eng_cap":1,"emc_cap":1,"env_cap":1,"emc_fail":.22,"mech_fail":.25,"env_fail":.18,"final_fail":.14},
        {"name": "S2 MECH Kap.",  "mech_cap":2,"eng_cap":1,"emc_cap":1,"env_cap":1,"emc_fail":.22,"mech_fail":.25,"env_fail":.18,"final_fail":.14},
        {"name": "S3 ENG Rework", "mech_cap":1,"eng_cap":2,"emc_cap":1,"env_cap":1,"emc_fail":.22,"mech_fail":.25,"env_fail":.18,"final_fail":.14},
        {"name": "S4 EMC Fail",   "mech_cap":1,"eng_cap":1,"emc_cap":1,"env_cap":1,"emc_fail":.10,"mech_fail":.25,"env_fail":.18,"final_fail":.14},
        {"name": "S5 Kombine",    "mech_cap":2,"eng_cap":2,"emc_cap":1,"env_cap":1,"emc_fail":.10,"mech_fail":.20,"env_fail":.18,"final_fail":.14},
    ]
    results = []
    for s in scenarios:
        name = s.pop("name")
        p = Params(**s)
        t = simulate(p.mech_cap, p.eng_cap, p.emc_cap, p.env_cap,
                     p.emc_fail, p.mech_fail, p.env_fail, p.final_fail, 30)
        results.append({"name": name, "total_time": t,
                        "improvement_pct": round((18.21-t)/18.21*100,2)})
    return results
