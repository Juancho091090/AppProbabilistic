"""Prueba end-to-end del pipeline diario contra PostgreSQL real.

Las APIs externas se simulan con ``httpx.MockTransport`` usando EXACTAMENTE la
estructura de respuesta verificada con las APIs reales (docs/data-sources.md). Claude,
Telegram y SMTP también se simulan. Se ejecutan dos días seguidos:

* Día 1: ingesta de histórico, predicción de los partidos del día, informe y envío.
* Día 2: los partidos del día 1 ya terminaron → se registran resultados y métricas.
"""

import json
import os
import re
import shutil
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from sports_analytics.config.settings import Settings
from sports_analytics.data.clients.api_football import ApiFootballClient
from sports_analytics.data.clients.tennis_api import TennisApiClient
from sports_analytics.db.models import (
    DataSource,
    FootballMatchRow,
    FootballStatistics,
    MarketOdds,
    MarketOddsCheck,
    ModelMetric,
    PipelineRun,
    PredictionResult,
    PredictionRow,
    Rating,
    TennisMatchRow,
)
from sports_analytics.db.session import session_scope
from sports_analytics.pipeline.daily import Services, run_daily
from sports_analytics.reporting.claude_narrator import ClaudeNarrator
from sports_analytics.reporting.email import EmailService
from sports_analytics.reporting.telegram import TelegramService
from tests.synthetic import football_league, tennis_tour

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="sin TEST_DATABASE_URL")]

DAY1 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)  # 07:00 en Bogotá
DAY2 = DAY1 + timedelta(days=1)


# ----------------------------------------------------------- API-Football falsa


class FakeFootballApi:
    def __init__(self):
        hist, _ = football_league(n_teams=12, rounds=4, start=DAY1 - timedelta(days=120))
        self.items = [
            self._item(
                m.match_id,
                m.kickoff_utc,
                m.home_team,
                m.away_team,
                "FT",
                m.home_goals,
                m.away_goals,
            )
            for m in hist
        ]
        # Como en la API real, algunos partidos no tienen estadísticas (1 de cada 10)
        self.stats = {
            m.match_id: (None, None) if i % 10 == 0 else (m.home_corners, m.away_corners)
            for i, m in enumerate(hist)
        }
        ko = DAY1.replace(hour=23)  # 18:00 hora local
        self.today = [
            self._item("up1", ko, "Team01", "Team02", "NS", None, None),
            self._item("up2", ko, "Team03", "Team04", "NS", None, None),
        ]
        self.other = self._item("x9", ko, "Foo", "Bar", "NS", None, None, league=40)
        # Benchmark de mercado: up1 con Pinnacle (preferida) y Bet365; up2 solo Bet365
        self.odds = {
            "up1": [
                _bookmaker(4, "Pinnacle", "1.80", "3.70", "4.60"),
                _bookmaker(8, "Bet365", "1.75", "3.60", "4.50"),
            ],
            "up2": [_bookmaker(8, "Bet365", "2.50", "3.10", "2.90")],
        }
        self.calls: list[str] = []

    @staticmethod
    def _item(fid, ko, home, away, status, hg, ag, league=39):
        tid = lambda name: int(re.sub(r"\D", "", name) or 0) + 100  # noqa: E731
        return {
            "fixture": {"id": fid, "date": ko.isoformat(), "status": {"short": status}},
            "league": {
                "id": league,
                "name": "Premier League" if league == 39 else "Championship",
                "season": 2024,
            },
            "teams": {
                "home": {"id": tid(home), "name": home},
                "away": {"id": tid(away), "name": away},
            },
            "goals": {"home": hg, "away": ag},
            "score": {"fulltime": {"home": hg, "away": ag}},
        }

    def finish_today(self):
        for it, (hg, ag) in zip(self.today, [(2, 0), (1, 1)], strict=True):
            it["fixture"]["status"]["short"] = "FT"
            it["goals"] = {"home": hg, "away": ag}
            it["score"] = {"fulltime": {"home": hg, "away": ag}}
            self.stats[it["fixture"]["id"]] = (6, 4)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, request.url.params
        self.calls.append(path)
        assert request.headers["x-apisports-key"] == "football-test-key"
        if path == "/leagues":
            resp = [
                {
                    "league": {"id": 39, "name": "Premier League"},
                    "country": {"name": "England"},
                    "seasons": [
                        {
                            "year": 2024,
                            "current": True,
                            "start": "2023-08-01",
                            "end": "2024-12-31",
                            "coverage": {"fixtures": {"events": True, "statistics_fixtures": True}},
                        }
                    ],
                }
            ]
        elif path == "/fixtures" and "date" in q:
            day = q["date"]
            resp = [it for it in self.today + [self.other] if it["fixture"]["date"].startswith(day)]
        elif path == "/fixtures":
            resp = self.items + self.today if q["season"] == "2024" else []
        elif path == "/fixtures/statistics":
            hc, ac = self.stats.get(q["fixture"], (None, None))
            resp = (
                []
                if hc is None
                else [
                    {
                        "statistics": [
                            {"type": "Corner Kicks", "value": hc},
                            {"type": "Total Shots", "value": 10},
                        ]
                    },
                    {
                        "statistics": [
                            {"type": "Corner Kicks", "value": ac},
                            {"type": "Total Shots", "value": 7},
                        ]
                    },
                ]
            )
        elif path == "/odds":
            assert q["bet"] == "1"
            resp = [
                {"update": "2024-06-01T10:00:00+00:00", "bookmakers": bks}
                for bks in [self.odds.get(q["fixture"])]
                if bks
            ]
        else:
            return httpx.Response(404)
        return httpx.Response(200, json={"errors": [], "response": resp})


