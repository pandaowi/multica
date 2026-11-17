import pg from 'pg';
import {createHash} from 'node:crypto';
const client=new pg.Client({connectionString:process.env.DATABASE_URL});await client.connect();
try {
 await client.query('create table if not exists fixture_accounts(email text primary key,fingerprint text not null)');
 const fingerprint=createHash('sha256').update(process.env.TEST_PASSWORD).digest('hex');
 await client.query('insert into fixture_accounts values($1,$2) on conflict do nothing',[process.env.TEST_EMAIL,fingerprint]);
 if((await client.query('select fingerprint from fixture_accounts where email=$1',[process.env.TEST_EMAIL])).rows[0].fingerprint!==fingerprint)throw Error('conflicting fixture account');
 // Deliberate leak probe: the caller verifies both values are redacted.
 console.log('seed '+process.env.TEST_EMAIL+' '+process.env.TEST_PASSWORD);
} finally {await client.end();}
