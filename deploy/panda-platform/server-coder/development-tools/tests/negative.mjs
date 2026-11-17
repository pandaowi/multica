import assert from 'node:assert/strict';
import {rename,symlink,unlink,readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
const id=createHash('sha256').update(process.cwd()).digest('hex').slice(0,16), state=join(homedir(),'.local/state/coder-development',id), path=join(state,'accounts/qa.json');
await rename(path,path+'.saved');
try{await symlink(path+'.saved',path);assert.notEqual(spawnSync('coder-dev',['account','status'],{env:process.env,timeout:5000}).status,0);}finally{await unlink(path);await rename(path+'.saved',path);}
assert.notEqual(spawnSync('coder-dev',['account','status'],{cwd:homedir(),env:process.env,timeout:5000}).status,0);
const before=await readFile(join(state,'postgres/PG_VERSION'),'utf8');
await rename(join(state,'database.json'),join(state,'database.json.saved'));
try{assert.notEqual(spawnSync('coder-dev',['env','--','node','-e','process.exit(0)'],{env:process.env,timeout:5000}).status,0);}finally{await rename(join(state,'database.json.saved'),join(state,'database.json'));}
assert.equal(await readFile(join(state,'postgres/PG_VERSION'),'utf8'),before);
console.log(JSON.stringify({account_symlink_denied:true,outside_project_denied:true,missing_config_fails_closed_without_data_reset:true}));
