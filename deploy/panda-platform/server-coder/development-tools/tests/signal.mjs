import {spawn} from 'node:child_process';
import pg from 'pg';
const child=spawn('coder-dev',['postgres','--','node','hold.mjs'],{env:process.env,stdio:['ignore','pipe','pipe']});
await new Promise((ok,no)=>{const t=setTimeout(()=>no(Error('launcher readiness timeout')),15000);child.stdout.on('data',b=>{if(b.toString().includes('HOLD_READY')){clearTimeout(t);ok();}});child.on('error',no);});
const exit=new Promise(ok=>child.once('exit',(code,signal)=>ok({code,signal})));child.kill('SIGTERM');
const result=await exit;
const c=new pg.Client({connectionString:process.env.DATABASE_URL,connectionTimeoutMillis:1500});
try{await c.connect();await c.end();throw Error('listener survived signal');}catch(e){if(e.code!=='ECONNREFUSED')throw e;}
if(result.code!==0){console.log(JSON.stringify({signal_result:result}));throw Error('launcher signal exit not clean');}
console.log(JSON.stringify({sigterm_stops_app_and_postgres:true,exit_code:result.code}));
