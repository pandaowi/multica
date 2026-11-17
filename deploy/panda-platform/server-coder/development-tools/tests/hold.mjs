import pg from 'pg';
const c=new pg.Client({connectionString:process.env.DATABASE_URL});await c.connect();
process.on('SIGTERM',()=>void c.end().then(()=>process.exit(0)));
console.log('HOLD_READY');setInterval(()=>{},1000);
