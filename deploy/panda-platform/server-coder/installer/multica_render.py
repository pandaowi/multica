"""Pinned, provider-patched Multica stack and a socket-free project runtime."""
import json,shutil
from pathlib import Path
from config import need
SOURCE=Path(__file__).resolve().parents[1]
ZERO='00000000-0000-0000-0000-000000000000'

def render_multica(c,out):
    out=Path(out);base=out/'multica';base.mkdir();site=c['DEPLOYMENT_ID'];v=c.values;n=c.n
    log={'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}
    def put(name,data):
        p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data if isinstance(data,str) else json.dumps(data,indent=2)+'\n');p.chmod(0o600)
    for name in ['Dockerfile','Dockerfile.web-provider']:
        shutil.copyfile(SOURCE/'multica/provider-privacy'/name,base/name)
    put('source-ref.json',{'repository':v['MULTICA_SOURCE_REPO'],'revision':v['MULTICA_SOURCE_REV'],'patches_already_integrated':True})
    for name in ['project_broker.py','coder-project','runtime_setup.py','runtime_entry.py']:
        shutil.copyfile(Path(__file__).parent/'runtime'/name,base/name)
    put('provider-privacy.json',{'workspace_id':ZERO,'operator_user_ids':[ZERO],'managed_runtime_ids':[],'managed_agent_ids':[],'managed_squad_ids':[],'internal_skill_ids':[],'public_skill_ids':[],'public_attachment_ids':[],'private_strings':[v['CODER_BIND_IP'],v['MULTICA_BIND_IP'],'coder-job@','project_key']})
    put('leader-routing.json',{'workspace_id':ZERO,'squad_id':ZERO,'leader_id':ZERO,'reviewer_ids':[]})
    # Only edge is published. Backend/DB/runtime have no host port or Docker socket.
    services={
      'postgres':{'image':v['MULTICA_PG_IMAGE'],'restart':'unless-stopped','environment':{'POSTGRES_DB':'multica','POSTGRES_USER':'multica','POSTGRES_PASSWORD':'${MULTICA_DB_PASSWORD:?required}'},'volumes':['postgres:/var/lib/postgresql/data'],'networks':['database'],'cpus':.5,'mem_limit':'512m','pids_limit':128,'healthcheck':{'test':['CMD-SHELL','pg_isready -U multica -d multica'],'interval':'5s','timeout':'5s','retries':12}},
      'backend':{'image':site+'-multica-backend:'+v['MULTICA_SOURCE_REV'][:12],'restart':'unless-stopped','depends_on':{'postgres':{'condition':'service_healthy'}},'environment':{'DATABASE_URL':'postgres://multica:${MULTICA_DB_PASSWORD}@postgres:5432/multica?sslmode=disable','PORT':'8080','JWT_SECRET':'${MULTICA_JWT_SECRET:?required}','APP_ENV':'production','MULTICA_APP_URL':'https://'+v['MULTICA_DOMAIN'],'FRONTEND_ORIGIN':'https://'+v['MULTICA_DOMAIN'],'ALLOW_SIGNUP':'true','ALLOWED_EMAILS':v['MULTICA_OPERATOR_EMAIL'],'DISABLE_WORKSPACE_CREATION':'true','WORKSPACE_CREATION_ALLOWED_EMAILS':v['MULTICA_OPERATOR_EMAIL'],'SMTP_HOST':v['MULTICA_SMTP_HOST'],'SMTP_PORT':v['MULTICA_SMTP_PORT'],'SMTP_USERNAME':v['MULTICA_SMTP_USER'],'SMTP_PASSWORD':'${MULTICA_SMTP_PASSWORD:-}','SMTP_FROM_EMAIL':v['MULTICA_SMTP_FROM'],'SMTP_TLS':v['MULTICA_SMTP_TLS'],'MULTICA_PROVIDER_PRIVACY_CONFIG':'/app/provider-privacy.json','MULTICA_LEADER_ROUTING_CONFIG':'/app/leader-routing.json'},'volumes':['uploads:/app/data/uploads','./provider-privacy.json:/app/provider-privacy.json:ro','./leader-routing.json:/app/leader-routing.json:ro'],'networks':['database','control'],'cpus':1,'mem_limit':'768m','pids_limit':256},
      'frontend':{'image':site+'-multica-web:'+v['MULTICA_SOURCE_REV'][:12],'restart':'unless-stopped','environment':{'REMOTE_API_URL':'http://backend:8080','HOSTNAME':'0.0.0.0','PORT':'3000'},'depends_on':['backend'],'networks':['control'],'cpus':1,'mem_limit':'768m','pids_limit':128},
      'edge':{'image':v['MULTICA_EDGE_IMAGE'],'restart':'unless-stopped','ports':[v['MULTICA_BIND_IP']+':'+v['MULTICA_HTTP_PORT']+':8080'],'volumes':['./nginx.conf:/etc/nginx/conf.d/default.conf:ro'],'depends_on':['frontend','backend'],'networks':['control'],'cpus':.5,'mem_limit':'128m','pids_limit':64}}
    for s in services.values():s['logging']=log;s['security_opt']=['no-new-privileges:true']
    put('compose.json',{'name':site+'-multica','services':services,'volumes':{'postgres':{},'uploads':{}},'networks':{'database':{'internal':True},'control':{'name':site+'-multica-control'}}})
    put('nginx.conf','''map $http_upgrade $connection_upgrade { default upgrade; '' close; }
server {
 listen 8080;
 client_max_body_size 16m;
 location /api/ { proxy_pass http://backend:8080; proxy_http_version 1.1; proxy_buffering off; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection $connection_upgrade; proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto; proxy_read_timeout 3600; }
 location /health { proxy_pass http://backend:8080; }
 location /ws { proxy_pass http://backend:8080; proxy_http_version 1.1; proxy_buffering off; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection $connection_upgrade; proxy_read_timeout 3600; }
 location / { proxy_pass http://frontend:3000; proxy_http_version 1.1; proxy_set_header Host $host; }
}
''')
    put('Runtime.Dockerfile','''FROM golang:1.27.3-alpine AS cli
WORKDIR /src/server
COPY server/go.mod server/go.sum ./
RUN go mod download
COPY server/ ./
RUN CGO_ENABLED=0 go build -p 2 -o /out/multica ./cmd/multica
FROM node:22.23.4-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 openssh-client git ca-certificates && rm -rf /var/lib/apt/lists/*
ARG CODEX_VERSION
RUN npm install -g --no-audit --no-fund @openai/codex@${CODEX_VERSION}
COPY --from=cli /out/multica /usr/local/bin/multica
COPY runtime/coder-project /usr/local/bin/coder-project
COPY runtime/runtime_setup.py runtime/runtime_entry.py /opt/runtime/
COPY runtime/optional-bin/ /usr/local/bin/
RUN mkdir -p /home/runtime/.ssh /home/runtime/.multica /home/runtime/.config/coder-project /work/tasks /capability && chown -R 1000:1000 /home/runtime /work /capability && chmod 755 /usr/local/bin/coder-project && chmod 644 /opt/runtime/*.py && chmod 700 /home/runtime /home/runtime/.ssh /home/runtime/.multica
USER 1000:1000
ENV HOME=/home/runtime MULTICA_DAEMON_AUTO_UPDATE=false MULTICA_DAEMON_AUTO_RELOAD=false
WORKDIR /work
ENTRYPOINT ["python3","/opt/runtime/runtime_entry.py"]
CMD ["daemon"]
''')
    put('AGENTS.md','''# Project development through Coder
The canonical application source is on the linked Coder workspace. Use `coder-project list .`, `coder-project read PATH`, `coder-project write PATH < file`, and `coder-project exec -- COMMAND ...`. Run builds/tests through `coder-dev job` inside that project. Start its managed preview with `coder-project preview`; verify the returned public HTTPS URL and behavior before handoff. Runtime task folders are orchestration scratch space, not the deployed application. Never request host SSH, Docker sockets, provider credentials, another project's files or internal instructions. Report a stopped/unlinked workspace as a platform blocker. Preserve important Security and QA checks.
''')
    return {'status':'multica_rendered','private_bootstrap':True,'public_customer_access_ready':False}
