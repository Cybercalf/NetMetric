"""校验 PE 清单与归档依赖，并在不含 Python PATH 的环境运行只读版自检。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import pefile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import VERSION


def inspect_exe(path: Path, level: str):
    pe = pefile.PE(str(path))
    assert pe.FILE_HEADER.Machine == 0x8664, "必须为 x64"
    assert pe.OPTIONAL_HEADER.Subsystem == 2, "必须为 Windows GUI 程序"
    manifests = []
    for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if entry.id == 24:
            for resource in entry.directory.entries:
                for lang in resource.directory.entries:
                    data = lang.data.struct
                    manifests.append(pe.get_data(data.OffsetToData, data.Size).decode("utf-8"))
    assert any(f'level="{level}"' in text for text in manifests), "权限清单不符合预期"
    archive = CArchiveReader(str(path))
    names = {Path(name).name.lower() for name in archive.toc}
    python_dll = next(n for n in names if re.fullmatch(r"python3\d+\.dll", n))
    digits = python_dll.removeprefix("python").removesuffix(".dll")
    required = {python_dll, "_tkinter.pyd", f"pythoncom{digits}.dll",
                f"pywintypes{digits}.dll", "vcruntime140.dll"}
    assert required <= names, f"缺少运行时：{required - names}"
    assert any(re.fullmatch(r"tcl\d+t?\.dll", n) for n in names), "缺少 Tcl DLL"
    assert any(re.fullmatch(r"tk\d+t?\.dll", n) for n in names), "缺少 Tk DLL"
    missing = set()
    for name in archive.toc:
        if not name.lower().endswith((".dll", ".pyd")):
            continue
        binary = pefile.PE(data=archive.extract(name), fast_load=True)
        binary.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        for entry in getattr(binary, "DIRECTORY_ENTRY_IMPORT", []):
            dependency = entry.dll.decode("ascii").lower()
            if dependency.startswith(("api-ms-win-", "ext-ms-win-")) or dependency in names:
                continue
            if not (Path(os.environ["SystemRoot"]) / "System32" / dependency).exists():
                missing.add(dependency)
        binary.close()
    assert not missing, f"缺少 DLL：{missing}"
    pe.close()
    return {"file": path.name, "bytes": path.stat().st_size, "manifest": level,
            "runtime_dependencies": "pass"}


def main():
    report = {"artifacts": [inspect_exe(ROOT / "dist/NetMetric.exe", "requireAdministrator"),
                            inspect_exe(ROOT / "dist/NetMetric-ReadOnly.exe", "asInvoker")]}
    env = {k.upper(): v for k, v in os.environ.items()
           if not k.upper().startswith(("PYTHON", "CONDA", "TCL", "TK_"))}
    env["PATH"] = os.pathsep.join((env["SYSTEMROOT"], str(Path(env["SYSTEMROOT"]) / "System32")))
    exe = ROOT / "dist/NetMetric-ReadOnly.exe"
    with tempfile.TemporaryDirectory(prefix="netmetric-qa-") as directory:
        output = Path(directory) / "snapshot.json"
        for flag, key in (("--diagnose", "diagnostic"), ("--self-test", "gui_self_test")):
            subprocess.run([str(exe), flag, "--output", str(output)], env=env, cwd=directory,
                           check=True, timeout=35)
            data = json.loads(output.read_text(encoding="utf-8"))
            assert data["version"] == VERSION
            if key == "diagnostic":
                assert isinstance(data["ifaces"], list) and isinstance(data["routes"], list)
                assert data["best_route_1_1_1_1"] is None or data["best_route_1_1_1_1"]["ifindex"] > 0
                report[key] = {"interfaces": len(data["ifaces"]), "routes": len(data["routes"]),
                               "warnings": data["warnings"], "admin": data["admin"]}
            else:
                assert data["frozen"] is True
                report[key] = data
            output.unlink()
    folder = ROOT / "build"
    folder.mkdir(exist_ok=True)
    (folder / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
