"""Estado puro y reproducible para el loop engineering de maps-to-3d.

Este modulo NO importa bpy. El agente evalua cada par render/referencia, registra
scores y defectos, aplica una sola familia de cambio y usa ``decision`` para
continuar, aceptar o volver al mejor checkpoint.
"""
from copy import deepcopy
import json
import os
import tempfile


SCHEMA_VERSION = 1
DIMENSION_WEIGHTS = {
    "semantics": 0.25,
    "geometry": 0.22,
    "framing": 0.18,
    "materials": 0.15,
    "lighting": 0.12,
    "realism": 0.08,
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _score(value):
    value = float(value)
    if not 0.0 <= value <= 100.0:
        raise ValueError("scores must be between 0 and 100")
    return round(value, 2)


def normalize_scores(scores):
    missing = set(DIMENSION_WEIGHTS) - set(scores or {})
    if missing:
        raise ValueError("missing score dimensions: %s" % ", ".join(sorted(missing)))
    return {key: _score(scores[key]) for key in DIMENSION_WEIGHTS}


def weighted_score(scores):
    scores = normalize_scores(scores)
    return round(sum(scores[key] * weight
                     for key, weight in DIMENSION_WEIGHTS.items()), 2)


def rank_defects(defects):
    """Ordena fallos por severidad y luego por impacto estimado."""
    out = []
    for defect in defects or []:
        item = dict(defect)
        severity = str(item.get("severity", "medium")).lower()
        if severity not in SEVERITY_ORDER:
            raise ValueError("invalid defect severity: %s" % severity)
        item["severity"] = severity
        item["impact"] = _score(item.get("impact", 50))
        item.setdefault("category", "realism")
        item.setdefault("fix", "inspect and correct")
        out.append(item)
    return sorted(out, key=lambda d: (SEVERITY_ORDER[d["severity"]],
                                      -d["impact"], str(d.get("category"))))


def new_state(project, references=None, max_iterations=6, target_score=85.0,
              min_dimension=70.0, min_delta=1.0, patience=2):
    if int(max_iterations) < 1:
        raise ValueError("max_iterations must be >= 1")
    return {
        "schema_version": SCHEMA_VERSION,
        "project": str(project),
        "references": list(references or []),
        "policy": {
            "max_iterations": int(max_iterations),
            "target_score": _score(target_score),
            "min_dimension": _score(min_dimension),
            "min_delta": max(0.0, float(min_delta)),
            "patience": max(1, int(patience)),
        },
        "iterations": [],
        "best_iteration": None,
        "best_score": None,
        "stagnation": 0,
        "status": "running",
    }


def record_iteration(state, scores, defects=None, change=None, artifacts=None,
                     camera_signature=None, notes=None):
    """Registra una observacion y actualiza best/stagnation sin mutar el input.

    Iteracion 0 es el baseline y no requiere ``change``. Desde iteracion 1 exigir
    una sola categoria de cambio para poder atribuir la mejora o regresion.
    """
    out = deepcopy(state)
    if out.get("status") != "running":
        raise ValueError("cannot append to a finished loop")
    index = len(out["iterations"])
    if index > 0:
        if not isinstance(change, dict) or not change.get("category"):
            raise ValueError("iterations after baseline require one controlled change")
        if isinstance(change.get("category"), (list, tuple, set)):
            raise ValueError("change.category must identify one failure family")

    clean_scores = normalize_scores(scores)
    total = weighted_score(clean_scores)
    ranked = rank_defects(defects)
    previous = out["iterations"][-1]["score"] if out["iterations"] else None
    delta = None if previous is None else round(total - previous, 2)
    record = {
        "index": index,
        "scores": clean_scores,
        "score": total,
        "delta": delta,
        "defects": ranked,
        "critical_count": sum(1 for d in ranked if d["severity"] == "critical"),
        "change": deepcopy(change),
        "artifacts": deepcopy(artifacts or {}),
        "camera_signature": camera_signature,
        "notes": notes,
    }
    out["iterations"].append(record)

    if out["best_score"] is None or total > out["best_score"]:
        out["best_score"] = total
        out["best_iteration"] = index

    if delta is None or delta >= out["policy"]["min_delta"]:
        out["stagnation"] = 0
    else:
        out["stagnation"] += 1

    verdict = decision(out)
    out["status"] = verdict["status"]
    return out


def decision(state):
    iterations = state.get("iterations", [])
    if not iterations:
        return {"status": "running", "action": "render_baseline",
                "reason": "no baseline", "next_defect": None}
    latest = iterations[-1]
    policy = state["policy"]
    min_score = min(latest["scores"].values())
    if (latest["score"] >= policy["target_score"] and
            min_score >= policy["min_dimension"] and
            latest["critical_count"] == 0):
        return {"status": "accepted", "action": "deliver",
                "reason": "acceptance gates passed", "next_defect": None}
    if len(iterations) >= policy["max_iterations"]:
        return {"status": "stopped", "action": "restore_best",
                "reason": "maximum iterations reached", "next_defect": None}
    if state.get("stagnation", 0) >= policy["patience"]:
        return {"status": "stopped", "action": "restore_best",
                "reason": "score stagnated", "next_defect": None}
    next_defect = latest["defects"][0] if latest["defects"] else {
        "category": min(latest["scores"], key=latest["scores"].get),
        "severity": "medium", "impact": 50.0,
        "fix": "improve the lowest-scoring dimension",
    }
    return {"status": "running", "action": "correct_one_family",
            "reason": "acceptance gates not met", "next_defect": next_defect}


def best_record(state):
    idx = state.get("best_iteration")
    if idx is None:
        return None
    return deepcopy(state["iterations"][idx])


def save_state(state, path):
    """Escritura atomica para no perder el loop si Blender/MCP falla."""
    path = os.path.abspath(str(path))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".loop-", suffix=".json",
                               dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def load_state(path):
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported loop state schema")
    return state
