import os
import csv
import json
import uuid
import sqlite3
import socket
import io
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Toronto")

LIKERT_ANCHORS = {
    "en": {
        "likert5": [
            "Strongly Disagree",
            "Disagree",
            "Neither agree nor disagree",
            "Agree",
            "Strongly Agree",
        ],
        "likert7": [
            "Strongly Disagree",
            "Disagree",
            "Somewhat Disagree",
            "Neither agree nor disagree",
            "Somewhat Agree",
            "Agree",
            "Strongly Agree",
        ],
    },
    "fr": {
        "likert5": [
            "Pas du tout d'accord",
            "Pas d'accord",
            "Indiff\u00e9rent",
            "D'accord",
            "Tout \u00e0 fait d'accord",
        ],
        "likert7": [
            "Pas du tout d'accord",
            "Pas d'accord",
            "Plut\u00f4t pas d'accord",
            "Indiff\u00e9rent",
            "Plut\u00f4t d'accord",
            "D'accord",
            "Tout \u00e0 fait d'accord",
        ],
    },
}
from pathlib import Path

import yaml
from flask import Flask, g, render_template, request, redirect, url_for, jsonify, send_file

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.jinja_env.globals["LIKERT_ANCHORS"] = LIKERT_ANCHORS

DB_PATH      = Path("data/responses.db")
EXPERIMENTS  = Path("experiments")
FORMS        = Path("forms")


# ── Database ─────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")   # safe concurrent writes
        conn.execute("PRAGMA synchronous=NORMAL")
        _init_db(conn)
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()


def _init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id    TEXT PRIMARY KEY,
            participant   TEXT NOT NULL,
            experiment_id TEXT NOT NULL,
            condition     TEXT NOT NULL DEFAULT '',
            questionnaires TEXT NOT NULL DEFAULT '[]',
            started_at    TEXT NOT NULL,
            current_q     INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS answers (
            session_id       TEXT NOT NULL,
            questionnaire_id TEXT NOT NULL,
            question_id      TEXT NOT NULL,
            value            TEXT,
            saved_at         TEXT NOT NULL,
            PRIMARY KEY (session_id, questionnaire_id, question_id)
        );

        CREATE TABLE IF NOT EXISTS completions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id       TEXT NOT NULL,
            questionnaire_id TEXT NOT NULL,
            q_index          INTEGER NOT NULL,
            completed_at     TEXT NOT NULL
        );
    """)
    # Migrate: add questionnaires column if upgrading from older schema
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "questionnaires" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN questionnaires TEXT NOT NULL DEFAULT '[]'")
    conn.commit()


# ── YAML helpers ─────────────────────────────────────────────────────────────

def _load_experiment(experiment_id):
    with open(EXPERIMENTS / f"{experiment_id}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_form(form_id):
    with open(FORMS / f"{form_id}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _list_experiments():
    if not EXPERIMENTS.exists():
        return []
    return sorted(p.stem for p in EXPERIMENTS.glob("*.yaml"))


def _experiment_conditions(experiment_id):
    """Return ordered list of condition names for an experiment."""
    exp = _load_experiment(experiment_id)
    if "conditions" in exp:
        return list(exp["conditions"].keys())
    # backward-compat: single-condition file
    return [exp.get("condition", "default")]


def _all_conditions():
    """Return {experiment_id: [condition, ...]} for every experiment file."""
    return {eid: _experiment_conditions(eid) for eid in _list_experiments()}


# ── CSV helpers ───────────────────────────────────────────────────────────────

_CSV_HEADERS = [
    "participant", "experiment_id", "condition", "started_at",
    "questionnaire_id", "question_id", "value", "saved_at",
]

def _append_csv(experiment_id: str, rows):
    """Append rows to data/<experiment_id>.csv, creating the file if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    csv_path = DB_PATH.parent / f"{experiment_id}.csv"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(_CSV_HEADERS)
        w.writerows(rows)


# ── Jinja2 filter ─────────────────────────────────────────────────────────────

@app.template_filter("from_json")
def from_json_filter(s):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
                           experiments=_list_experiments(),
                           all_conditions=_all_conditions())


