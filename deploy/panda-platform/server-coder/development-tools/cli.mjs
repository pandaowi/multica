import {readFile, writeFile, mkdir, lstat, realpath, open, rename} from 'node:fs/promises';
import {homedir} from 'node:os';
import {resolve, join, dirname, basename, relative} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createHash, randomBytes} from 'node:crypto';
import {createServer} from 'node:net';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import pg from 'pg';
import {chromium} from 'playwright-core';
import {trackJob} from './jobs.mjs';

process.umask(0o077);
const installation = dirname(fileURLToPath(import.meta.url));
const fail = code => {const e=new Error(code);e.safeCode=code;throw e;};
const output = obj => console.log(JSON.stringify(obj));
const exists = p => lstat(p).then(()=>true,()=>false);
async function privateDir(p) {
  await mkdir(p,{recursive:true,mode:0o700});
  const st=await lstat(p);
  if(st.isSymbolicLink() || !st.isDirectory() || st.uid!==process.getuid() || (st.mode&0o077)) fail('UNSAFE_PRIVATE_DIRECTORY');
}
async function readPrivate(p) {
  const f=await open(p, (await import('node:fs')).constants.O_RDONLY | (await import('node:fs')).constants.O_NOFOLLOW);
  try {const st=await f.stat();if(!st.isFile() || st.uid!==process.getuid() || (st.mode&0o077)) fail('UNSAFE_PRIVATE_FILE');return JSON.parse(await f.readFile('utf8'));} finally {await f.close();}
}
async function savePrivate(p,obj) {
  const tmp=p+'.'+randomBytes(8).toString('hex')+'.tmp';
  await writeFile(tmp,JSON.stringify(obj),{mode:0o600,flag:'wx'});await rename(tmp,p);
}
async function context() {
  const root=await realpath(join(homedir(),'projects'));const project=await realpath(process.cwd());
  if(dirname(project)!==root) fail('PROJECT_ROOT_REQUIRED');
  const id=createHash('sha256').update(project).digest('hex').slice(0,16);
  const base=join(homedir(),'.local/state/coder-development');await privateDir(base);
  const state=join(base,id);await privateDir(state);
  return {project,id,state};
}
function profile(args) {
  const i=args.indexOf('--name');const name=i<0?'qa':args[i+1];
  if(!name || !/^[a-z][a-z0-9-]{0,31}$/.test(name)) fail('INVALID_ACCOUNT_PROFILE');
  if(i>=0) args.splice(i,2);return name;
}
const argvAfter = args => {const i=args.indexOf('--');if(i<0||!args[i+1])fail('COMMAND_REQUIRED');return args.slice(i+1);};
async function account(ctx,name,create=false) {
  const dir=join(ctx.state,'accounts');await privateDir(dir);const path=join(dir,name+'.json');
  if(create && !await exists(path)) {
    // Exclusive create prevents a concurrent provision from replacing credentials.
    try {await writeFile(path,JSON.stringify({email:name+'+'+ctx.id+'@example.test',password:randomBytes(24).toString('base64url'),provisioned:false}),{flag:'wx',mode:0o600});}
    catch(e) {if(e.code!=='EEXIST')throw e;}
  }
  const value=await readPrivate(path);
  if(typeof value.email!=='string'||typeof value.password!=='string'||Buffer.byteLength(value.password)>72)fail('INVALID_ACCOUNT');
  return {path,value};
}
async function databaseConfig(ctx,create=false) {
  const p=join(ctx.state,'database.json');const data=join(ctx.state,'postgres');
  if(create&&!await exists(p)) {
    if(await exists(data))fail('DATABASE_CONFIG_MISSING');
    const port=await new Promise((ok,no)=>{const s=createServer();s.on('error',no);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>ok(p));});});
    await writeFile(p,JSON.stringify({port,adminPassword:randomBytes(32).toString('hex'),appPassword:randomBytes(32).toString('hex')}),{flag:'wx',mode:0o600});
  }
  const c=await readPrivate(p);
  if(!Number.isInteger(c.port)||c.port<1024||c.port>65535||!/^([a-f0-9]{64})$/.test(c.adminPassword)||!/^([a-f0-9]{64})$/.test(c.appPassword))fail('INVALID_DATABASE_CONFIG');
  return {...c,data,user:'app_'+ctx.id,database:'app_'+ctx.id};
}
async function appEnv(ctx) {
  const c=await databaseConfig(ctx);const u=new URL('postgresql://127.0.0.1');
  u.port=''+c.port;u.username=c.user;u.password=c.appPassword;u.pathname='/'+c.database;
  const env={...process.env,PATH:join(homedir(),'.local/bin')+':'+process.env.PATH,DATABASE_URL:u.href};
  // coder-preview writes the manifest before starting its managed command.
  if(!env.APP_ORIGIN && await exists(join(ctx.project,'.coder-preview.json'))) {
    const m=JSON.parse(await readFile(join(ctx.project,'.coder-preview.json'),'utf8'));
    if(!/^preview-[a-z0-9-]+$/.test(m.name))fail('INVALID_PREVIEW_MANIFEST');
    env.APP_ORIGIN='https://'+m.name+'.coder.example.com';
  }
  return env;
}
function childRun(command,env,redact=null) {
  const child=spawn(command[0],command.slice(1),{env,stdio:redact?['inherit','pipe','pipe']:'inherit',detached:true});
  if(redact)for(const [stream,destination] of [[child.stdout,process.stdout],[child.stderr,process.stderr]])createInterface({input:stream}).on('line',line=>destination.write(redact(line)+'\n'));
  const completion=new Promise((ok,no)=>{child.on('error',no);child.on('exit',(c,s)=>ok(c??(s?1:0)));});
  return {child,completion};
}
async function terminate(run,signalGroup=true) {
  if(!run||run.child.exitCode!==null||run.child.signalCode!==null)return;
  const signal=(s,group=signalGroup)=>{try{process.kill(group?-run.child.pid:run.child.pid,s);}catch(e){if(e.code!=='ESRCH')throw e;}};
  signal('SIGTERM');let timer;
  await Promise.race([run.completion,new Promise(r=>{timer=setTimeout(()=>{signal('SIGKILL',true);r();},signalGroup?10000:20000);})]);clearTimeout(timer);
}
async function execute(command,env,redact=null,signalGroup=true) {
  const run=childRun(command,env,redact);const stop=()=>void terminate(run,signalGroup);
  process.once('SIGTERM',stop);process.once('SIGINT',stop);
  try{return await run.completion;}finally {process.off('SIGTERM',stop);process.off('SIGINT',stop);}
}
async function postgres(ctx,command) {
  // This CLI owns app-before-database shutdown. The pinned library otherwise
  // races its own signal hook against ours and forces exit 143/130 early.
  const signals=['SIGTERM','SIGINT'];
  const before=new Map(signals.map(s=>[s,new Set(process.listeners(s))]));
  const {default:EmbeddedPostgres}=await import('embedded-postgres');
  const {gracefulExit}=await import('exit-hook');
  for(const signal of signals)for(const listener of process.listeners(signal))
    if(!before.get(signal).has(listener))process.removeListener(signal,listener);
  const c=await databaseConfig(ctx,true);
  const db=new EmbeddedPostgres({databaseDir:c.data,port:c.port,user:'postgres',password:c.adminPassword,
    authMethod:'scram-sha-256',persistent:true,createPostgresUser:false,
    postgresFlags:['-c','listen_addresses=127.0.0.1','-c','unix_socket_directories=','-c','max_connections=20','-c','shared_buffers=32MB'],onLog:()=>{},onError:()=>{}});
  let app,stopping=false;
  async function stop(code) {
    if(stopping)return;stopping=true;await terminate(app);
    if(db.process?.exitCode===null && db.process?.signalCode===null)await db.stop();
    else if(db.process)db.process=undefined;
    return code;
  }
  const onSignal=()=>void stop(0).then(()=>gracefulExit(0),()=>gracefulExit(1));
  process.once('SIGTERM',onSignal);process.once('SIGINT',onSignal);
  try {
    if(!await exists(join(c.data,'PG_VERSION'))) {
      if(await exists(c.data))fail('INCOMPLETE_DATABASE_DIRECTORY');await db.initialise();
    }
    await db.start();
    // Only this fresh project cluster's administrator is used for provisioning.
    const admin=new pg.Client({host:'127.0.0.1',port:c.port,user:'postgres',password:c.adminPassword,database:'postgres'});
    await admin.connect();
    try {
      if(!(await admin.query('SELECT 1 FROM pg_roles WHERE rolname=$1',[c.user])).rowCount)
        await admin.query('CREATE ROLE "'+c.user+'" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD \''+c.appPassword+'\'');
      if(!(await admin.query('SELECT 1 FROM pg_database WHERE datname=$1',[c.database])).rowCount)
        await admin.query('CREATE DATABASE "'+c.database+'" OWNER "'+c.user+'"');
    } finally {await admin.end();}
    db.process.once('exit',()=>{if(!stopping)void stop(1).then(()=>gracefulExit(1),()=>gracefulExit(1));});
    app=childRun(command,await appEnv(ctx));
    output({postgres:'ready',project:basename(ctx.project),scope:'development',public:false});
    return await stop(await app.completion);
  } finally {if(!stopping)await stop(1);process.off('SIGTERM',onSignal);process.off('SIGINT',onSignal);}
}
function browserEnv() {
  return {...process.env,LD_LIBRARY_PATH:join(installation,'browser-root/usr/lib/x86_64-linux-gnu'),FONTCONFIG_FILE:join(installation,'fonts.conf')};
}
async function launchBrowser() {return chromium.launch({headless:true,env:browserEnv()});}
async function browserDoctor() {
  const browser=await launchBrowser();
  try {
    for(const viewport of [{width:1280,height:800},{width:390,height:844}]) {
      const p=await browser.newPage({viewport});await p.setContent('<h1>QA Ready</h1><label>Email<input aria-label="Email"></label>');
      await p.getByLabel('Email').fill('synthetic@example.test');
      if(await p.getByLabel('Email').inputValue()!=='synthetic@example.test'||(await p.locator('h1').boundingBox())?.height<20)fail('BROWSER_RENDER_INPUT_FAILED');
      await p.close();
    }
  } finally {await browser.close();}
  return true;
}
async function qaRun(ctx,args) {
  const name=profile(args),script=args[0];if(!script)fail('QA_MODULE_REQUIRED');
  const file=await realpath(resolve(ctx.project,script));if(relative(ctx.project,file).startsWith('..'))fail('QA_MODULE_OUTSIDE_PROJECT');
  const a=await account(ctx,name);if(!a.value.provisioned)fail('QA_ACCOUNT_NOT_PROVISIONED');
  const artifactsDir=join(ctx.state,'qa-artifacts',Date.now().toString());await privateDir(artifactsDir);
  const redact=s=>String(s).replaceAll(a.value.password,'[REDACTED]').replaceAll(a.value.email,'[TEST_ACCOUNT]');
  const originals={};for(const key of ['log','warn','error']) {originals[key]=console[key];console[key]=(...v)=>originals[key](...v.map(x=>redact(typeof x==='string'?x:JSON.stringify(x))));}
  const browser=await launchBrowser();
  try {
    const module=await import(pathToFileURL(file).href);
    if(typeof module.default!=='function')fail('QA_DEFAULT_FUNCTION_REQUIRED');
    await module.default({browser,artifactsDir,getTestAccount:async()=>({email:a.value.email,password:a.value.password}),previewUrl:(await appEnv(ctx)).APP_ORIGIN});
    output({qa:'passed',profile:name});
  } catch(e) {throw Object.assign(new Error('QA_FAILED'),{safeCode:'QA_FAILED'});}
  finally {await browser.close();for(const key in originals)console[key]=originals[key];}
}
async function main(args) {
  const command=args.shift();
  if(command==='version')return output({version:'1.1.0'});
  if(command==='browser'&&args[0]==='doctor') {await browserDoctor();return output({browser:'ready',desktop:true,mobile_emulation:true,render_and_input:true});}
  const ctx=await context();
  if(command==='postgres') {
    // flock owns only this project's lifecycle; env/QA commands remain concurrent.
    return await execute(['flock','--nonblock','--no-fork',join(ctx.state,'runtime.lock'),process.execPath,fileURLToPath(import.meta.url),'_postgres',...argvAfter(args)],process.env,null,false);
  }
  if(command==='_postgres')return await postgres(ctx,args);
  if(command==='env')return await execute(argvAfter(args),await appEnv(ctx));
  if(command==='account') {
    const mode=args.shift(),name=profile(args);
    const a=await account(ctx,name,mode==='provision');
    if(mode==='status')return output({profile:name,provisioned:a.value.provisioned===true});
    if(mode!=='provision'&&mode!=='run')fail('INVALID_ACCOUNT_COMMAND');
    if(mode==='run'&&!a.value.provisioned)fail('QA_ACCOUNT_NOT_PROVISIONED');
    const code=await execute(argvAfter(args),{...await appEnv(ctx),TEST_EMAIL:a.value.email,TEST_PASSWORD:a.value.password,QA_ACCOUNT_NAME:name},text=>text.replaceAll(a.value.password,'[REDACTED]').replaceAll(a.value.email,'[TEST_ACCOUNT]'));
    if(code===0&&mode==='provision')await savePrivate(a.path,{...a.value,provisioned:true});
    if(code===0)output({profile:name,provisioned:true});return code;
  }
  if(command==='qa'&&args.shift()==='run')return await trackJob(ctx,'qa',()=>qaRun(ctx,args));
  if(command==='job'){
    const k=args.indexOf('--kind');if(k<0||!args[k+1])fail('JOB_KIND_REQUIRED');
    const kind=args[k+1];return await trackJob(ctx,kind,()=>execute(argvAfter(args),process.env));
  }
  if(command==='ready') {
    await browserDoctor();const c=await databaseConfig(ctx),a=await account(ctx,'qa');
    if(!a.value.provisioned)fail('QA_ACCOUNT_NOT_PROVISIONED');
    const env=await appEnv(ctx);const client=new pg.Client({connectionString:env.DATABASE_URL});await client.connect();
    try {await client.query('SELECT 1');}finally {await client.end();}
    output({project:basename(ctx.project),postgres:'ready',browser:'ready',qa_profile:'qa',account:'provisioned',authentication_verified:false});return;
  }
  fail('UNKNOWN_COMMAND');
}
try {const code=await main(process.argv.slice(2));if(Number.isInteger(code))process.exitCode=code;}
catch(e) {console.error(JSON.stringify({ok:false,code:e.safeCode||'OPERATION_FAILED'}));process.exitCode=1;}
