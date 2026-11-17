import assert from 'node:assert/strict';
import pg from 'pg';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,chmod} from 'node:fs/promises';
import {homedir} from 'node:os';
import {join} from 'node:path';
const client=new pg.Client({connectionString:process.env.DATABASE_URL});await client.connect();
try {
 const role=(await client.query('select rolsuper,rolcreatedb,rolcreaterole from pg_roles where rolname=current_user')).rows[0];
 assert.deepEqual(role,{rolsuper:false,rolcreatedb:false,rolcreaterole:false});
 assert.equal((await client.query('show listen_addresses')).rows[0].listen_addresses,'127.0.0.1');
 await client.query('create table if not exists bootstrap_counter(n int not null)');
 await client.query('insert into bootstrap_counter values (1)');
 const count=Number((await client.query('select count(*) n from bootstrap_counter')).rows[0].n);
 const run=(args,ok=true)=>{const r=spawnSync('coder-dev',args,{encoding:'utf8',env:process.env,timeout:45000});assert.equal(r.status===0,ok,'unexpected helper status');return r.stdout+r.stderr;};
 assert.notEqual(spawnSync('coder-dev',['postgres','--','node','-e','process.exit(0)'],{env:process.env,timeout:5000}).status,0,'duplicate launcher must be refused');
 const output=run(['account','provision','--','node','seed.mjs']);assert.match(output,/\[REDACTED\]/);assert.match(output,/\[TEST_ACCOUNT\]/);
 const id=createHash('sha256').update(process.cwd()).digest('hex').slice(0,16), path=join(homedir(),'.local/state/coder-development',id,'accounts/qa.json');
 const a=JSON.parse(await readFile(path,'utf8'));assert.ok(a.provisioned);assert.ok(!output.includes(a.password));assert.ok(!output.includes(a.email));
 assert.equal((await client.query('select fingerprint from fixture_accounts where email=$1',[a.email])).rows[0].fingerprint,createHash('sha256').update(a.password).digest('hex'));
 await chmod(path,0o644);try{run(['account','status'],false);}finally{await chmod(path,0o600);}
 run(['ready']);run(['qa','run','qa.mjs']);
 console.log(JSON.stringify({postgres:true,least_privilege_role:true,loopback_only:true,exclusive_runtime:true,private_account_reused:true,redacted_child_output:true,unsafe_permissions_denied:true,browser_and_account_handoff:true,persisted_start_count:count}));
} finally {await client.end();}
