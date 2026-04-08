# QuestLab

A lightweight, self-hosted web app for running sequential questionnaire batteries in human factors experiments. Designed for in-lab use over a local network — participants enter a code on any browser-equipped device (tablet, laptop, phone) with no internet required.

**Key properties**
- Every keystroke and selection is saved instantly to SQLite (autosave on every input event)
- A flat CSV is written after each questionnaire is submitted — data survives a crash mid-session
- Experiments are defined in plain YAML files; no code changes are needed to add a new study
- Multi-condition experiments in a single file; participants navigate between conditions from a progress screen
- Bilingual (FR/EN) toggle per form, driven entirely by YAML fields

---

## Quick start

```bash
git clone https://github.com/yourname/questlab.git
cd questlab
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000` in a browser. The LAN IP is printed at startup so participants can connect from other devices.

**Requirements:** Python 3.9+, Flask, PyYAML (see `requirements.txt`).

---

## Project layout

```
questlab/
├── app.py                    # Flask application
├── experiments/              # One YAML per study (all conditions inside)
│   └── HAT_study.yaml
├── forms/                    # One YAML per questionnaire instrument
│   ├── nasa_tlx_evaluation.yaml
│   ├── sus.yaml
│   └── ...
├── templates/
│   ├── index.html            # Participant start page
│   ├── form.html             # Question renderer
│   └── complete.html         # Condition / study complete screen
├── static/
│   └── style.css
└── data/                     # Auto-created; holds SQLite DB and CSV files
```

---

## Experiment file format

`experiments/<id>.yaml`

```yaml
experiment_id: my_study
name: "My Study"

conditions:
  baseline:
    questionnaires:
      - demographics
      - nasa_tlx_evaluation
      - sus

  condition_a:
    questionnaires:
      - nasa_tlx_evaluation
      - trust_propensity
      - sus
```

Conditions appear in declaration order on the progress screen. Participants can start any unfinished condition in any order. Each condition produces its own session row in the CSV (keyed by `condition`).

---

## Form file format

`forms/<id>.yaml`

```yaml
id: my_form
name: "Form Title"
language: en          # en | fr  (primary display language)
bilingual: true       # show FR↔EN toggle button  (optional)
name_fr: "Titre"      # alt name shown when toggled
instructions: >       # shown above the questions
instructions_fr: >    # alt instructions (when bilingual: true)

questions:
  - id: q1
    type: likert5     # see question types below
    label: "Question text"
    label_fr: "Texte FR"          # when bilingual: true
    description: "Help text"      # grey sub-label  (optional)
    description_fr: "Aide FR"
    required: false               # default: true
```

---

## Question types

| Type | Renders as | Key extra fields |
|---|---|---|
| `likert5` | 5-point radio scale | `anchors` (overrides preset labels) |
| `likert7` | 7-point radio scale | `anchors`, `anchors_fr` / `anchors_en` |
| `vas` | Visual Analogue Slider | `min`, `max`, `min_label`, `max_label` |
| `single_choice` | Radio list | `options`, `options_fr` |
| `multi_choice` | Checkbox list | `options`, `options_fr` |
| `free_text` | Textarea | `rows`, `placeholder` |
| `number` | Number input | `min`, `max`, `step`, `placeholder` |

**Likert preset anchors** are automatically applied based on `language:` in the form file (EN and FR presets for both 5- and 7-point scales are built in). Override per question with `anchors:`.

**VAS pristine state:** the thumb is hidden until the participant first touches the slider, preventing anchoring bias from a default position.

---

## Data export

- **Live CSV:** `data/<experiment_id>.csv` — appended after every questionnaire submission. Columns: `participant, experiment_id, condition, started_at, questionnaire_id, question_id, value, saved_at`.
- **Full download:** `http://<host>:5000/export/<experiment_id>` — returns a complete CSV of all responses for that experiment.

---

## Bundled instruments

| File | Instrument | Language |
|---|---|---|
| `nasa_tlx_evaluation.yaml` | NASA Task Load Index (6 VAS, 0–20) | EN |
| `nasa_tlx_subscale_ranking.yaml` | NASA-TLX 15-pair pairwise weighting | EN |
| `sus.yaml` | System Usability Scale (10 × Likert-5) | EN |
| `trust_propensity.yaml` | Trust Propensity scale (7 × Likert-5) | EN |
| `checklist_for_trust_jian_et_al_2000.yaml` | Jian et al. (2000) Trust Checklist (12 × Likert-7) | EN/FR |
| `trust_in_automation_scale_korber_et_al_2015.yaml` | Körber et al. (2015) Trust in Automation (12 × Likert-5) | EN |
| `ueq_plus_customized.yaml` | UEQ+ semantic differential (12 × Likert-7) | FR/EN |
| `oversight_bespoke.yaml` | Bespoke oversight & monitoring scale (10 × Likert-5) | FR/EN |
| `trust_risk_bespoke.yaml` | Bespoke trust + perceived risk VAS (2 × 0–100) | FR/EN |
| `trust_ranking_tars.yaml` | System trust ranking — TARS/TARP-F/TARP-S | FR/EN |
| `autonomous_systems_and_vocal_commands_familiarity.yaml` | Familiarity questionnaire | FR/EN |
| `current_operational_factors.yaml` | Fatigue / operational state screener | FR/EN |

---

## License

GNU General Public License v2.0 — see [LICENSE](LICENSE).
