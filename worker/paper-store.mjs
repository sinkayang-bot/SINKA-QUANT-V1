import {DatabaseSync} from 'node:sqlite';
import {mkdirSync,chmodSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {restoreAccount} from '../strategy/account-checkpoint.mjs';
const LIMIT=8*1024*1024,TTL=30000;
export function openPaperStore(path,{symbol,now=Date.now,owner=randomUUID()}={}){
  const filename=resolve(path);mkdirSync(dirname(filename),{recursive:true,mode:0o700});
  const db=new DatabaseSync(filename);chmodSync(filename,0o600);
  let closed=false,revision=0,lastTime=now();
  db.exec('PRAGMA busy_timeout=2000; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;');
  const schema=db.prepare('PRAGMA user_version').get().user_version;
  if(schema!==0&&schema!==1){db.close();throw Error('Unsupported paper database version');}
  try{
    if(schema===0){
      const tables=db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all();
      if(tables.length)throw Error('Unrecognized paper database');
      db.exec(`BEGIN IMMEDIATE;
        CREATE TABLE lease(id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT NOT NULL,expires INTEGER NOT NULL);
        CREATE TABLE account(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,payload TEXT NOT NULL,checksum TEXT NOT NULL,updated INTEGER NOT NULL);
        PRAGMA user_version=1;COMMIT;`);
    }
    transaction(()=>{
      const time=clock(),lease=db.prepare('SELECT * FROM lease WHERE id=1').get();
      if(lease&&lease.expires>time)throw Error('Paper worker lease is held');
      db.prepare('INSERT INTO lease(id,owner,expires) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,expires=excluded.expires').run(owner,time+TTL);
    });
  }catch(e){db.close();throw e;}
  function clock(){const value=now();if(!Number.isSafeInteger(value)||value<lastTime)throw Error('Paper worker clock moved backwards');lastTime=value;return value;}
  function transaction(fn){db.exec('BEGIN IMMEDIATE');try{const result=fn();db.exec('COMMIT');return result;}catch(e){db.exec('ROLLBACK');throw e;}}
  function owned(time){const lease=db.prepare('SELECT * FROM lease WHERE id=1').get();if(!lease||lease.owner!==owner||lease.expires<=time)throw Error('Paper worker lease lost');}
  const checksum=payload=>createHash('sha256').update(payload).digest('hex');
  function load(){return transaction(()=>{
    owned(clock());const row=db.prepare('SELECT * FROM account WHERE id=1').get();
    if(!row){revision=0;return null;}
    if(row.payload.length>LIMIT||checksum(row.payload)!==row.checksum)throw Error('Paper checkpoint checksum mismatch');
    const clean=restoreAccount(JSON.parse(row.payload),symbol);revision=row.revision;return clean;
  });}
  function save(account){
    const clean=restoreAccount(account,symbol),payload=JSON.stringify(clean);if(payload.length>LIMIT)throw Error('Paper checkpoint is too large');
    const next=transaction(()=>{
      const time=clock();owned(time);const row=db.prepare('SELECT revision,checksum FROM account WHERE id=1').get();
      if((row?.revision??0)!==revision)throw Error('Paper checkpoint revision changed');
      const hash=checksum(payload);
      const nextRevision=revision+(!row||hash!==row.checksum?1:0);
      if(!row||hash!==row.checksum){
        db.prepare('INSERT INTO account VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,checksum=excluded.checksum,updated=excluded.updated').run(nextRevision,payload,hash,time);
      }
      db.prepare('UPDATE lease SET expires=? WHERE id=1 AND owner=?').run(time+TTL,owner);
      return nextRevision;
    });
    revision=next;return revision;
  }
  function renew(){return transaction(()=>{const time=clock();owned(time);db.prepare('UPDATE lease SET expires=? WHERE id=1 AND owner=?').run(time+TTL,owner);});}
  function close(){if(closed)return;try{db.prepare('DELETE FROM lease WHERE id=1 AND owner=?').run(owner);}finally{db.close();closed=true;}}
  return {load,save,renew,close,get revision(){return revision;}};
}