@app.route("/start", methods=["POST"])
def start():
    participant   = request.form.get("participant", "").strip()
    experiment_id = request.form.get("experiment_id", "").strip()
    if not participant or not experiment_id:
        return redirect(url_for("index"))

    experiment = _load_experiment(experiment_id)
    condition  = request.form.get("condition", "").strip()

    # Resolve questionnaire list from selected condition (or flat legacy format)
    if "conditions" in experiment:
        cond_data  = experiment["conditions"].get(condition, {})
        form_list  = cond_data.get("questionnaires", []) if isinstance(cond_data, dict) else cond_data
    else:
        form_list  = experiment.get("questionnaires", [])
        if not condition:
            condition = experiment.get("condition", "")

    session_id = str(uuid.uuid4())
    db = get_db()
    db.execute(
        "INSERT INTO sessions "
        "(session_id, participant, experiment_id, condition, started_at, current_q) "
        "VALUES (?,?,?,?,?,0)",
        (session_id, participant, experiment_id, condition,
         datetime.now(TZ).isoformat()),  # Eastern Time
    )
    # Store resolved form list in session so questionnaire route doesn't need to
    # re-derive it from the condition name
    db.execute(
        "UPDATE sessions SET questionnaires=? WHERE session_id=?",
        (json.dumps(form_list), session_id),
    )
    db.commit()
    return redirect(url_for("questionnaire", session_id=session_id, q_index=0))


