"""Install root-owned management code; runtime containers never mount this tree."""
import hashlib,json,os,shutil
from pathlib import Path
from config import need

def install_management(c):
    need(os.geteuid()==0,'sudo_required');source=Path(__file__).resolve().parent;dest=Path('/opt/platform-installer')/c['DEPLOYMENT_ID']
    files=['main.py','config.py','host.py','render.py','gateway.py','multica_host.py','multica_render.py','interconnect.py','management.py','.env.example']
    runtime=['broker.py','domain_provider.py','project_broker.py','coder-project','runtime_setup.py','runtime_entry.py']
    wanted={name:(source/name).read_bytes() for name in files};wanted.update({'runtime/'+name:(source/'runtime'/name).read_bytes() for name in runtime})
    if dest.exists():
        need(all((dest/name).is_file() and (dest/name).read_bytes()==body for name,body in wanted.items()),'management_code_upgrade_requires_review');return
    parent=dest.parent
    if parent.exists():need(parent.stat().st_uid==0 and not parent.is_symlink() and not parent.stat().st_mode&0o022,'unsafe_management_parent')
    else:parent.mkdir(mode=0o755);parent.chmod(0o755)
    dest.mkdir(mode=0o755);(dest/'runtime').mkdir(mode=0o755)
    for name,body in wanted.items():p=dest/name;p.write_bytes(body);p.chmod(0o644)
    (dest/'manifest.json').write_text(json.dumps({k:hashlib.sha256(v).hexdigest() for k,v in wanted.items()}));(dest/'manifest.json').chmod(0o644)
