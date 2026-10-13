# DecompileFunctions.py
# Ghidra script (GUI or headless). Runs under PyGhidra (Python 3, required on Ghidra 12.1+) and Jython 2.7.
# Exports every non-thunk function as {name, entry, size, decompiled} to a JSON file for REvAI.
#@category REvAI
from ghidra.app.decompiler import DecompInterface
from ghidra.util import Msg
import json
import os

DECOMPILE_TIMEOUT_SECS = 60  # per function; big functions often need more than 30


def decompile_all_functions():
    fm = currentProgram.getFunctionManager()
    total = fm.getFunctionCount()
    decompiler = DecompInterface()
    decompiler.openProgram(currentProgram)

    results = []
    skipped_thunks = 0
    failed = 0

    try:
        for i, func in enumerate(fm.getFunctions(True)):
            # Use the script's own monitor so Cancel in the GUI (or a headless timeout) actually stops the run
            if monitor.isCancelled():
                Msg.warn(None, "Cancelled by user after {} functions".format(i))
                break
            monitor.setMessage("REvAI: decompiling {}/{}".format(i + 1, total))

            # Thunks are just jump stubs to imports; skipping them saves AI tokens and noise
            if func.isThunk():
                skipped_thunks += 1
                continue

            name = str(func.getName())
            entry = str(func.getEntryPoint().toString())
            size = int(func.getBody().getNumAddresses())

            decompiled = "Failed to decompile"
            try:
                res = decompiler.decompileFunction(func, DECOMPILE_TIMEOUT_SECS, monitor)
                if res and res.decompileCompleted():
                    decompiled = str(res.getDecompiledFunction().getC())
                else:
                    failed += 1
            except Exception as e:
                # One bad function should not kill the whole export
                failed += 1
                Msg.warn(None, "Error decompiling {} @ {}: {}".format(name, entry, str(e)))

            results.append({
                "name": name,
                "entry": entry,
                "size": size,
                "decompiled": decompiled,
            })
    finally:
        decompiler.dispose()

    Msg.info(None, "Exported {} functions ({} thunks skipped, {} failed)".format(len(results), skipped_thunks, failed))
    return results


def default_output_path():
    # Override with REVAI_OUTPUT_DIR; otherwise ~/REvAI/Ghidra/Decompiled_JSON
    output_dir = os.environ.get("REVAI_OUTPUT_DIR") or os.path.join(
        os.path.expanduser("~"), "REvAI", "Ghidra", "Decompiled_JSON")
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
    except Exception as e:
        Msg.warn(None, "Could not create output directory: {}".format(str(e)))
        output_dir = os.path.expanduser("~")

    program_name = str(currentProgram.getName())
    safe_name = program_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    return os.path.join(output_dir, "{}_decompiled.json".format(safe_name))


# Handle output path (decompile_binary.sh passes it as the first script arg)
args = getScriptArgs()
output_path = str(args[0]) if args else default_output_path()

Msg.info(None, "Saving decompilation to: {}".format(output_path))

data = decompile_all_functions()

with open(output_path, "w") as f:
    json.dump(data, f, indent=2)

print("Decompilation complete. Saved to: {}".format(output_path))
