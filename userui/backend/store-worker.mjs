import { parentPort, workerData } from 'node:worker_threads';
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomBytes, randomUUID, scryptSync, timingSafeEqual, createHash } from 'node:crypto';

if (workerData.path !== ':memory:') mkdirSync(dirname(workerData.path), { recursive: true });
const db = new DatabaseSync(workerData.path);
db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
 CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}', active_chat TEXT);
 CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL, created_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, text TEXT NOT NULL, error TEXT, preferences TEXT, sent_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS message_recommendations (message_id TEXT NOT NULL REFERENCES messages(id), recommendation_id TEXT NOT NULL, PRIMARY KEY(message_id,recommendation_id));
 CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(user_id);
 CREATE INDEX IF NOT EXISTS message_chat ON messages(conversation_id);`);
const digest = (value) => createHash('sha256').update(value).digest('hex');
const fail = (status, message) => {
  throw Object.assign(new Error(message), { status });
};
function newChat(userId) {
  const id = randomUUID();
  db.prepare('INSERT INTO conversations VALUES (?, ?, ?, ?)').run(
    id,
    userId,
    'Yeni sohbet',
    new Date().toISOString(),
  );
  db.prepare('UPDATE users SET active_chat=? WHERE id=?').run(id, userId);
  return id;
}
function active(userId) {
  return db.prepare('SELECT active_chat FROM users WHERE id=?').get(userId).active_chat;
}
const operations = {
  close() {
    db.close();
    return { ok: true };
  },
  ping() {
    return { status: 'ok' };
  },
  register({ username, password }) {
    if (
      typeof username !== 'string' ||
      !/^[a-zA-Z0-9_]{3,32}$/.test(username) ||
      typeof password !== 'string' ||
      password.length < 8 ||
      password.length > 128
    )
      fail(400, 'Kullanıcı adı 3–32 harf, rakam veya alt çizgi; parola 8–128 karakter olmalı.');
    const salt = randomBytes(16).toString('hex');
    const hash = scryptSync(password, salt, 64).toString('hex');
    const id = randomUUID();
    db.exec('BEGIN IMMEDIATE');
    try {
      if (db.prepare('SELECT id FROM users WHERE username=?').get(username.toLowerCase()))
        fail(409, 'Bu kullanıcı adı kullanılıyor.');
      db.prepare('INSERT INTO users (id,username,password) VALUES (?,?,?)').run(
        id,
        username.toLowerCase(),
        `${salt}:${hash}`,
      );
      newChat(id);
      db.exec('COMMIT');
    } catch (e) {
      db.exec('ROLLBACK');
      throw e;
    }
    return { id, username: username.toLowerCase() };
  },
  login({ username, password }) {
    if (typeof username !== 'string' || typeof password !== 'string' || password.length > 128)
      fail(401, 'Kullanıcı adı veya parola doğru değil.');
    const user = db.prepare('SELECT * FROM users WHERE username=?').get(username.toLowerCase());
    const [salt, hash] = (user?.password || `${'0'.repeat(32)}:${'0'.repeat(128)}`).split(':');
    const matches = timingSafeEqual(scryptSync(password, salt, 64), Buffer.from(hash, 'hex'));
    if (!user || !matches) fail(401, 'Kullanıcı adı veya parola doğru değil.');
    return { id: user.id, username: user.username };
  },
  session({ token }) {
    return (
      db
        .prepare(
          'SELECT u.id,u.username FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',
        )
        .get(digest(token), Date.now()) || null
    );
  },
  issue({ userId }) {
    const token = randomBytes(32).toString('hex');
    db.prepare('DELETE FROM sessions WHERE expires<=?').run(Date.now());
    db.prepare('INSERT INTO sessions VALUES (?,?,?)').run(
      digest(token),
      userId,
      Date.now() + 43200000,
    );
    return token;
  },
  logout({ token }) {
    db.prepare('DELETE FROM sessions WHERE token=?').run(digest(token));
    return { ok: true };
  },
  settings({ userId }) {
    return JSON.parse(db.prepare('SELECT settings FROM users WHERE id=?').get(userId).settings);
  },
  saveSettings({ userId, value }) {
    db.prepare('UPDATE users SET settings=? WHERE id=?').run(JSON.stringify(value), userId);
    return value;
  },
  history({ userId }) {
    const conversationId = active(userId);
    const messages = db
      .prepare('SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid')
      .all(conversationId)
      .map((m) => ({
        id: m.id,
        role: m.role,
        text: m.text,
        error: m.error,
        preferences: m.preferences ? JSON.parse(m.preferences) : undefined,
        sentAt: m.sent_at,
        recommendationIds: db
          .prepare('SELECT recommendation_id FROM message_recommendations WHERE message_id=?')
          .all(m.id)
          .map((r) => r.recommendation_id),
      }));
    return { conversationId, messages };
  },
  conversations({ userId }) {
    return db
      .prepare('SELECT id,title,created_at FROM conversations WHERE user_id=? ORDER BY rowid DESC')
      .all(userId);
  },
  selectChat({ userId, id }) {
    if (!db.prepare('SELECT id FROM conversations WHERE id=? AND user_id=?').get(id, userId))
      fail(404, 'Sohbet bulunamadı.');
    db.prepare('UPDATE users SET active_chat=? WHERE id=?').run(id, userId);
    return { ok: true };
  },
  newChat({ userId }) {
    db.exec('BEGIN IMMEDIATE');
    try {
      const conversationId = newChat(userId);
      db.exec('COMMIT');
      return { conversationId };
    } catch (error) {
      db.exec('ROLLBACK');
      throw error;
    }
  },
  startMessage({ userId, text, preferences }) {
    const conversationId = active(userId),
      id = randomUUID();
    db.exec('BEGIN IMMEDIATE');
    try {
      db.prepare(
        'INSERT INTO messages (id,conversation_id,role,text,preferences,sent_at) VALUES (?,?,?,?,?,?)',
      ).run(
        id,
        conversationId,
        'user',
        text,
        JSON.stringify(preferences),
        new Date().toISOString(),
      );
      db.prepare("UPDATE conversations SET title=? WHERE id=? AND title='Yeni sohbet'").run(
        text.slice(0, 60),
        conversationId,
      );
      db.exec('COMMIT');
    } catch (e) {
      db.exec('ROLLBACK');
      throw e;
    }
    return { conversationId };
  },
  finishMessage({ conversationId, text, error, recommendationIds = [] }) {
    const id = randomUUID();
    db.exec('BEGIN IMMEDIATE');
    try {
      db.prepare(
        'INSERT INTO messages (id,conversation_id,role,text,error,sent_at) VALUES (?,?,?,?,?,?)',
      ).run(id, conversationId, 'assistant', text, error || null, new Date().toISOString());
      for (const recommendationId of new Set(recommendationIds)) {
        db.prepare('INSERT INTO message_recommendations VALUES (?,?)').run(id, recommendationId);
      }
      db.exec('COMMIT');
    } catch (error) {
      db.exec('ROLLBACK');
      throw error;
    }
    return { ok: true };
  },
};
parentPort.on('message', ({ id, operation, args }) => {
  try {
    parentPort.postMessage({ id, result: operations[operation](args) });
    if (operation === 'close') parentPort.close();
  } catch (error) {
    parentPort.postMessage({
      id,
      error: {
        message: error.status ? error.message : 'Kayıt işlemi tamamlanamadı.',
        status: error.status || 500,
      },
    });
  }
});