@app.route("/q/<session_id>/<int:q_index>")
def questionnaire(session_id, q_index):
    db      = get_db()
    session = db.execute(
        "SELECT * FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not session:
        return redirect(url_for("index"))

    experiment = _load_experiment(session["experiment_id"])
    stored_qs = session["questionnaires"]
    form_list = json.loads(stored_qs) if stored_qs and stored_qs != '[]' else experiment.get("questionnaires", [])

    if q_index >= len(form_list):
        return redirect(url_for("complete", session_id=session_id))

    form_id = form_list[q_index]
    form    = _load_form(form_id)

    # Reload any answers already autosaved (allows page refresh / resume)
    existing = {
        r["question_id"]: r["value"]
        for r in db.execute(
            "SELECT question_id, value FROM answers "
            "WHERE session_id=? AND questionnaire_id=?",
            (session_id, form_id),
        ).fetchall()
    }

    return render_template(
        "form.html",
        session_id=session_id,
        q_index=q_index,
        total=len(form_list),
        experiment=experiment,
        form=form,
        form_id=form_id,
        existing=existing,
        participant=session["participant"],
    )


@app.route("/autosave", methods=["POST"])
def autosave():
    """Called by JS on every input change — persists one answer immediately."""
    data             = request.get_json(silent=True) or {}
    session_id       = data.get("session_id")
    questionnaire_id = data.get("questionnaire_id")
    question_id      = data.get("question_id")
    value            = data.get("value")

    if not (session_id and questionnaire_id and question_id):
        return jsonify(ok=False), 400

    if isinstance(value, list):
        value = json.dumps(value)
    elif value is None:
        value = ""
    else:
        value = str(value)

    db = get_db()
    db.execute(
        """
        INSERT INTO answers (session_id, questionnaire_id, question_id, value, saved_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(session_id, questionnaire_id, question_id)
        DO UPDATE SET value=excluded.value, saved_at=excluded.saved_at
        """,
        (session_id, questionnaire_id, question_id,
         value, datetime.now(TZ).isoformat()),
    )
    db.commit()
    return jsonify(ok=True)


@app.route("/submit/<session_id>/<int:q_index>", methods=["POST"])
def submit(session_id, q_index):
    db      = get_db()
    session = db.execute(
        "SELECT * FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not session:
        return redirect(url_for("index"))

    experiment = _load_experiment(session["experiment_id"])
    stored_qs = session["questionnaires"]
    form_list = json.loads(stored_qs) if stored_qs and stored_qs != '[]' else experiment.get("questionnaires", [])
    form_id    = form_list[q_index]
    form       = _load_form(form_id)
    now        = datetime.now(TZ).isoformat()

    # Final save from the form POST — belt-and-suspenders on top of autosave
    for q in form["questions"]:
        qid = q["id"]
        if q["type"] == "multi_choice":
            value = json.dumps(request.form.getlist(qid))
        else:
            value = request.form.get(qid, "")
        db.execute(
            """
            INSERT INTO answers (session_id, questionnaire_id, question_id, value, saved_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(session_id, questionnaire_id, question_id)
            DO UPDATE SET value=excluded.value, saved_at=excluded.saved_at
            """,
            (session_id, form_id, qid, value, now),
        )

    db.execute(
        "INSERT INTO completions (session_id, questionnaire_id, q_index, completed_at) "
        "VALUES (?,?,?,?)",
        (session_id, form_id, q_index, now),
    )
    db.execute(
        "UPDATE sessions SET current_q=? WHERE session_id=?",
        (q_index + 1, session_id),
    )
    db.commit()

    # Write completed questionnaire answers to the experiment CSV immediately
    saved_rows = db.execute(
        """
        SELECT s.participant, s.experiment_id, s.condition, s.started_at,
               a.questionnaire_id, a.question_id, a.value, a.saved_at
        FROM   answers a
        JOIN   sessions s ON a.session_id = s.session_id
        WHERE  a.session_id = ? AND a.questionnaire_id = ?
        ORDER  BY a.question_id
        """,
        (session_id, form_id),
    ).fetchall()
    _append_csv(session["experiment_id"], saved_rows)

    next_q = q_index + 1
    if next_q >= len(form_list):
        return redirect(url_for("complete", session_id=session_id))
    return redirect(url_for("questionnaire", session_id=session_id, q_index=next_q))


@app.route("/done/<session_id>")
def complete(session_id):
    db         = get_db()
    session    = db.execute(
        "SELECT * FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not session:
        return redirect(url_for("index"))

    experiment   = _load_experiment(session["experiment_id"])
    all_conds    = _experiment_conditions(session["experiment_id"])

    # Find which conditions this participant has already completed
    done_rows = db.execute(
        "SELECT DISTINCT condition FROM sessions WHERE participant=? AND experiment_id=?",
        (session["participant"], session["experiment_id"]),
    ).fetchall()
    done_conds = {r["condition"] for r in done_rows}

    # Next condition not yet started
    next_cond = next((c for c in all_conds if c not in done_conds), None)
    all_finished = next_cond is None

    return render_template(
        "complete.html",
        session=session,
        experiment=experiment,
        all_conds=all_conds,
        done_conds=done_conds,
        next_cond=next_cond,
        all_finished=all_finished,
    )


@app.route("/next/<session_id>", methods=["POST"])
def next_condition(session_id):
    """Start the next condition for the same participant without re-entering code."""
    db      = get_db()
    session = db.execute(
        "SELECT * FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not session:
        return redirect(url_for("index"))

    condition = request.form.get("condition", "").strip()
    experiment_id = session["experiment_id"]
    experiment    = _load_experiment(experiment_id)

    if "conditions" in experiment:
        cond_data = experiment["conditions"].get(condition, {})
        form_list = cond_data.get("questionnaires", []) if isinstance(cond_data, dict) else cond_data
    else:
        form_list = experiment.get("questionnaires", [])

    new_session_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO sessions "
        "(session_id, participant, experiment_id, condition, questionnaires, started_at, current_q) "
        "VALUES (?,?,?,?,?,?,0)",
        (new_session_id, session["participant"], experiment_id, condition,
         json.dumps(form_list), datetime.now(TZ).isoformat()),
    )
    db.commit()
    return redirect(url_for("questionnaire", session_id=new_session_id, q_index=0))


@app.route("/export/<experiment_id>")
def export(experiment_id):
    """Download a flat CSV of all responses for this experiment."""
    db   = get_db()
    rows = db.execute(
        """
        SELECT s.participant, s.experiment_id, s.condition, s.started_at,
               a.questionnaire_id, a.question_id, a.value, a.saved_at
        FROM   answers a
        JOIN   sessions s ON a.session_id = s.session_id
        WHERE  s.experiment_id = ?
        ORDER  BY s.participant, s.started_at, a.questionnaire_id, a.question_id
        """,
        (experiment_id,),
    ).fetchall()

    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow([
        "participant", "experiment_id", "condition", "started_at",
        "questionnaire_id", "question_id", "value", "saved_at",
    ])
    w.writerows(rows)
    buf.seek(0)

    filename = f"{experiment_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "127.0.0.1"

    experiments = _list_experiments()

    print("\n  ╔══════════════════════════════════════╗")
    print("  ║        QuickWebForms running         ║")
    print("  ╠══════════════════════════════════════╣")
    print(f"  ║  Local:   http://127.0.0.1:5000      ║")
    print(f"  ║  Network: http://{lan_ip}:5000")
    print("  ╠══════════════════════════════════════╣")
    if experiments:
        print("  ║  Export URLs:                        ║")
        for exp in experiments:
            print(f"  ║  /export/{exp}")
    print("  ╚══════════════════════════════════════╝\n")

    app.run(host="0.0.0.0", port=5000, debug=False)
