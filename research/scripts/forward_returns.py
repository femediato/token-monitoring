"""Forward-return study (docs/RESEARCH.md, round 2): what happens after a token rises or
falls, across every tracked token, independent of any strategy.

Export the hourly evaluations, then run (stdlib only):

    docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d autolp_dev -c "\\copy (
      SELECT token_mint, evaluated_at, eligible, critical,
        (features->>'"'"'price_usd'"'"')::float, (features->>'"'"'ret_1h_pct'"'"')::float,
        (features->>'"'"'ret_4h_pct'"'"')::float,
        (features->>'"'"'dist_from_4h_high_pct'"'"')::float,
        (features->>'"'"'sell_cost_pct'"'"')::float, (features->>'"'"'market_cap'"'"')::float,
        (features->>'"'"'liquidity_usd'"'"')::float, (features->>'"'"'buy_share_1h'"'"')::float,
        (features->>'"'"'relvol_ratio'"'"')::float, total
      FROM score_evaluations WHERE hourly ORDER BY 1, 2) TO stdout csv"' > hourly.csv
    python3 research/scripts/forward_returns.py hourly.csv

Population: the backtest's baseline candidates (no critical failure, market cap >= $300k,
liquidity >= $50k). Forward return = price of the same token's hourly evaluation h hours
later; a missing later evaluation is counted as missing (not as a return), so dead tokens
bias the results upward. Returns are clipped to [-100, +200] %. Hourly observations of a
24h horizon overlap, so the effective sample is far smaller than n; "days+" counts the
days whose mean was positive, a cruder but more honest measure of consistency.
"""
import csv, sys, statistics as st
from datetime import datetime, timedelta
from collections import defaultdict
rows=[]; price={}
def f(x): return float(x) if x not in ("",None) else None
for r in csv.reader(open(sys.argv[1])):
    m,at,el,cr,p,r1,r4,dh,cost,mc,liq,bs,rv,tot=r
    t=datetime.fromisoformat(at).replace(minute=0,second=0)
    d=dict(m=m,t=t,el=el=="t",cr=cr=="t",p=f(p),r1=f(r1),r4=f(r4),dh=f(dh),cost=f(cost),mc=f(mc),liq=f(liq),bs=f(bs),rv=f(rv))
    rows.append(d); price[(m,t)]=d["p"]
end=max(r["t"] for r in rows)
def fwd(d,h):
    if d["t"]+timedelta(hours=h)>end: return "na"
    p1=price.get((d["m"],d["t"]+timedelta(hours=h)))
    if not d["p"] or p1 is None: return None
    return (p1/d["p"]-1)*100
cand=[d for d in rows if not d["cr"] and (d["mc"] or 0)>=3e5 and (d["liq"] or 0)>=5e4]
print("candidates",len(cand),"eligible",sum(d["el"] for d in cand), "window", min(r["t"] for r in rows), end)
def summ(name, ds, h):
    xs=[];miss=0;days=defaultdict(list)
    for d in ds:
        x=fwd(d,h)
        if x=="na": continue
        if x is None: miss+=1; continue
        x=max(min(x,200),-100); xs.append(x); days[d["t"].date()].append(x)
    if len(xs)<30: print(f"  {name:28s} {h:>2}h n={len(xs)}"); return
    dm=[st.mean(v) for v in days.values() if len(v)>=5]
    print(f"  {name:28s} {h:>2}h n={len(xs):6d} miss={miss/(miss+len(xs))*100:4.1f}% mean={st.mean(xs):+6.2f} med={st.median(xs):+6.2f} pos={sum(x>0 for x in xs)/len(xs)*100:4.1f}% days+={sum(v>0 for v in dm)}/{len(dm)}")
for h in (4,24):
    print(f"== {h}h forward")
    summ("all candidates", cand, h)
    summ("eligible", [d for d in cand if d["el"]], h)
    summ("not eligible", [d for d in cand if not d["el"]], h)
    for pop,ds in (("cand",cand),("elig",[d for d in cand if d["el"]])):
        for lo,hi in ((-999,-30),(-30,-15),(-15,-5),(-5,0),(0,5),(5,15),(15,999)):
            summ(f"{pop} ret4h [{lo},{hi})", [d for d in ds if d["r4"] is not None and lo<=d["r4"]<hi], h)
print("== reversal proxies (candidates)")
for h in (1,4,24):
    summ("4h<-15 & 1h>+2 (turning)", [d for d in cand if d["r4"] is not None and d["r1"] is not None and d["r4"]<-15 and d["r1"]>2], h)
    summ("4h<-15 & 1h<0 (still falling)", [d for d in cand if d["r4"] is not None and d["r1"] is not None and d["r4"]<-15 and d["r1"]<0], h)
    summ("4h<-15 & buyshare>0.55", [d for d in cand if d["r4"] is not None and d["bs"] is not None and d["r4"]<-15 and d["bs"]>0.55], h)
    summ("1h<-10", [d for d in cand if d["r1"] is not None and d["r1"]<-10], h)
    summ("1h>+10", [d for d in cand if d["r1"] is not None and d["r1"]>10], h)
    summ("4h in [-15,-5) liq>=150k", [d for d in cand if d["r4"] is not None and -15<=d["r4"]<-5 and d["liq"]>=1.5e5], h)
