import os,sys
from pathlib import Path
args=sys.argv[1:]
if args==['daemon']:
    if not (Path.home()/'.runtime-ready').is_file():raise SystemExit('Runtime requires link-project setup before start')
    os.execvp('multica',['multica','daemon','start','--foreground','--no-auto-update','--no-auto-reload'])
elif args and args[0]=='codex-login':os.execvp('codex',['codex','login','--device-auth'])
elif args and args[0]=='agy-login':os.execvp('agy',['agy'])
elif args and args[0]=='doctor':os.execvp('coder-project',['coder-project','doctor'])
else:raise SystemExit('Unsupported runtime operation')
