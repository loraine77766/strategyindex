"""context:restore — reconstruye el contexto leyendo docs + git + cambios recientes.

Uso:  python docs/context_restore.py
Imprime: CURRENT PROJECT STATE / CURRENT TASK / WHAT IS WORKING /
WHAT IS BROKEN / WHAT WAS LAST CHANGED / WHAT MUST BE DONE NEXT.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DOCS = Path(__file__).parent
ROOT = DOCS.parent


def read(name, limit=4000):
    p = DOCS / name
    if not p.exists():
        return f"({name} AUSENTE)"
    t = p.read_text(encoding="utf-8")
    return t if len(t) <= limit else t[:limit] + "\n…(recortado)"


def git_info():
    try:
        r = subprocess.run(["git", "status", "--short"], capture_output=True,
                           text=True, cwd=str(ROOT), timeout=15)
        if r.returncode != 0:
            return "git no disponible / no es repo"
        out = r.stdout.strip() or "(árbol limpio)"
        log = subprocess.run(["git", "log", "--oneline", "-5"], capture_output=True,
                             text=True, cwd=str(ROOT), timeout=15).stdout.strip()
        return out + ("\n" + log if log else "")
    except Exception as e:
        return f"git no disponible ({e})"


def recent(n=8):
    files = []
    for pat in ("bot/*.py", "bot/frontend/*", "bot/tests/*.py", "docs/*"):
        files += list(ROOT.glob(pat))
    files = sorted([f for f in files if f.is_file()],
                   key=lambda f: f.stat().st_mtime, reverse=True)[:n]
    return "\n".join(f"{f.relative_to(ROOT)} "
                     f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(f.stat().st_mtime))})"
                     for f in files)


def main():
    try:
        cp = json.loads((DOCS / "CHECKPOINT.json").read_text(encoding="utf-8"))
    except Exception:
        cp = {}
    print("CURRENT PROJECT STATE\n" + read("PROJECT_STATE.md", 2500))
    print("\nCURRENT TASK\n" + str(cp.get("current_task", "none")))
    mem = read("PROJECT_MEMORY.md", 12000)
    working = [l for l in mem.splitlines() if l.startswith("- Terminado")]
    print("\nWHAT IS WORKING\n" + ("\n".join(working) or "(ver memoria)"))
    print("\nWHAT IS BROKEN\n" + "; ".join(cp.get("known_errors", [])))
    print("\nWHAT WAS LAST CHANGED\n" + recent() + "\n--- git ---\n" + git_info())
    print("\nWHAT MUST BE DONE NEXT\n" + str(cp.get("next_action", "?"))
          + "\nPendientes: " + "; ".join(cp.get("pending_tasks", [])))


if __name__ == "__main__":
    main()