def _bookmaker(bid, name, h, d, a):
    vals = [{"value": "Home", "odd": h}, {"value": "Draw", "odd": d}, {"value": "Away", "odd": a}]
    return {"id": bid, "name": name, "bets": [{"id": 1, "name": "Match Winner", "values": vals}]}


# ------------------------------------------------------------- Tennis API falsa


class FakeTennisApi:
    def __init__(self):
        hist, self.skill = tennis_tour(
            n_players=20, n_matches=1400, start=DAY1 - timedelta(hours=6 * 1400 + 48)
        )
        self.results = [self._result(m) for m in hist]
        self.results.append(self._result(hist[0], challenger=True))  # debe filtrarse
        best = sorted(self.skill, key=self.skill.get)
        ko = DAY1.replace(hour=20).isoformat().replace("+00:00", ".000Z")
        self.fixtures = [
            {
                "id": 1,
                "matchId": 1,
                "date": ko,
                "startTime": ko,
                "roundId": 4,
                "tournamentId": 900,
                "player1Id": self.pid(best[-1]),
                "player2Id": self.pid(best[0]),
                "player1": {"id": self.pid(best[-1]), "name": f"Player {best[-1]}"},
                "player2": {"id": self.pid(best[0]), "name": f"Player {best[0]}"},
            },
        ]

    @staticmethod
    def pid(name):
        return 1000 + int(name[1:])

    def _result(self, m, challenger=False):
        a, b = self.pid(m.player_a), self.pid(m.player_b)
        score = "6-4 6-3" if m.winner == "A" else "4-6 3-6"
        return {
            "id": m.match_id + ("c" if challenger else ""),
            "matchId": m.match_id,
            "date": m.kickoff_utc.isoformat().replace("+00:00", ".000Z"),
            "player1Id": a,
            "player2Id": b,
            "tournamentId": 22 if challenger else 500,
            "match_winner": a if m.winner == "A" else b,
            "result": score,
            "result_type": "completed",
            "player1": {"id": a, "name": f"Player {m.player_a}"},
            "player2": {"id": b, "name": f"Player {m.player_b}"},
            "tournament": {
                "id": 22 if challenger else 500,
                "name": "Lima Challenger" if challenger else "Synthetic Open",
                "courtId": 1,
                "rankId": 1 if challenger else 2,
            },
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers["X-RapidAPI-Key"] == "tennis-test-key"
        if "/wta/" in path:
            return httpx.Response(200, json={"data": [], "hasNextPage": False})
        m = re.match(r".*/atp/results/(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})$", path)
        if m:
            lo = datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
            hi = datetime.fromisoformat(m.group(2)).replace(tzinfo=UTC) + timedelta(days=1)
            data = [
                r
                for r in self.results
                if lo <= datetime.fromisoformat(r["date"].replace("Z", "+00:00")) < hi
            ]
        elif "/atp/fixtures/" in path:
            data = self.fixtures if path.endswith(DAY1.date().isoformat()) else []
        elif "/atp/tournament/calendar/" in path:
            data = [
                {
                    "id": 900,
                    "name": "Clay Masters",
                    "courtId": 2,
                    "rankId": 3,
                    "tier": "1000",
                    "date": "2024-05-28T00:00:00.000Z",
                    "court": {"id": 2, "name": "Clay"},
                }
            ]
        elif path.endswith("/atp/ranking/singles"):
            data = [
                {"position": i + 1, "player": {"id": self.pid(p)}}
                for i, p in enumerate(sorted(self.skill, key=self.skill.get, reverse=True))
            ]
        else:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data, "hasNextPage": False})


