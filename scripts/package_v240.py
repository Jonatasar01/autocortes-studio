from pathlib import Path
import shutil, zipfile
root=Path(__file__).resolve().parents[1]
workspace=root.parents[2]
target=workspace/'outputs'/'AutoCortes-Studio-Local-v2.4.0'
target.mkdir(parents=True,exist_ok=True)
exclude={'.venv','data','entrada','saida','evidencias','.git','.pytest_cache','__pycache__'}
for source in root.rglob('*'):
    rel=source.relative_to(root)
    if rel.parts[0] in exclude or '__pycache__' in rel.parts or source.suffix in {'.pyc','.pyo'} or not source.is_file(): continue
    dest=target/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
for name in ['pytest-v240.log','desktop-v240.png','mobile-v240.png','filmcraft-real.log']:
    source=root/'evidencias'/name
    if source.exists():
        (target/'evidencias').mkdir(exist_ok=True);shutil.copy2(source,target/'evidencias'/name)
archive=Path(str(target)+'.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for file in target.rglob('*'):
        if file.is_file():z.write(file,Path(target.name)/file.relative_to(target))
print(archive)
print(archive.stat().st_size)
