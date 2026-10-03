import os, math, datetime as dt
import requests
import streamlit as st

st.set_page_config(page_title="توقعات كرة القدم", page_icon="⚽", layout="wide")
st.markdown(
    "<style>html,body,[data-testid='stAppViewContainer'],[data-testid='stSidebar']"
    "{direction:rtl;text-align:right}</style>",
    unsafe_allow_html=True,
)

API = "https://api.football-data.org/v4"
LEAGUES = {
    "الدوري الإنجليزي": "PL", "الدوري الإسباني": "PD", "الدوري الإيطالي": "SA",
    "الدوري الألماني": "BL1", "الدوري الفرنسي": "FL1", "دوري أبطال أوروبا": "CL",
    "الدوري الهولندي": "DED", "الدوري البرتغالي": "PPL",
}


def token():
    t = os.environ.get("FOOTBALL_DATA_API_TOKEN")
    if not t:
        try:
            t = st.secrets["FOOTBALL_DATA_API_TOKEN"]
        except Exception:
            t = None
    return t


def _fetch(path):
    r = requests.get(API + path, headers={"X-Auth-Token": token()}, timeout=20)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=60)
def fast(path):
    return _fetch(path)


@st.cache_data(ttl=3600)
def slow(path):
    return _fetch(path)


def tables(std):
    t = {}
    for s in std["standings"]:
        t.setdefault(s["type"], []).extend(s["table"])
    H = {r["team"]["id"]: r for r in t.get("HOME", [])}
    A = {r["team"]["id"]: r for r in t.get("AWAY", [])}
    return H, A


def league_avg(rows, default):
    p = sum(r["playedGames"] for r in rows.values())
    return sum(r["goalsFor"] for r in rows.values()) / p if p else default


def strength(row, key, avg):
    if not row or not row["playedGames"]:
        return 1.0
    p = row["playedGames"]
    w = p / (p + 5)  # تقليص النتائج عندما تكون العينة صغيرة
    return w * ((row[key] / p) / avg) + (1 - w)


def lambdas(H, A, hid, aid):
    avg_h, avg_a = league_avg(H, 1.45), league_avg(A, 1.15)
    h, a = H.get(hid), A.get(aid)
    lh = avg_h * strength(h, "goalsFor", avg_h) * strength(a, "goalsAgainst", avg_h)
    la = avg_a * strength(a, "goalsFor", avg_a) * strength(h, "goalsAgainst", avg_a)
    return lh, la


def pois(l, k):
    return math.exp(-l) * l ** k / math.factorial(k)


def probs(lh, la, gh=0, ga=0, n=10):
    r = dict(h=0, d=0, a=0, o25=0, btts=0, best=(0, (0, 0)))
    for i in range(n + 1):
        for j in range(n + 1):
            p = pois(lh, i) * pois(la, j)
            H, A = gh + i, ga + j
            r["h" if H > A else "a" if A > H else "d"] += p
            if H + A > 2:
                r["o25"] += p
            if H > 0 and A > 0:
                r["btts"] += p
            if p > r["best"][0]:
                r["best"] = (p, (H, A))
    return r


def remaining_fraction(m):
    if m["status"] == "PAUSED":
        return 0.5
    kick = dt.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
    el = (dt.datetime.now(dt.timezone.utc) - kick).total_seconds() / 60
    if el > 60:
        el -= 15
    return max(0.02, (90 - min(max(el, 0), 90)) / 90)


def pct(x):
    return f"{x * 100:.0f}%"


