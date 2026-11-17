// Operational attribution only; labels do not confer tenant or API authority.
import {mkdir,writeFile,rename,readdir,lstat,unlink,readFile} from 'node:fs/promises';
import {homedir} from 'node:os';
import {join,basename} from 'node:path';
import {randomUUID} from 'node:crypto';
const uuid=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const kinds=new Set(['build','qa','test','migration','seed','other']);
export async function trackJob(ctx,kind,action) {
 if(!kinds.has(kind))throw Object.assign(Error('INVALID_JOB_KIND'),{safeCode:'INVALID_JOB_KIND'});
 const dir=join(homedir(),'.local/state/coder-observability/jobs');
 await mkdir(dir,{recursive:true,mode:0o700});
 const st=await lstat(dir);if(st.isSymbolicLink()||st.uid!==process.getuid()||(st.mode&0o077))throw Error('UNSAFE_JOB_DIRECTORY');
 const raw=await readFile('/proc/self/stat','utf8');const start=raw.slice(raw.lastIndexOf(')')+2).split(' ')[19];
 const boot=(await readFile('/proc/sys/kernel/random/boot_id','utf8')).trim();
 const id=randomUUID();const path=join(dir,id+'.json');
 const record={version:1,job_id:id,project:basename(ctx.project),project_key:ctx.id,kind,
  pid:process.pid,identity:boot+':'+start,state:'running',started_at:Date.now()/1000};
 const hint=process.env.MULTICA_TASK_ID;
 if(hint&&uuid.test(hint)){record.reported_task_id=hint;record.task_id_verified=false;}
 async function save(){const tmp=path+'.tmp';await writeFile(tmp,JSON.stringify(record),{mode:0o600});await rename(tmp,path);}
 await save();const previous=process.env.CODER_DEV_JOB_ID;process.env.CODER_DEV_JOB_ID=id;
 try {
  const code=await action();record.state=code===undefined||code===0?'completed':'failed';record.exit_code=code??0;return code;
 } catch(error){record.state='failed';record.exit_code=1;throw error;}
 finally {
  record.finished_at=Date.now()/1000;await save();
  if(previous===undefined)delete process.env.CODER_DEV_JOB_ID;else process.env.CODER_DEV_JOB_ID=previous;
  // Retain metadata for seven days; never inspect or store command/environment data.
  for(const name of await readdir(dir))if(uuid.test(name.slice(0,-5))&&name.endsWith('.json')){
   const p=join(dir,name);const s=await lstat(p);
   if(s.isFile()&&!s.isSymbolicLink()&&Date.now()-s.mtimeMs>7*86400000)await unlink(p);
  }
 }
}
