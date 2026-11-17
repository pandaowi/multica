import pg from 'pg';
const c=new pg.Client({connectionString:process.env.DATABASE_URL,connectionTimeoutMillis:1500});
try{await c.connect();await c.end();throw new Error('Database listener survived launcher exit');}catch(e){if(e.code!=='ECONNREFUSED')throw e;}
console.log(JSON.stringify({database_stopped_with_launcher:true}));