@st.fragment(run_every=60)
def panel(code, mid):
    m = next((x for x in fast(f"/competitions/{code}/matches?dateFrom={dt.date.today()}"
                              f"&dateTo={dt.date.today() + dt.timedelta(days=7)}")["matches"]
              if x["id"] == mid), None)
    if not m:
        st.warning("لم يعد هذا اللقاء متاحاً في القائمة.")
        return
    H, A = tables(slow(f"/competitions/{code}/standings"))
    hid, aid = m["homeTeam"]["id"], m["awayTeam"]["id"]
    lh, la = lambdas(H, A, hid, aid)
    live = m["status"] in ("IN_PLAY", "PAUSED")
    gh = ga = 0
    f = 1.0
    if live:
        gh = m["score"]["fullTime"].get("home") or 0
        ga = m["score"]["fullTime"].get("away") or 0
        f = remaining_fraction(m)
        st.error(f"🔴 مباشر الآن: {m['homeTeam']['name']} {gh} - {ga} {m['awayTeam']['name']}")
    p = probs(lh * f, la * f, gh, ga)
    st.subheader(f"{m['homeTeam']['name']} 🆚 {m['awayTeam']['name']}")
    c1, c2, c3 = st.columns(3)
    c1.metric(f"فوز {m['homeTeam']['name']}", pct(p["h"]), f"سعر عادل {1 / max(p['h'], 1e-9):.2f}")
    c2.metric("تعادل", pct(p["d"]), f"سعر عادل {1 / max(p['d'], 1e-9):.2f}")
    c3.metric(f"فوز {m['awayTeam']['name']}", pct(p["a"]), f"سعر عادل {1 / max(p['a'], 1e-9):.2f}")
    c4, c5, c6 = st.columns(3)
    c4.metric("أكثر نتيجة متوقعة", f"{p['best'][1][0]} - {p['best'][1][1]}")
    c5.metric("أكثر من 2.5 هدف", pct(p["o25"]))
    c6.metric("الفريقان يسجلان", pct(p["btts"]))

    st.markdown("### ⚽ الأكثر ترشيحاً للتسجيل")
    try:
        sc = slow(f"/competitions/{code}/scorers?limit=100")["scorers"]
        rows = []
        for s in sc:
            tid = s["team"]["id"]
            if tid not in (hid, aid) or not s.get("playedMatches"):
                continue
            lam, row = (lh, H.get(hid)) if tid == hid else (la, A.get(aid))
            team_avg = (row["goalsFor"] / row["playedGames"]) if row and row["playedGames"] else lam
            g_pg = s["goals"] / s["playedMatches"]
            prob = 1 - math.exp(-g_pg * (lam / max(team_avg, 0.3)) * f)
            rows.append((prob, s["player"]["name"], s["team"]["name"], s["goals"]))
        rows.sort(reverse=True)
        if rows:
            st.table([{"اللاعب": r[1], "الفريق": r[2], "أهدافه": r[3], "احتمال التسجيل": pct(r[0])}
                      for r in rows[:6]])
        else:
            st.info("لا يوجد هدافون من الفريقين ضمن أفضل 100 هداف في الدوري.")
    except Exception:
        st.info("بيانات الهدافين غير متاحة الآن.")
    st.caption("التقدير يفترض أن اللاعب سيلعب. التشكيلة وبطاقات الطرد غير متاحة في الخطة المجانية.")

    with st.sidebar:
        st.markdown("### 💰 مقارنة مع أسعار شركة الرهان")
        st.caption("أدخل الأسعار وسيخبرك التطبيق هل فيها قيمة فعلية.")
        for label, key, pr in (("فوز المضيف", "oh", p["h"]), ("تعادل", "od", p["d"]),
                               ("فوز الضيف", "oa", p["a"])):
            odds = st.number_input(f"سعر {label}", min_value=0.0, value=0.0, step=0.05, key=f"{key}{mid}")
            if odds > 1:
                edge = pr * odds - 1
                st.write(("✅ فيها قيمة" if edge > 0.05 else "❌ لا قيمة") + f" ({edge * 100:+.0f}%)")


st.title("⚽ توقعات المباريات")
if not token():
    st.error("لم يتم ضبط المفتاح FOOTBALL_DATA_API_TOKEN في الأسرار (Secrets).")
    st.stop()

league = st.sidebar.selectbox("الدوري", list(LEAGUES))
code = LEAGUES[league]
try:
    data = fast(f"/competitions/{code}/matches?dateFrom={dt.date.today()}"
                f"&dateTo={dt.date.today() + dt.timedelta(days=7)}")["matches"]
except Exception as e:
    st.error(f"تعذر جلب المباريات: {e}")
    st.stop()
if not data:
    st.info("لا توجد مباريات خلال الأيام السبعة القادمة في هذا الدوري.")
    st.stop()

labels = {m["id"]: f"{m['utcDate'][:16].replace('T', ' ')} UTC | {m['homeTeam']['name']} - {m['awayTeam']['name']}"
          for m in data}
mid = st.selectbox("اختر المباراة", list(labels), format_func=lambda i: labels[i])
panel(code, mid)
st.warning("هذه احتمالات إحصائية وليست ضماناً. أسعار شركات الرهان تتضمن هذه المعلومات أصلاً، "
           "ولا تراهن بمال لا تتحمل خسارته.")
