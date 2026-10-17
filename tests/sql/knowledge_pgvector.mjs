// SPDX-License-Identifier: GPL-3.0-or-later
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url);
const { PGlite }=require('@electric-sql/pglite');
const { vector }=require('@electric-sql/pglite-pgvector');
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const pg = await PGlite.create({extensions: {vector}});
const sql = await fs.readFile(new URL('../../alembic/sql/core_0003.sql',import.meta.url),'utf8');
const f = JSON.parse(await fs.readFile(new URL('../../.cache/rag/pgvector-sql-fixture.json',import.meta.url),'utf8'));
let count=0;
const pass=(name)=>{count++;console.log(`PASS ${name}`)};
async function rejects(query,params=[]) {
  let failed=false;
  try {await pg.query(query,params)} catch {failed=true}
  assert.equal(failed,true);
}
function bound(sql,params) {
  const names=[];
  const query=sql.replace(/(?<!:):([a-zA-Z_][a-zA-Z_0-9]*)/g,(_,name)=>{
    if(!names.includes(name))names.push(name);
    return '$'+(names.indexOf(name)+1);
  });
  return pg.query(query,names.map(name=>params[name]));
}
try {
 await pg.exec('CREATE ROLE anon; CREATE ROLE authenticated;');
 await pg.exec('BEGIN;'+sql+'COMMIT;');pass('migration creates pgvector Core schema');
 await pg.exec('BEGIN;'+sql+'COMMIT;');pass('adopts compatible existing tables without reset');
 assert.equal((await pg.query("SELECT count(*)::int AS n FROM information_schema.columns WHERE table_schema='core'")).rows[0].n,37);
 pass('three tables have 37 expected columns');
 await pg.query("INSERT INTO core.documents(document_id,document_kind,subject_type,subject_id,title,scope_type,owner_subject) VALUES($1,'supplier_review','supplier',$2,'Demo','global','operator://test')",[f.document_id,f.supplier_id]);
 await rejects("INSERT INTO core.documents(document_kind,subject_type,subject_id,title,scope_type,owner_subject) VALUES('supplier_review','supplier',$1,'Duplicate','global','operator://test')",[f.supplier_id]);pass('one active supplier profile enforced');
 await pg.query("INSERT INTO core.document_versions(version_id,document_id,version_no,title_snapshot,content_md,checksum_sha256,status,metadata,created_by_subject) VALUES($1,$2,1,'Demo',$3,$4,'published',$5,'operator://test')",[f.version_id,f.document_id,f.content,f.checksum,f.metadata]);
 const v=(await pg.query('SELECT * FROM core.document_versions WHERE version_id=$1',[f.version_id])).rows[0];
 assert(v.published_at && v.known_from);pass('publication sets server availability timestamps');
 await rejects("UPDATE core.document_versions SET content_md='forged' WHERE version_id=$1",[f.version_id]);
 await rejects('DELETE FROM core.document_versions WHERE version_id=$1',[f.version_id]);pass('published source cannot be edited or deleted');
 await rejects("INSERT INTO core.document_versions(document_id,version_no,title_snapshot,content_md,checksum_sha256,created_by_subject) VALUES($1,2,'Bad hash','forged',$2,'operator://test')",[f.document_id,'0'.repeat(64)]);pass('checksum validates actual UTF-8 content');
 const insert=f.queries.find(q=>q.includes('INSERT INTO core.document_chunks'));
 assert(insert);
 await pg.exec('BEGIN;');
 for(const c of f.chunks)await bound(insert,c);
 await pg.exec('COMMIT;');pass('application INSERT stores exact UTF-8 spans and normalized vectors');
 const params={operator:true,department:null,supplier:f.supplier_id};
 assert.equal((await bound(f.source_sql,params)).rows.length,1);pass('application source SELECT binds optional supplier and scope');
 const reads=f.queries.find(q=>q.includes('FROM core.document_chunks')&&q.includes('ORDER BY version_id,chunk_no'));
 const common={versions:[f.version_id],generations:[f.generation],profile:f.profile};
 assert.equal((await bound(reads,common)).rows.length,f.chunks.length);pass('application chunk SELECT binds UUID and generation arrays');
 const dense=f.queries.find(q=>q.includes('OPERATOR(extensions.<=>)'));
 assert.equal((await bound(dense,{...common,embedding:f.chunks[0].embedding})).rows.length,f.chunks.length);pass('application dense cosine query executes');
 await pg.query("UPDATE core.documents SET scope_type='department',department_id=$1 WHERE document_id=$2",['33333333-3333-4333-8333-333333333333',f.document_id]);
 assert.equal((await bound(f.source_sql,{...params,operator:false,department:'44444444-4444-4444-8444-444444444444'})).rows.length,0);pass('department filter excludes other departments');
 await pg.query("UPDATE core.documents SET scope_type='global',department_id=NULL WHERE document_id=$1",[f.document_id]);
 await pg.exec('BEGIN;');
 await pg.query('DELETE FROM core.document_chunks WHERE version_id=$1',[f.version_id]);
 await rejects(insert.replace(/(?<!:):([a-zA-Z_][a-zA-Z_0-9]*)/g,'NULL'));
 await pg.exec('ROLLBACK;');
 assert.equal((await bound(reads,common)).rows.length,f.chunks.length);pass('failed replacement rolls back and retains complete generation');
 const bad={...f.chunks[0],id:'55555555-5555-4555-8555-555555555555',number:20,start:1};
 let failed=false;try{await bound(insert,bad)}catch{failed=true}assert(failed);pass('incorrect source byte spans rejected by DB trigger');
 await pg.exec('SET ROLE anon;');
 await rejects('SELECT content_md FROM core.document_versions');
 await pg.exec('RESET ROLE;');pass('anonymous role cannot read Core sources');
 await pg.query("UPDATE core.document_versions SET status='withdrawn' WHERE version_id=$1",[f.version_id]);
 assert((await pg.query('SELECT withdrawn_at FROM core.document_versions WHERE version_id=$1',[f.version_id])).rows[0].withdrawn_at);
 await rejects("UPDATE core.document_versions SET status='published' WHERE version_id=$1",[f.version_id]);pass('withdrawal is timestamped and cannot republish immutable version');
 const evidence={engine:'PGlite 0.5.10 / PostgreSQL WASM + real pgvector',driver:'PGlite direct SQL; not asyncpg/Supabase E2E',embedding:'controlled normalized 1024-dimensional basis vectors; not a retrieval quality benchmark',passed:count,tables:3,columns:37,chunks:f.chunks.length};
 await fs.writeFile(new URL('../../.cache/rag/pgvector-sql-results.json',import.meta.url),JSON.stringify(evidence,null,2));
 console.log(JSON.stringify(evidence));
} finally {await pg.close()}
