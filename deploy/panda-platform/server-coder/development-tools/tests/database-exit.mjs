import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {homedir} from 'node:os';
import {join} from 'node:path';
import pg from 'pg';
const child=spawn('coder-dev',['postgres','--','node','hold.mjs'],{env:process.env,stdio:['ignore','pipe','ignore']});
await new Promise((ok,no)=>{const t=setTimeout(()=>no(Error('launcher readiness timeout')),15000);child.stdout.on('data',b=>{if(b.toString().includes('HOLD_READY')){clearTimeout(t);ok();}});child.on('error',no);});
const id=createHash('sha256').update(process.cwd()).digest('hex').slice(0,16), data=join(homedir(),'.local/state/coder-development',id,'postgres');
const pid=Number((await readFile(join(data,'postmaster.pid'),'utf8')).split('\n')[0]);
assert.ok((await readFile('/proc/'+pid+'/cmdline','utf8')).includes(data));
const exit=new Promise(ok=>child.once('exit',code=>ok(code)));process.kill(pid,'SIGINT');
let timer;const code=await Promise.race([exit,new Promise((_,no)=>{timer=setTimeout(()=>no(Error('database-exit cleanup timeout')),15000);})]);clearTimeout(timer);assert.notEqual(code,0);
const c=new pg.Client({connectionString:process.env.DATABASE_URL,connectionTimeoutMillis:1500});
try{await c.connect();await c.end();throw Error('listener survived database exit');}catch(e){if(e.code!=='ECONNREFUSED')throw e;}
console.log(JSON.stringify({unexpected_database_exit_stops_launcher:true,exit_code:code,listener_gone:true}));
