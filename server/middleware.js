'use strict';

const { hashPin, getAdminPinHash, getAdminPinSalt } = require('./auth');

async function adminAuth(req, res, next) {
  const pin       = req.headers['x-admin-pin'] || req.body?.admin_pin;
  const adminHash = getAdminPinHash();
  const adminSalt = getAdminPinSalt();

  if (!adminHash) {
    if (req.path === '/admin/setup') return next();
    return res.status(403).json({ ok: false, reason: '管理員 PIN 尚未設定' });
  }
  if (!pin) return res.status(401).json({ ok: false, reason: '缺少管理員 PIN' });

  try {
    const hash = await hashPin(pin, adminSalt);
    if (hash !== adminHash) return res.status(403).json({ ok: false, reason: '管理員 PIN 錯誤' });
    next();
  } catch {
    res.status(500).json({ ok: false, reason: '驗證錯誤' });
  }
}

module.exports = { adminAuth };
