"""context_guard: verifica que el contexto del agente coincide con el proyecto real.

Uso:  python docs/context_guard.py [--docs docs]
Compara lo afirmado en la memoria contra archivos reales. No inventa nada:
si algo falta o contradice, muestra CONTEXT LOST/INCOMPLETE y qué leer.
Sale 0 si OK, 1 si hay problemas.
"""
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DOCS = Path(__file__).parent
ROOT = DOCS.parent
REQUIRED_DOCS = ["PROJECT_STATE.md", "PROJECT_MEMORY.md",
                 "CONTEXT_CONTRACT.md", "CHECKPOINT.json"]
CHECKPOINT_KEYS = ["project_name", "current_phase", "pending_tasks",
                   "tests_status", "next_action", "context_version"]


def check_docs(docs):
    problems = []
    for f in REQUIRED_DOCS:
        if not (docs / f).exists():
            problems.append(f"falta documento: {f} (recrear desde cero está prohibido; pedirlo)")
    return problems


def check_checkpoint(docs):
    problems = []
    p = docs / "CHECKPOINT.json"
    if not p.exists():
        return ["CHECKPOINT.json ausente"]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"CHECKPOINT.json inválido: {e}"]
    for k in CHECKPOINT_KEYS:
        if k not in data:
            problems.append(f"CHECKPOINT.json sin clave: {k}")
    for secret_pat in (r"pat_[A-Za-z0-9]{10,}",):
        if re.search(secret_pat, p.read_text(encoding="utf-8")):
            problems.append("CHECKPOINT.json contendría un secreto: prohibido")
    return problems


def check_code(root):
    """Hechos del código real que la memoria afirma."""
    problems = []
    facts = [
        ("bot/providers.py", r"class DerivProvider", True),
        ("bot/main.py", r"BinanceProvider", False),   # no debe importar Binance
        ("bot/main.py", r"PAXGUSDT", False),           # ni símbolos Binance
        ("bot/strategies.py", r"def cross_up", True),
        ("bot/strategies.py", r"def cross_down", True),
        ("bot/brokers.py", r"NOT IMPLEMENTED / WAITING FOR DEMO AUTH VALIDATION", True),
        ("bot/brokers.py", r"raise NotImplementedError", True),
    ]
    for rel, pat, must_exist in facts:
        p = root / rel
        if not p.exists():
            problems.append(f"código ausente: {rel}")
            continue
        found = re.search(pat, p.read_text(encoding="utf-8")) is not None
        if must_exist and not found:
            problems.append(f"contradicción: {rel} no contiene '{pat}'")
        if not must_exist and found:
            problems.append(f"contradicción: {rel} aún contiene '{pat}'")
    return problems


def check_no_secrets(docs):
    problems = []
    for f in docs.glob("*.md"):
        if re.search(r"pat_[A-Za-z0-9]{10,}", f.read_text(encoding="utf-8")):
            problems.append(f"secreto real en {f.name}: prohibido por contrato §9")
    return problems


def main():
    docs = Path(sys.argv[sys.argv.index("--docs") + 1] if "--docs" in sys.argv else DOCS)
    root = docs.parent
    problems = check_docs(docs) + check_checkpoint(docs) + check_code(root) + check_no_secrets(docs)
    if problems:
        print("⚠️ CONTEXT LOST / CONTEXT INCOMPLETE")
        for pr in problems:
            print(" -", pr)
        print("Archivos a leer: PROJECT_STATE.md, PROJECT_MEMORY.md, "
              "CONTEXT_CONTRACT.md, CHECKPOINT.json")
        try:
            cp = json.loads((docs / "CHECKPOINT.json").read_text(encoding="utf-8"))
            print("Último estado confirmado:",
                  cp.get("current_phase"), "| tests:", cp.get("tests_status"),
                  "| siguiente:", cp.get("next_action"))
        except Exception:
            print("Último estado confirmado: DESCONOCIDO (CHECKPOINT ilegible)")
        return 1
    print("CONTEXT GUARD: OK (docs + código verificados, sin secretos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