# ----------------------------------------------------- Claude / Telegram / SMTP


class FakeClaudeClient:
    """Devuelve una narrativa válida construida con los números recibidos."""

    def __init__(self):
        self.messages = self
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        data = json.loads(kwargs["messages"][0]["content"])
        matches = {
            m["event_id"]: [f"Probabilidad principal calculada: {next(iter(m['pct'].values()))}%."]
            for m in data["matches"]
        }
        text = json.dumps({"summary": "Resumen de la jornada.", "matches": matches})
        from types import SimpleNamespace

        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture(scope="module")
def env(engine, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    settings = Settings(
        database_url=URL,
        cache_dir=str(tmp / "cache"),
        api_football_key="football-test-key",
        api_football_daily_limit=5000,
        tennis_api_key="tennis-test-key",
        tennis_api_daily_limit=500,
        football_history_seasons=1,
        football_stats_per_run=600,
        tennis_history_days=400,
        tennis_window_days=60,
        tennis_backfill_calls_per_run=20,
        anthropic_api_key="sk-ant-test-0000000000",
        telegram_bot_token="123456789:ABCdefGhIJKlmNoPQRstuVWxyz0123456789",
        telegram_chat_id="7",
        smtp_host="smtp.test",
        email_from="bot@x.com",
        email_to="me@x.com",
    )
    fb, tn, tg_msgs = FakeFootballApi(), FakeTennisApi(), []

    def tg_handler(request):
        tg_msgs.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    def services():
        return Services(
            football=ApiFootballClient(
                settings, transport=httpx.MockTransport(fb), sleep=lambda s: None
            ),
            tennis=TennisApiClient(
                settings, transport=httpx.MockTransport(tn), sleep=lambda s: None, min_interval=0
            ),
            narrator=ClaudeNarrator(settings, client=FakeClaudeClient()),
            telegram=TelegramService(settings, transport=httpx.MockTransport(tg_handler)),
            email=EmailService(settings, smtp_factory=FakeSMTP),
        )

    return settings, fb, tn, tg_msgs, services


def test_day1_full_pipeline(env, app_config):
    settings, fb, tn, tg_msgs, services = env
    out = run_daily(settings, app_config, services(), now=DAY1)

    assert out.status in ("success", "partial")
    md = out.markdown
    # Fútbol: solo Premier (la liga 40 no está autorizada) y 2 partidos
    assert "## Premier League" in md and "Team01 vs Team02" in md and "Championship" not in md
    assert out.payload.n_football == 2
    # Córners garantizados para los partidos del día gracias a la carga prioritaria
    assert all(f.markets["corners"] is not None for f in out.payload.football[0].forecasts)
    # Tenis: 1 partido del circuito principal, superficie desde el calendario (arcilla)
    assert out.payload.n_tennis("ATP") == 1 and "Superficie: clay" in md
    # Narrativa de Claude validada e insertada; probabilidades intactas
    assert "Resumen de la jornada." in md
    f = out.payload.football[0].forecasts[0]
    assert f"{f.markets['1x2']['home'] * 100:.1f}%" in md
    # Envíos
    assert out.telegram_sent and tg_msgs and all(m["parse_mode"] == "HTML" for m in tg_msgs)
    assert out.email_sent and FakeSMTP.sent
    mail = FakeSMTP.sent[-1]
    assert [a.get_filename() for a in mail.iter_attachments()] == ["informe_2024-06-01.pdf"]
    assert out.pdf and out.pdf.startswith(b"%PDF")
    # Persistencia
    with session_scope(URL) as s:
        assert s.scalar(select(func.count()).select_from(FootballMatchRow)) >= 264
        assert s.scalar(select(func.count()).select_from(FootballStatistics)) > 0
        n_tennis = s.scalar(select(func.count()).select_from(TennisMatchRow))
        assert n_tennis > 500
        assert (
            s.scalar(
                select(func.count())
                .select_from(TennisMatchRow)
                .where(TennisMatchRow.tournament == "Lima Challenger")
            )
            == 0
        )  # Challenger filtrado
        preds = s.scalars(select(PredictionRow)).all()
        assert {p.sport for p in preds} == {"football", "tennis"}
        assert all(0 <= p.probability <= 1 for p in preds)
        assert s.scalar(select(func.count()).select_from(Rating)) > 0
        odds = s.scalars(select(MarketOdds)).all()
        assert sorted(o.bookmaker_id for o in odds) == [4, 8, 8]
        checks = s.scalars(select(MarketOddsCheck)).all()
        assert len(checks) == 2 and not any(c.final for c in checks)  # aún no empiezan
    # El mercado es solo para evaluar: nunca aparece en el informe ni en el correo
    assert "Pinnacle" not in md and "Bet365" not in md and "1.80" not in md
    with session_scope(URL) as s:
        run = s.get(PipelineRun, out.run_id)
        assert run.status == out.status and run.predictions_generated == len(preds)
        assert run.telegram_sent and run.email_sent and run.report_markdown
        assert run.details["loaded"].get("football:statistics_priority", 0) > 0


def test_day1_rerun_is_idempotent(env, app_config):
    settings, *_, services = env
    with session_scope(URL) as s:
        before = s.scalar(select(func.count()).select_from(PredictionRow))
    run_daily(settings, app_config, services(), now=DAY1 + timedelta(minutes=5), send=False)
    with session_scope(URL) as s:
        assert s.scalar(select(func.count()).select_from(PredictionRow)) == before


def test_day2_settles_results(env, app_config):
    settings, fb, *_rest, services = env
    fb.finish_today()
    # Un día después la caché HTTP (TTL de horas) ya expiró: se simula vaciándola
    shutil.rmtree(settings.cache_dir, ignore_errors=True)
    run_daily(settings, app_config, services(), now=DAY2, send=False)
    with session_scope(URL) as s:
        results = s.execute(
            select(PredictionRow, PredictionResult)
            .join(PredictionResult, PredictionResult.prediction_id == PredictionRow.id)
            .where(
                PredictionRow.sport == "football",
                PredictionRow.event_id == "up1",
                PredictionRow.market == "1x2",
                PredictionRow.model == "ensemble_v1",
            )
        ).all()
        by_event = {p.event: r.outcome for p, r in results}
        assert by_event == {"home_win": 1, "draw": 0, "away_win": 0}  # up1 terminó 2-0
        corners = s.execute(
            select(PredictionRow.line, PredictionResult.outcome, PredictionResult.actual_value)
            .join(PredictionResult, PredictionResult.prediction_id == PredictionRow.id)
            .where(PredictionRow.market == "corners_total", PredictionRow.event_id == "up1")
        ).all()
        assert corners and all(v == 10.0 for _, _, v in corners)
        assert all(o == int(line < 10) for line, o, _ in corners)
        # Benchmark de mercado: reconsulta final tras el inicio y métricas vivas comparables
        assert all(c.final for c in s.scalars(select(MarketOddsCheck)))
        metrics = {
            m.model: m
            for m in s.scalars(
                select(ModelMetric).where(
                    ModelMetric.segment_type == "global", ModelMetric.market == "1x2"
                )
            )
        }
        assert metrics["mercado"].n == metrics["ensemble_v1@mercado"].n == 6  # 2 partidos × 3
    from sports_analytics.pipeline.results import live_market_pairs

    with session_scope(URL) as s:
        pairs = {
            p.match_label: p for p in live_market_pairs(s, app_config.models.market, "ensemble_v1")
        }
    assert set(pairs) == {"up1", "up2"} and pairs["up1"].outcome == 0 and pairs["up2"].outcome == 1
    assert pairs["up1"].market[0] == pytest.approx((1 / 1.80) / (1 / 1.80 + 1 / 3.70 + 1 / 4.60))


def test_weekly_recalibration_fit_and_load(env, app_config):
    from sports_analytics.pipeline.recalibration import (
        PROVIDER,
        fit_recalibration,
        load_recalibrator,
    )

    settings, *_ = env
    cal = dict(app_config.models.calibration)
    cal["recalibration_1x2"] = {**cal["recalibration_1x2"], "window_days": 120, "min_samples": 50}
    cfg = app_config.model_copy(
        update={"models": app_config.models.model_copy(update={"calibration": cal})}
    )
    with session_scope(URL) as s:
        res = fit_recalibration(s, settings, cfg, now=DAY2)
        s.commit()
        assert res.n >= 50 and res.status in ("applied", "rejected")
        assert set(res.params) == {"a", "b_home", "b_away", "n_train"}
        assert res.holdout["n_test"] > 0 and res.holdout["favorite"]
        md = res.to_markdown()
        assert "Validación fuera de muestra" in md and "Calibración del favorito" in md
        row = s.scalar(select(DataSource).where(DataSource.provider == PROVIDER))
        assert row.details["status"] == res.status
        loaded = load_recalibrator(s, cfg, DAY2 + timedelta(hours=1))
        assert (loaded is not None) == (res.status == "applied")
        assert load_recalibrator(s, cfg, DAY2 + timedelta(days=40)) is None  # caducado
        assert load_recalibrator(s, cfg, DAY2 - timedelta(days=1)) is None  # futuro: no


def test_pipeline_survives_api_outage(env, app_config):
    settings, *_ = env

    def broken(request):
        return httpx.Response(503)

    svc = Services(
        football=ApiFootballClient(
            settings.model_copy(update={"http_max_retries": 0}),
            transport=httpx.MockTransport(broken),
            sleep=lambda s: None,
        ),
        tennis=None,
        narrator=None,
        telegram=None,
        email=None,
    )
    out = run_daily(settings, app_config, svc, now=DAY2 + timedelta(days=1), send=False)
    assert out.status == "partial"
    assert any("football" in i.lower() or "fútbol" in i.lower() for i in out.issues)
    assert "# INFORME DEPORTIVO" in out.markdown


def test_scheduled_run_does_not_resend_same_day(env, app_config):
    settings, *_rest, services = env
    # Día 1 ya se envió en test_day1_full_pipeline: una corrida programada no debe reenviar
    s2 = settings.model_copy(update={"skip_if_already_sent": True})
    out = run_daily(s2, app_config, services(), now=DAY1 + timedelta(hours=1))
    assert not out.telegram_sent and not out.email_sent


def test_evening_run_reports_next_day(env, app_config):
    """Fines de semana: la corrida de las 19:00 del viernes informa los partidos del sábado."""
    settings, _fb, *_rest, services = env
    fb = FakeFootballApi()  # partidos de DAY1 aún sin jugar
    tn = FakeTennisApi()
    svc = services()
    svc.football = ApiFootballClient(
        settings, transport=httpx.MockTransport(fb), sleep=lambda s: None
    )
    svc.tennis = TennisApiClient(
        settings, transport=httpx.MockTransport(tn), sleep=lambda s: None, min_interval=0
    )
    evening_before = DAY1 - timedelta(hours=12, minutes=15)  # 31-may 18:45 Bogotá
    s2 = settings.model_copy(update={"skip_if_already_sent": True})
    shutil.rmtree(settings.cache_dir, ignore_errors=True)
    out = run_daily(s2, app_config, svc, now=evening_before, send=True, days_ahead=1)
    assert out.payload.report_date == DAY1.date()  # informe de los partidos del 1-jun
    assert out.payload.n_football == 2 and "Fecha: 2024-06-01" in out.markdown
    with session_scope(URL) as s:
        assert s.get(PipelineRun, out.run_id).run_date == DAY1.date()
    # El 1-jun ya se había enviado (test_day1): el guard de no reenvío usa la fecha del informe
    assert not out.telegram_sent and not out.email_sent
