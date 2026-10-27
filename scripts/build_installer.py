"""Gera um instalador Windows offline a partir da versão atual do RealGuard.

O pacote contém código, modelos, Python e bibliotecas já instaladas no
ambiente local. Banco, fotos, logs e senhas nunca entram no executável.
"""

import argparse
import hashlib
import json
import os
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
ROOT_FILES = (
    "main.py", "launch_windows.pyw", "RealGuard.exe", "iniciar.bat", "iniciar.sh",
    "Abrir RealGuard.bat", "Assistente NEXT.bat", "requirements.txt",
    "requirements-aibox.txt",
)
ROOT_DIRS = ("api", "app", "config", "database", "docs", "models", "scripts", "web", "runtime")
REQUIRED = (
    "RealGuard.exe", "scripts/native_config.py", "scripts/next_assistant.py",
    "web/index.html", "web/i18n.js", "web/native-config-i18n.tsv",
    "web/brand/realguard-config.ico", "models/insightface/models/buffalo_sc/det_500m.onnx",
    "models/insightface/models/buffalo_sc/w600k_mbf.onnx", "runtime/python.exe",
    "config/realguard.env.example",
)


def allowed(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if "__pycache__" in rel.parts or ".git" in rel.parts or path.suffix in (".pyc", ".pyo"):
        return False
    if rel.parts[0] == "config" and path.name == "realguard.env":
        return False
    if path.name in (".DS_Store", "Thumbs.db"):
        return False
    return True


def project_files(root: Path):
    for name in ROOT_FILES:
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(path)
        yield path, "project/" + name
    for name in ROOT_DIRS:
        directory = root / name
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        for path in directory.rglob("*"):
            if path.is_file() and allowed(path, root):
                yield path, "project/" + path.relative_to(root).as_posix()


def packages(root: Path):
    directory = root / ".venv" / "Lib" / "site-packages"
    if not directory.is_dir():
        raise FileNotFoundError("Ambiente Python atual não encontrado: " + str(directory))
    for path in directory.rglob("*"):
        if path.is_file() and allowed(path, directory):
            yield path, "packages/" + path.relative_to(directory).as_posix()


def build(project: Path, source: Path, output: Path):
    for rel in REQUIRED:
        if not (project / rel).is_file():
            raise FileNotFoundError("Arquivo obrigatório ausente: " + rel)
    if not CSC.is_file():
        raise FileNotFoundError(CSC)
    output.parent.mkdir(parents=True, exist_ok=True)
    stub = output.with_name("RealGuard-Setup-stub.exe")
    payload = output.with_name("RealGuard-payload.zip")
    logo = project / "web" / "brand" / "installer-logo.png"
    icon = project / "web" / "brand" / "realguard-config.ico"
    cmd = [str(CSC), "/nologo", "/target:winexe", "/platform:x64",
           "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll",
           "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll",
           "/win32icon:" + str(icon), "/resource:" + str(logo) + ",BrandLogo",
           "/out:" + str(stub), str(source)]
    subprocess.run(cmd, check=True)
    print("Interface do instalador compilada.", flush=True)

    counts = {"project": 0, "packages": 0}
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                         allowZip64=True) as archive:
        for path, arcname in project_files(project):
            archive.write(path, arcname)
            counts["project"] += 1
        print("Arquivos do projeto incluídos:", counts["project"], flush=True)
        for path, arcname in packages(project):
            archive.write(path, arcname)
            counts["packages"] += 1
            if counts["packages"] % 1000 == 0:
                print("Bibliotecas incluídas:", counts["packages"], flush=True)
        archive.writestr("manifest.json", json.dumps({
            "product": "RealGuard", "created_at": datetime.now().isoformat(timespec="seconds"),
            "project_files": counts["project"], "package_files": counts["packages"],
            "offline": True, "personal_data_included": False,
        }, ensure_ascii=False))
    with zipfile.ZipFile(payload) as archive:
        broken = archive.testzip()
        if broken:
            raise RuntimeError("Arquivo corrompido no pacote: " + broken)
        names = set(archive.namelist())
        assert "project/config/realguard.env" not in names
        assert not any(name.startswith("project/data/") or name.startswith("project/logs/") for name in names)
        for rel in REQUIRED:
            assert "project/" + rel in names, rel
    print("Pacote íntegro; dados privados não incluídos.", flush=True)

    with output.open("wb") as final, stub.open("rb") as compiled, payload.open("rb") as archive:
        while block := compiled.read(1024 * 1024):
            final.write(block)
        offset = final.tell()
        while block := archive.read(1024 * 1024):
            final.write(block)
        length = final.tell() - offset
        final.write(b"RGPKG001" + offset.to_bytes(8, "little") + length.to_bytes(8, "little"))
    digest = hashlib.sha256()
    with output.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    print("Instalador:", output, flush=True)
    print("Tamanho:", output.stat().st_size, "bytes", flush=True)
    print("SHA-256:", digest.hexdigest(), flush=True)
    print("Arquivos:", counts, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source", type=Path, default=Path(__file__).with_name("RealGuardInstaller.cs"))
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "dist" / "RealGuard-Setup.exe")
    args = parser.parse_args()
    build(args.project.resolve(), args.source.resolve(), args.output.resolve())
